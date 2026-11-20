<script lang="ts">
  import { onMount } from "svelte";
  import { api, onReachability, type Photo } from "./lib/api";
  import { startTiming, isDecoded } from "./lib/images";
  import { urls } from "./lib/api";
  import {
    FILTERS,
    REASONS,
    burstLine,
    burstOf,
    firstOpen,
    loadFolder,
    markOf,
    markText,
    matches,
    refreshFolder,
    say,
    scenesOf,
    setPanel,
    tilesOf,
    ui,
    type CullActions,
    type Filter,
    type Tile,
  } from "./lib/state.svelte";
  import Folders from "./components/Folders.svelte";
  import Grid from "./components/Grid.svelte";
  import Loupe from "./components/Loupe.svelte";
  import Compare from "./components/Compare.svelte";
  import Help from "./components/Help.svelte";
  import DoneDialog from "./components/DoneDialog.svelte";
  import Actions from "./components/Actions.svelte";
  import Mark from "./components/Mark.svelte";

  // Every present photo: one tile per single photo or whole burst, by scene in shooting order (ADR 0011).
  const allTiles = $derived(tilesOf(ui.photos.filter((p) => p.state === "present")));
  // What the filter shows. The selected tile stays while it is selected, so a photo just decided doesn't vanish
  // from under the cursor; once nothing matches, the filter shows nothing.
  const tiles = $derived.by(() => {
    const filter = ui.filter;
    if (filter === null) return allTiles;
    const shown = allTiles.filter((t) => matches(t, filter) || t.frames.some((p) => p.id === ui.currentId));
    return shown.some((t) => matches(t, filter)) ? shown : [];
  });
  const items = $derived(tiles.flatMap((t) => t.frames)); // every frame, for the loupe
  const tileIndex = $derived(tiles.findIndex((t) => t.frames.some((p) => p.id === ui.currentId)));
  const currentTile = $derived(tiles[tileIndex] ?? null);
  // The selected photo; in the grid and the loupe only while it is shown. Compare shows its burst whatever the filter.
  const current = $derived.by(() => {
    const photo = ui.photos.find((p) => p.id === ui.currentId) ?? null;
    return ui.mode === "compare" || currentTile ? photo : null;
  });
  const frames = $derived(current ? burstOf(current, ui.photos) : []);
  const isBurst = $derived(frames.length > 1);
  const counts = $derived(
    Object.fromEntries(FILTERS.map((f) => [f.id, allTiles.filter((t) => matches(t, f.id)).length])) as Record<Filter, number>,
  );
  const scenes = $derived(scenesOf(tiles));
  const sceneNumbers = $derived(new Map(scenesOf(allTiles).map((g, i) => [g.scene, i + 1])));
  const hasBursts = $derived(allTiles.some((t) => t.frames.length > 1));
  const emptyText = $derived(
    ui.filter === "open"
      ? "Nothing left to decide."
      : ui.filter === "app_reject"
        ? "The app rejects nothing on its own in this folder."
        : ui.filter === "app_keep"
          ? "The app keeps nothing on its own in this folder."
          : "No photos in this folder.",
  );

  const clamp = (i: number, n: number) => Math.min(Math.max(i, 0), n - 1);

  function select(id: number | null, timed = true) {
    if (id === null || id === ui.currentId) return;
    const photo = ui.photos.find((p) => p.id === id);
    if (timed && photo && ui.mode !== "grid") {
      startTiming(ui.zoom ? "zoom" : "flip", isDecoded(ui.zoom ? urls.full(photo) : urls.screen(photo)));
    }
    ui.currentId = id;
  }

  // Moving onto a tile selects its cover; staying on the same tile keeps the frame it shows.
  function selectTile(tile: Tile | undefined) {
    if (tile && tile !== currentTile) select(tile.photo.id);
  }

  // ← →: tiles in the grid, every frame in the loupe, the burst's frames in compare.
  function step(delta: number) {
    if (ui.mode === "compare") {
      if (!current) return;
      const i = frames.findIndex((p) => p.id === current.id);
      select(frames[clamp(i + delta, frames.length)]?.id ?? null);
    } else if (ui.mode === "grid") {
      selectTile(tiles[clamp(tileIndex + delta, tiles.length)]);
    } else {
      const i = items.findIndex((p) => p.id === ui.currentId);
      select(items[clamp(i + delta, items.length)]?.id ?? null);
    }
  }

  // ↑ ↓ in the loupe: the previous or next tile; in compare: the previous or next burst.
  function tileStep(delta: number, burstsOnly: boolean) {
    for (let i = tileIndex + delta; i >= 0 && i < tiles.length; i += delta) {
      if (!burstsOnly || tiles[i].frames.length > 1) return selectTile(tiles[i]);
    }
    if (burstsOnly) say(delta > 0 ? "no burst after this one" : "no burst before this one");
  }

  // ↑ ↓ in the grid: the nearest tile in the row above or below on screen, across scene breaks.
  function rowStep(delta: number) {
    const nodes = [...document.querySelectorAll<HTMLElement>(".grid [data-tile]")];
    const here = nodes.find((n) => n.dataset.tile === currentTile?.key);
    if (!here) return step(delta);
    const box = here.getBoundingClientRect();
    const middle = box.left + box.width / 2;
    const others = nodes
      .map((node) => ({ node, box: node.getBoundingClientRect() }))
      .filter((o) => (delta > 0 ? o.box.top >= box.bottom - 1 : o.box.bottom <= box.top + 1));
    if (!others.length) return;
    const tops = others.map((o) => o.box.top);
    const row = delta > 0 ? Math.min(...tops) : Math.max(...tops);
    const distance = (o: { box: DOMRect }) => Math.abs(o.box.left + o.box.width / 2 - middle);
    const nearest = others.filter((o) => Math.abs(o.box.top - row) < 2).reduce((a, b) => (distance(b) < distance(a) ? b : a));
    selectTile(tiles.find((t) => t.key === nearest.node.dataset.tile));
  }

  // PgUp PgDn: the first tile of the previous or next scene (PgUp in the first scene: its first tile).
  function sceneStep(delta: number) {
    const s = scenes.findIndex((g) => g.tiles.some((t) => t === currentTile));
    const target = scenes[s + delta] ?? (delta < 0 ? scenes[0] : undefined);
    if (target) selectTile(target.tiles[0]);
    else say("this is the last scene");
  }

  // N, Shift+N: the next or previous tile still to decide (in compare, burst), going round at the ends.
  function openStep(delta: number) {
    const burstsOnly = ui.mode === "compare";
    const n = tiles.length;
    for (let k = 1; k <= n; k += 1) {
      const i = (((tileIndex + delta * k) % n) + n) % n;
      if (i === tileIndex) break;
      const tile = tiles[i];
      if (tile.mark.verdict !== "open" || (burstsOnly && tile.frames.length < 2)) continue;
      if (tileIndex >= 0 && (delta > 0 ? i < tileIndex : i > tileIndex)) {
        say(delta > 0 ? "from the top: the first one still to decide" : "from the end: the last one still to decide");
      }
      return selectTile(tile);
    }
    say(burstsOnly ? "no other burst is left to decide" : "nothing else is left to decide");
  }

  // A filter keeps the selection when its tile matches, else it moves to the filter's first tile (ADR 0011).
  function setFilter(filter: Filter | null) {
    if (filter === ui.filter) return;
    const here = allTiles.find((t) => t.frames.some((p) => p.id === ui.currentId));
    ui.filter = filter;
    ui.zoom = false;
    if (filter === null) {
      if (!here) ui.currentId = firstOpen();
    } else if (!here || !matches(here, filter)) {
      ui.currentId = allTiles.find((t) => matches(t, filter))?.photo.id ?? ui.currentId;
    }
    if (ui.mode === "compare") ui.mode = "loupe";
  }

  function cycleFilter(delta: number) {
    const order: (Filter | null)[] = [null, ...FILTERS.map((f) => f.id)];
    const i = order.indexOf(ui.filter);
    setFilter(order[(i + delta + order.length) % order.length]);
  }

  function update(id: number, change: Partial<Photo>) {
    const photo = ui.photos.find((p) => p.id === id);
    if (photo) Object.assign(photo, change);
  }

  // Where the grid moves on to after P or X: the next tile.
  function nextTile(): number | null {
    return tiles[tileIndex + 1]?.photo.id ?? null;
  }

  async function decide(photo: Photo, decision: Photo["decision"], advance = true) {
    const before = photo.decision;
    // the next tile in the grid, the next photo in the loupe; compare stays
    const target = !advance
      ? null
      : ui.mode === "grid"
        ? nextTile()
        : ui.mode === "loupe"
          ? (items[items.findIndex((p) => p.id === photo.id) + 1]?.id ?? null)
          : null;
    update(photo.id, { decision });
    const overrules = decision !== null && photo.suggestion !== null && decision !== photo.suggestion;
    if (target !== null) select(target);
    try {
      await api.decide(photo.id, decision);
      ui.reasonFor = overrules ? photo.id : null;
      if (overrules) setTimeout(() => ui.reasonFor === photo.id && (ui.reasonFor = null), 5000);
    } catch (error) {
      update(photo.id, { decision: before });
      say(`not saved: ${(error as Error).message}`);
    }
  }

  // P on a frame of a burst keeps it and rejects the burst's other frames (moving an earlier keep); Shift+P keeps it too.
  async function pick(photo: Photo, keepOthers: boolean, advance = false) {
    const target = advance ? nextTile() : null;
    if (target !== null) select(target);
    try {
      const result = await api.pick(photo.id, keepOthers);
      for (const id of result.changed) update(id, { decision: id === photo.id ? "keep" : "reject" });
      say(keepOthers ? `kept #${photo.burst.position} too` : `picked #${photo.burst.position}; the burst's other frames are rejected`);
    } catch (error) {
      say(`not saved: ${(error as Error).message}`);
    }
  }

  // X or U on a burst tile in the grid: reject every frame, or clear every frame.
  async function decideBurst(photo: Photo, decision: "reject" | null, advance: boolean) {
    const target = advance ? nextTile() : null;
    if (target !== null) select(target);
    try {
      const result = await api.decideBurst(photo.id, decision);
      for (const id of result.changed) update(id, { decision });
      say(decision ? "every frame of the burst is rejected" : "your decisions on the burst are cleared");
    } catch (error) {
      say(`not saved: ${(error as Error).message}`);
    }
  }

  async function giveReason(reason: string) {
    const id = ui.reasonFor;
    ui.reasonFor = null;
    if (id === null) return;
    try {
      await api.reason(id, reason);
      update(id, { owner_reason: reason });
    } catch (error) {
      say(`not saved: ${(error as Error).message}`);
    }
  }

  function enterCompare() {
    ui.compareFrom = ui.mode === "loupe" ? "loupe" : "grid";
    ui.zoom = false;
    ui.mode = "compare";
  }

  function toggleZoom(photo: Photo) {
    if (ui.mode === "grid" || ui.mode === "compare") ui.mode = "loupe";
    if (!ui.zoom) startTiming("zoom", isDecoded(urls.full(photo)));
    ui.zoom = !ui.zoom;
  }

  // Everything the culling page can do; the keys below and the action panel both call these.
  const act: CullActions = {
    keep: () => {
      if (!current) return;
      if (ui.mode === "compare") pick(current, false);
      else if (isBurst) pick(current, false, true);
      else decide(current, "keep");
    },
    keepToo: () => {
      if (!current) return;
      if (isBurst) pick(current, true);
      else act.keep();
    },
    reject: () => {
      if (!current) return;
      if (ui.mode === "grid" && isBurst) decideBurst(current, "reject", true);
      else decide(current, "reject", ui.mode !== "compare");
    },
    clear: () => {
      if (!current) return;
      if (ui.mode === "grid" && isBurst) decideBurst(current, null, false);
      else decide(current, null, false);
    },
    previous: () => step(-1),
    next: () => step(1),
    up: () => (ui.mode === "grid" ? rowStep(-1) : tileStep(-1, ui.mode === "compare")),
    down: () => (ui.mode === "grid" ? rowStep(1) : tileStep(1, ui.mode === "compare")),
    nextOpen: () => openStep(1),
    previousOpen: () => openStep(-1),
    nextScene: () => {
      if (ui.mode !== "compare") sceneStep(1);
    },
    previousScene: () => {
      if (ui.mode !== "compare") sceneStep(-1);
    },
    nextFilter: () => cycleFilter(1),
    previousFilter: () => cycleFilter(-1),
    clearFilter: () => setFilter(null),
    open: () => {
      if (ui.mode !== "grid") ui.mode = "grid";
      else if (current && isBurst) enterCompare();
      else if (current) ui.mode = "loupe";
      ui.zoom = false;
    },
    zoom: () => {
      if (current) toggleZoom(current);
    },
    focusMap: () => {
      if (!current) return;
      if (ui.mode === "loupe") ui.heat = !ui.heat;
      else {
        ui.mode = "loupe";
        ui.heat = true;
      }
    },
    compare: () => {
      if (!current) return;
      if (ui.mode === "compare") ui.mode = ui.compareFrom;
      else if (isBurst) enterCompare();
      else say("a single photo: nothing to compare");
    },
    acceptPick: () => {
      if (!current) return;
      const best = frames.find((p) => p.burst.rank === 1);
      if (frames.length > 1 && best) pick(best, false);
      else say("not a burst: nothing to accept");
    },
    back: () => {
      if (ui.help) ui.help = false;
      else if (ui.zoom) ui.zoom = false;
      else if (ui.mode === "compare") ui.mode = ui.compareFrom;
      else if (ui.mode === "loupe" && tiles.length) ui.mode = "grid";
      else {
        ui.mode = "grid";
        setFilter(null);
      }
    },
    help: () => (ui.help = !ui.help),
    timings: () => (ui.debug = !ui.debug),
  };

  function onKey(event: KeyboardEvent) {
    if (ui.view !== "cull" || ui.done) return;
    const target = event.target as HTMLElement;
    if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const key = event.key;
    if (ui.reasonFor !== null && /^[1-5]$/.test(key)) {
      event.preventDefault();
      giveReason(REASONS[Number(key) - 1]);
      return;
    }
    if (ui.reasonFor !== null && key !== "Shift") ui.reasonFor = null;
    const keys: Record<string, () => void> = {
      ArrowRight: act.next,
      ArrowLeft: act.previous,
      ArrowDown: act.down,
      ArrowUp: act.up,
      PageDown: act.nextScene,
      PageUp: act.previousScene,
      Enter: act.open,
      Escape: act.back,
      Tab: event.shiftKey ? act.previousFilter : act.nextFilter,
      "?": act.help,
      p: event.shiftKey ? act.keepToo : act.keep,
      n: event.shiftKey ? act.previousOpen : act.nextOpen,
      x: act.reject,
      u: act.clear,
      z: act.zoom,
      f: act.focusMap,
      c: act.compare,
      a: act.acceptPick,
      d: act.timings,
    };
    const action = keys[key] ?? keys[key.toLowerCase()];
    if (action) {
      event.preventDefault();
      action();
    }
  }

  onReachability((ok) => (ui.offline = !ok));

  onMount(() => {
    api.status().then((s) => (ui.status = s)).catch(() => undefined);
    const timer = setInterval(() => api.status().then((s) => (ui.status = s)).catch(() => undefined), 15000);
    const events = new EventSource("/api/events");
    let finished = new Set<number>();
    events.onerror = () => (ui.offline = true); // the browser keeps retrying on its own
    events.onopen = () => (ui.offline = false);
    events.onmessage = (message) => {
      const data = JSON.parse(message.data);
      ui.jobs = data.jobs;
      for (const job of ui.jobs) {
        if (job.state === "done" && !finished.has(job.id)) {
          finished.add(job.id);
          if (job.folder_id !== null && job.folder_id === ui.folderId) {
            refreshFolder();
            say("analysis done: the app's calls now use focus, faces and bursts");
          }
        }
      }
    };
    const fromHash = () => {
      const folder = Number(new URLSearchParams(location.hash.slice(1)).get("folder"));
      if (folder && folder !== ui.folderId) loadFolder(folder).catch(() => (ui.view = "folders"));
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    return () => {
      clearInterval(timer);
      events.close();
      finished = new Set();
      window.removeEventListener("hashchange", fromHash);
    };
  });

  $effect(() => {
    if (ui.view === "cull" && ui.folderId !== null) history.replaceState(null, "", `#folder=${ui.folderId}`);
    else history.replaceState(null, "", "#");
  });

  $effect(() => {
    document.title =
      ui.view === "cull" && ui.folder ? `Culling · ${ui.folder.name} · Raw Image Editor` : "Folders · Raw Image Editor";
  });

  const running = $derived(ui.jobs.filter((j) => j.state === "running" || j.state === "queued"));
  let tick = $state(0);
  $effect(() => {
    if (!ui.debug) return;
    const timer = setInterval(() => (tick += 1), 500);
    return () => clearInterval(timer);
  });
  const timingText = $derived.by(() => {
    void tick;
    const t = (window.__rie?.timings ?? []).slice(-20);
    if (!ui.debug || t.length === 0) return "";
    const flips = t.filter((x) => x.kind === "flip").map((x) => x.ms);
    const zooms = t.filter((x) => x.kind === "zoom").map((x) => x.ms);
    const median = (a: number[]) => (a.length ? [...a].sort((x, y) => x - y)[Math.floor(a.length / 2)] : 0);
    return `flip median ${median(flips)} ms (${flips.length}) · zoom median ${median(zooms)} ms (${zooms.length})`;
  });
</script>

<svelte:window onkeydown={onKey} />

<div class="app">
  <header>
    <button class="home" onclick={() => (ui.view = "folders")} title="Back to the folders">Raw Image Editor</button>
    <span class="crumb" aria-hidden="true">›</span>
    <h1>{ui.view === "cull" ? "Culling" : "Folders"}</h1>
    {#if ui.view === "cull" && ui.folder}
      <span class="folder" title={ui.folder.path}>{ui.folder.name}</span>
      <nav aria-label="Filters">
        {#each FILTERS as filter (filter.id)}
          {@const on = ui.filter === filter.id}
          <button
            class="chip"
            class:active={on}
            aria-pressed={on}
            title={on ? "Show every photo again (Esc in the grid)" : `${filter.title} (Tab cycles through the filters)`}
            onclick={(event) => {
              (event.currentTarget as HTMLElement).blur();
              setFilter(on ? null : filter.id);
            }}
            >{filter.label} <span class="count">{counts[filter.id]}</span>{#if on}<span class="x" aria-hidden="true">×</span>{/if}</button
          >
        {/each}
      </nav>
      <span class="spacer"></span>
      {#if ui.folder.culled_at}<span class="badge keep">culled</span>{/if}
      {#if !ui.panel}<button onclick={() => setPanel(true)} title="Show the action panel">Actions</button>{/if}
      <button class="primary" onclick={() => (ui.done = true)} disabled={!ui.folder.available}>Done culling</button>
    {:else}
      <span class="spacer"></span>
    {/if}
    {#each running as job}
      <span class="job" title={job.path}>{job.step || "queued"} {job.total ? `${job.done}/${job.total}` : ""}</span>
    {/each}
    {#if ui.status}
      <span class="drive" class:bad={ui.status.drive.configured && ui.status.drive.state !== "connected"} title={ui.status.drive.detail}>
        drive: {ui.status.drive.configured ? ui.status.drive.state : "not configured"}
      </span>
    {/if}
    <button onclick={() => (ui.help = !ui.help)} title="Guide and keys (?)">?</button>
  </header>

  {#if ui.offline}
    <div class="offline">
      Can't reach the Raw Image Editor server. Start it again in a terminal with <code>uv run rie serve</code> and keep
      it running; this page reconnects by itself.
    </div>
  {/if}

  <div class="body">
    <main>
      {#if ui.view === "folders"}
        <Folders />
      {:else if ui.mode === "compare" && current}
        <Compare photo={current} onselect={(id: number) => select(id)} onpick={(p: Photo, keep: boolean) => pick(p, keep)} />
      {:else if tiles.length === 0}
        <p class="empty muted">
          {emptyText}
          {#if ui.filter !== null}<kbd>Esc</kbd> shows every photo.{/if}
        </p>
      {:else if ui.mode === "grid"}
        <Grid
          {tiles}
          numbers={sceneNumbers}
          onselect={(id: number) => select(id, false)}
          onopen={(tile: Tile, id: number) => {
            select(id, false);
            if (tile.frames.length > 1) enterCompare();
            else ui.mode = "loupe";
          }}
        />
      {:else if current}
        <Loupe photo={current} {items} />
      {/if}
    </main>
    {#if ui.view === "cull" && ui.panel}
      <Actions photo={current} {act} onhide={() => setPanel(false)} />
    {/if}
  </div>

  {#if current && ui.view === "cull"}
    {@const mark = markOf(current)}
    <footer>
      <div class="row">
        <span class="name">{current.name}{current.jpeg ? ` + ${current.jpeg.split(".").pop()}` : ""}</span>
        <span class="verdict"><Mark {mark} size={15} /> {markText(mark)}</span>
        {#if current.suggestion}
          <span class="suggestion">
            app: <b class={current.suggestion}>{current.suggestion}</b>
            <span class="muted">({current.kind}{current.p !== null ? `, ${Math.round(current.p * 100)}% reject` : ""})</span>
            {current.reason}
          </span>
        {/if}
        <span class="spacer"></span>
        {#if current.burst.size > 1}<span class="muted">burst #{current.burst.position}/{current.burst.size}, rank {current.burst.rank}</span>{/if}
        <span class="muted">{current.settings}</span>
        {#if current.edits || current.unmodeled}<span class="badge" title="edits from RawTherapee, kept">RT edits</span>{/if}
      </div>
      {#if hasBursts}
        <!-- a second line in folders with bursts, kept for single photos too so the view doesn't jump while flipping -->
        <div class="row burst-line muted" title={isBurst ? burstLine(frames) : undefined}>{isBurst ? `Burst: ${burstLine(frames)}` : "\u00a0"}</div>
      {/if}
    </footer>
  {/if}

  {#if ui.reasonFor !== null}
    <div class="reason">
      Why? {#each REASONS as reason, i}<button onclick={() => giveReason(reason)}><kbd>{i + 1}</kbd> {reason}</button>{/each}
      <span class="muted">optional; any other key skips</span>
    </div>
  {/if}
  {#if ui.message}<div class="toast">{ui.message}</div>{/if}
  {#if timingText}<div class="debug">{timingText}</div>{/if}
  {#if ui.help}<Help onclose={() => (ui.help = false)} />{/if}
  {#if ui.done && ui.folderId !== null}<DoneDialog folderId={ui.folderId} onclose={() => (ui.done = false)} />{/if}
</div>

<style>
  .app { display: flex; flex-direction: column; height: 100%; }
  header, .offline, footer { flex: none; } /* never squeezed: a wrapped top bar stays clickable */
  .offline { background: var(--reject); color: #1d0505; padding: 6px 12px; font-weight: 600; }
  .offline code { background: #0002; padding: 0 4px; border-radius: 3px; }
  header {
    display: flex; align-items: center; flex-wrap: wrap; gap: 4px 10px; padding: 6px 10px;
    border-bottom: 1px solid var(--line); background: var(--panel); min-height: 42px;
  }
  .home { background: none; border: none; padding: 0; font-weight: 700; color: var(--text); }
  .home:hover { color: var(--accent); }
  .crumb { color: var(--muted); margin: 0 -4px; }
  h1 { font-size: 14px; font-weight: 600; margin: 0; }
  .folder { font-weight: 600; max-width: 28ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  nav { display: flex; gap: 4px; flex-wrap: wrap; }
  .chip { border-radius: 14px; padding: 3px 11px; }
  .chip.active { border-color: var(--accent); background: #24324a; }
  .count { color: var(--muted); margin-left: 4px; }
  .x { margin-left: 6px; color: var(--text); }
  .spacer { flex: 1; }
  .job { color: var(--warn); font-size: 12px; }
  .drive { font-size: 12px; color: var(--muted); }
  .drive.bad { color: var(--reject); }
  .body { display: flex; flex: 1 1 auto; min-height: 0; }
  main { position: relative; overflow: hidden; min-width: 0; flex: 1 1 auto; }
  footer {
    display: flex; flex-direction: column; justify-content: center; gap: 2px; padding: 4px 10px;
    border-top: 1px solid var(--line); background: var(--panel); font-size: 13px; min-height: 32px;
  }
  .row { display: flex; align-items: center; gap: 10px; white-space: nowrap; overflow: hidden; min-width: 0; }
  .burst-line { display: block; text-overflow: ellipsis; font-size: 12px; }
  .name { font-weight: 600; }
  .verdict { display: inline-flex; align-items: center; gap: 5px; }
  .suggestion { overflow: hidden; text-overflow: ellipsis; }
  .suggestion b.keep { color: var(--keep); }
  .suggestion b.reject { color: var(--reject); }
  .empty { padding: 40px; text-align: center; }
  .reason {
    position: fixed; bottom: 48px; left: 50%; transform: translateX(-50%); background: var(--panel);
    border: 1px solid var(--accent); border-radius: 8px; padding: 8px 12px; display: flex; gap: 6px; align-items: center;
  }
  .toast {
    position: fixed; top: 52px; left: 50%; transform: translateX(-50%); background: #2a2f3a; border: 1px solid var(--line);
    border-radius: 6px; padding: 6px 12px;
  }
  .debug { position: fixed; bottom: 40px; right: 10px; font: 12px ui-monospace, monospace; background: #000a; padding: 4px 8px; }
</style>
