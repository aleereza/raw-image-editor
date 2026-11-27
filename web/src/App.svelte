<script lang="ts">
  import { onMount } from "svelte";
  import { api, onReachability, type Photo } from "./lib/api";
  import { startTiming, isDecoded, load } from "./lib/images";
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

  // The shown tile a photo belongs to.
  const tileOf = (id: number | null) => tiles.find((t) => t.frames.some((p) => p.id === id));

  // Only the loupe times flips and zooms: it stops the clock once the photo is on screen.
  function select(id: number | null, timed = true) {
    if (id === null || id === ui.currentId) return;
    const photo = ui.photos.find((p) => p.id === id);
    if (timed && photo && ui.mode === "loupe") {
      startTiming(ui.zoom ? "zoom" : "flip", isDecoded(ui.zoom ? urls.full(photo) : urls.screen(photo)));
    }
    ui.currentId = id;
  }

  // The loupe and burst compare are one walk in shooting order: a burst shows in compare, a single photo in the loupe.
  function showTile(tile: Tile) {
    if (tile.frames.length > 1) enterCompare();
    else ui.mode = "loupe";
  }

  // Selects a photo. In the loupe and compare, a move onto another tile shows it as the walk does; within a tile the
  // view stays, so a burst's frames opened in the loupe (C, M or Z in compare) go on in the loupe. The grid stays.
  function moveTo(id: number | null) {
    if (id === null) return;
    const tile = tileOf(id);
    if (ui.mode !== "grid" && tile && tile.key !== currentTile?.key) showTile(tile);
    select(id);
  }

  // Moving onto a tile selects its cover; staying on the same tile keeps the frame it shows.
  function selectTile(tile: Tile | undefined) {
    if (tile && tile.key !== currentTile?.key) moveTo(tile.photo.id);
  }

  // Where ← (-1) or → (1) goes from a photo in the loupe or compare: the next frame of its burst, else the next tile,
  // onto a burst's first frame going forward and its last going back.
  function walk(photo: Photo | null, delta: number): number | null {
    const burst = photo ? burstOf(photo, ui.photos) : [];
    const i = burst.findIndex((p) => p.id === photo?.id);
    if (i >= 0 && burst[i + delta]) return burst[i + delta].id;
    const at = tiles.findIndex((t) => t.frames.some((p) => p.id === photo?.id));
    const tile = at < 0 ? tiles[0] : tiles[at + delta];
    if (!tile) return null;
    return (at >= 0 && delta < 0 ? tile.frames[tile.frames.length - 1] : tile.frames[0]).id;
  }

  // ← →: tiles in the grid; in the loupe and compare, the walk.
  function step(delta: number) {
    if (ui.mode === "grid") selectTile(tiles[clamp(tileIndex + delta, tiles.length)]);
    else moveTo(walk(current, delta));
  }

  // ↑ ↓ in the loupe and compare: the previous or next photo or burst.
  function tileStep(delta: number) {
    selectTile(tiles[tileIndex + delta]);
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

  // N, Shift+N: the next or previous tile still to decide, going round at the ends.
  function openStep(delta: number) {
    const n = tiles.length;
    for (let k = 1; k <= n; k += 1) {
      const i = (((tileIndex + delta * k) % n) + n) % n;
      if (i === tileIndex) break;
      const tile = tiles[i];
      if (tile.mark.verdict !== "open") continue;
      if (tileIndex >= 0 && (delta > 0 ? i < tileIndex : i > tileIndex)) {
        say(delta > 0 ? "from the top: the first one still to decide" : "from the end: the last one still to decide");
      }
      return selectTile(tile);
    }
    say("nothing else is left to decide");
  }

  // A filter keeps the selection when its tile matches, else it moves to the filter's first tile (ADR 0011). In the
  // loupe and compare, the selected tile then shows as the walk shows it; when the filter shows nothing, the loupe
  // says so.
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
    if (ui.mode !== "grid") {
      const tile = tileOf(ui.currentId);
      if (tile) showTile(tile);
      else ui.mode = "loupe";
    }
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

  // Where the grid moves on to after G or F: the next tile.
  function nextTile(): number | null {
    return tiles[tileIndex + 1]?.photo.id ?? null;
  }

  // Where a decision moves on to: the next tile in the grid; in the loupe, where → goes (the next frame of a burst,
  // else the next photo or burst, which shows as the walk shows it); compare stays on the frame.
  function nextAfter(photo: Photo): number | null {
    if (ui.mode === "grid") return nextTile();
    if (ui.mode === "loupe") return walk(photo, 1);
    return null;
  }

  // A decision against the app's call asks why, in a bar at the bottom. The photo stays on screen until the question
  // is answered or skipped, so the answer can't seem to be about the next photo; then the view moves on as the
  // decision would have. Any other key, or selecting another photo, drops the question.
  let moveOn = false; // answering the question moves on to the next photo

  function dropQuestion() {
    ui.reasonFor = null;
    moveOn = false;
  }

  async function decide(photo: Photo, decision: Photo["decision"], advance = true) {
    const before = photo.decision;
    const overrules = decision !== null && photo.suggestion !== null && decision !== photo.suggestion;
    const target = advance && !overrules ? nextAfter(photo) : null;
    update(photo.id, { decision });
    ui.reasonFor = overrules ? photo.id : null;
    moveOn = overrules && advance;
    moveTo(target);
    try {
      await api.decide(photo.id, decision);
    } catch (error) {
      update(photo.id, { decision: before });
      if (ui.reasonFor === photo.id) dropQuestion();
      say(`not saved: ${(error as Error).message}`);
    }
  }

  // G on a frame of a burst keeps it and rejects the burst's open frames. Frames already kept stay kept, so another G
  // adds a keep. The grid then moves to the next tile and the loupe to the next frame; compare stays on the frame.
  async function pick(photo: Photo, advance = false) {
    moveTo(advance ? nextAfter(photo) : null);
    try {
      const result = await api.pick(photo.id);
      for (const id of result.changed) update(id, { decision: id === photo.id ? "keep" : "reject" });
      const rejected = result.changed.length - 1;
      const open = rejected === 1 ? "the open frame" : `the ${rejected} open frames`;
      say(`kept #${photo.burst.position}` + (rejected ? ` and rejected ${open}` : ""));
    } catch (error) {
      say(`not saved: ${(error as Error).message}`);
    }
  }

  // F or U on a burst tile in the grid: reject every frame, or clear every frame.
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

  // A reason for the photo the question is about, or null for Skip; either moves on as the decision would have.
  async function answer(reason: string | null) {
    const id = ui.reasonFor;
    const advance = moveOn;
    dropQuestion();
    const photo = ui.photos.find((p) => p.id === id);
    if (!photo) return;
    if (advance && ui.currentId === photo.id) moveTo(nextAfter(photo));
    if (reason === null) return;
    try {
      await api.reason(photo.id, reason);
      update(photo.id, { owner_reason: reason });
    } catch (error) {
      say(`not saved: ${(error as Error).message}`);
    }
  }

  // The question is about the photo on screen: it goes when another photo is selected.
  $effect(() => {
    if (ui.reasonFor !== null && ui.currentId !== ui.reasonFor) dropQuestion();
  });

  // Compare decodes the photos on either side of its burst, as the loupe does its neighbours, so the walk leaves the
  // burst as quickly as the loupe flips.
  $effect(() => {
    if (ui.mode !== "compare" || !currentTile) return;
    const first = items.indexOf(currentTile.frames[0]);
    if (first < 0) return;
    for (const photo of [items[first - 1], items[first + currentTile.frames.length]]) if (photo) load(urls.screen(photo));
  });

  function enterCompare() {
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
      if (isBurst) pick(current, ui.mode !== "compare");
      else decide(current, "keep");
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
    up: () => (ui.mode === "grid" ? rowStep(-1) : tileStep(-1)),
    down: () => (ui.mode === "grid" ? rowStep(1) : tileStep(1)),
    nextOpen: () => openStep(1),
    previousOpen: () => openStep(-1),
    nextScene: () => sceneStep(1),
    previousScene: () => sceneStep(-1),
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
      if (ui.mode === "compare") ui.mode = "loupe"; // the selected frame
      else if (isBurst) enterCompare();
      else say("a single photo: nothing to compare");
    },
    acceptPick: () => {
      if (!current) return;
      const best = frames.find((p) => p.burst.rank === 1);
      if (frames.length > 1 && best) pick(best);
      else say("not a burst: nothing to accept");
    },
    back: () => {
      if (ui.help) ui.help = false;
      else if (ui.zoom) ui.zoom = false;
      else if (ui.mode !== "grid" && tiles.length) ui.mode = "grid";
      else {
        ui.mode = "grid";
        setFilter(null);
      }
    },
    help: () => (ui.help = !ui.help),
    timings: () => (ui.debug = !ui.debug),
  };

  // The mouse's back button, the browser's Back and Alt+← step back like Esc, and from the grid with every photo
  // shown, back to the folders. Firefox doesn't pass the mouse's side buttons to pages, but every browser goes back in
  // its history, so the culling page keeps an entry of its own above the folders page's: going back from it is one
  // step back, and the entry is put back while there is more to step back from.
  const CULL_ENTRY = "raw-image-editor.cull";
  let guarded = false; // the culling page's entry is in the history, above the folders page's
  let ownBack = false; // the app itself is going back to the folders page's entry

  function stepBack() {
    dropQuestion(); // like Esc, Back skips the question
    if (ui.done) return; // the Done culling dialog closes with its own buttons
    if (ui.help || ui.zoom || ui.mode !== "grid" || ui.filter !== null) act.back();
    else ui.view = "folders";
  }

  function onPopState() {
    if (ownBack) {
      ownBack = false;
      history.replaceState(history.state, "", "#");
    } else if (guarded) {
      guarded = false;
      stepBack();
      if (ui.view === "cull") {
        history.pushState(CULL_ENTRY, "", `#folder=${ui.folderId}`);
        guarded = true;
      }
    }
    // else Forward, or an entry from before the page loaded: the address decides, through hashchange
  }

  function onKey(event: KeyboardEvent) {
    if (ui.view !== "cull" || ui.done) return;
    const target = event.target as HTMLElement;
    if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const key = event.key;
    if (ui.reasonFor !== null && /^[1-5]$/.test(key)) {
      event.preventDefault();
      answer(REASONS[Number(key) - 1]);
      return;
    }
    if (ui.reasonFor !== null && key !== "Shift") dropQuestion(); // any other key skips the question, then does its job
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
      g: act.keep,
      n: event.shiftKey ? act.previousOpen : act.nextOpen,
      f: act.reject,
      u: act.clear,
      z: act.zoom,
      m: act.focusMap,
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
      // another folder, or this one again after going back to the folders page (Forward)
      if (folder && (folder !== ui.folderId || ui.view !== "cull")) loadFolder(folder).catch(() => (ui.view = "folders"));
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

  // The address names the open folder, so a reload comes back to it. The culling page's own history entry is
  // explained above onKey.
  $effect(() => {
    const cull = ui.view === "cull" && ui.folderId !== null;
    const url = cull ? `#folder=${ui.folderId}` : "#";
    if (cull && !guarded) {
      guarded = true;
      // back on the culling page's own entry after a reload or Forward: no second one
      if (history.state === CULL_ENTRY) history.replaceState(CULL_ENTRY, "", url);
      else history.pushState(CULL_ENTRY, "", url);
    } else if (!cull && guarded) {
      guarded = false;
      ownBack = true;
      history.back(); // to the folders page's entry; onPopState gives it the folders page's address
    } else history.replaceState(history.state, "", url);
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

<svelte:window onkeydown={onKey} onpopstate={onPopState} />

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
        <Compare photo={current} onselect={(id: number) => select(id)} onpick={(p: Photo) => pick(p)} />
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
    {@const asked = ui.photos.find((p) => p.id === ui.reasonFor)}
    <div class="reason" role="group" aria-label="Why you went against the app">
      <span>Why {asked?.decision === "keep" ? "keep" : "reject"} <b>{asked?.name}</b>?</span>
      {#each REASONS as reason, i}<button onclick={() => answer(reason)}><kbd>{i + 1}</kbd> {reason}</button>{/each}
      <button onclick={() => answer(null)} title="Move on without a reason">Skip</button>
      <span class="muted">optional; any other key skips it</span>
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
    flex-wrap: wrap; justify-content: center; width: max-content; max-width: calc(100vw - 32px);
  }
  .toast {
    position: fixed; top: 52px; left: 50%; transform: translateX(-50%); background: #2a2f3a; border: 1px solid var(--line);
    border-radius: 6px; padding: 6px 12px;
  }
  .debug { position: fixed; bottom: 40px; right: 10px; font: 12px ui-monospace, monospace; background: #000a; padding: 4px 8px; }
</style>
