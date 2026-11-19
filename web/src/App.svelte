<script lang="ts">
  import { onMount } from "svelte";
  import { api, onReachability, type Photo } from "./lib/api";
  import { startTiming, isDecoded } from "./lib/images";
  import { urls } from "./lib/api";
  import { PILES, REASONS, burstOf, loadFolder, refreshFolder, say, tilesOf, ui } from "./lib/state.svelte";
  import Folders from "./components/Folders.svelte";
  import Grid from "./components/Grid.svelte";
  import Loupe from "./components/Loupe.svelte";
  import Compare from "./components/Compare.svelte";
  import Help from "./components/Help.svelte";
  import DoneDialog from "./components/DoneDialog.svelte";

  const items = $derived(ui.photos.filter((p) => p.state === "present" && p.pile === ui.pile));
  const tiles = $derived(tilesOf(items));
  const current = $derived(ui.photos.find((p) => p.id === ui.currentId) ?? null);
  const counts = $derived(
    Object.fromEntries(PILES.map((pile) => [pile.id, ui.photos.filter((p) => p.state === "present" && p.pile === pile.id).length])),
  );
  const undecided = $derived(
    ui.photos.filter((p) => p.state === "present" && p.pile === "your_call" && !p.decision).length,
  );
  let gridColumns = $state(6);

  function select(id: number | null, timed = true) {
    if (id === null || id === ui.currentId) return;
    const photo = ui.photos.find((p) => p.id === id);
    if (timed && photo && ui.mode !== "grid") {
      startTiming(ui.zoom ? "zoom" : "flip", isDecoded(ui.zoom ? urls.full(photo) : urls.screen(photo)));
    }
    ui.currentId = id;
  }

  function step(delta: number) {
    if (ui.mode === "compare" && current) {
      const frames = burstOf(current, ui.photos);
      const i = frames.findIndex((p) => p.id === current.id);
      select(frames[Math.min(Math.max(i + delta, 0), frames.length - 1)]?.id ?? null);
      return;
    }
    if (ui.mode === "grid") {
      const i = tiles.findIndex((t) => t.frames.some((p) => p.id === ui.currentId));
      select(tiles[Math.min(Math.max(i + delta, 0), tiles.length - 1)]?.photo.id ?? null);
      return;
    }
    const i = items.findIndex((p) => p.id === ui.currentId);
    select(items[Math.min(Math.max(i + delta, 0), items.length - 1)]?.id ?? null);
  }

  function burstStep(delta: number) {
    const i = tiles.findIndex((t) => t.frames.some((p) => p.id === ui.currentId));
    select(tiles[Math.min(Math.max(i + delta, 0), tiles.length - 1)]?.photo.id ?? null);
  }

  function update(id: number, change: Partial<Photo>) {
    const photo = ui.photos.find((p) => p.id === id);
    if (photo) Object.assign(photo, change);
  }

  async function decide(photo: Photo, decision: Photo["decision"], advance = true) {
    const before = photo.decision;
    update(photo.id, { decision });
    const overrules = decision !== null && photo.suggestion !== null && decision !== photo.suggestion;
    if (advance && ui.mode !== "compare") step(1);
    try {
      await api.decide(photo.id, decision);
      ui.reasonFor = overrules ? photo.id : null;
      if (overrules) setTimeout(() => ui.reasonFor === photo.id && (ui.reasonFor = null), 5000);
    } catch (error) {
      update(photo.id, { decision: before });
      say(`not saved: ${(error as Error).message}`);
    }
  }

  async function rate(photo: Photo, rating: number) {
    const before = photo.rating;
    update(photo.id, { rating });
    try {
      const result = await api.rate(photo.id, rating);
      if (result.sidecar === "pending") say("rating saved; it goes into the RawTherapee sidecar when the drive is back");
      else if (result.sidecar.startsWith("not written")) say(`rating saved, ${result.sidecar}`, 8000);
    } catch (error) {
      update(photo.id, { rating: before });
      say(`not saved: ${(error as Error).message}`);
    }
  }

  async function pick(photo: Photo, keepOthers: boolean) {
    try {
      const result = await api.pick(photo.id, keepOthers);
      for (const id of result.changed) update(id, { decision: id === photo.id ? "keep" : "reject" });
      say(keepOthers ? `kept #${photo.burst.position} too` : `picked #${photo.burst.position}; the rest of the burst is rejected`);
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

  function cyclePile(delta: number) {
    const i = PILES.findIndex((p) => p.id === ui.pile);
    ui.pile = PILES[(i + delta + PILES.length) % PILES.length].id;
    const first = ui.photos.find((p) => p.state === "present" && p.pile === ui.pile);
    ui.currentId = first?.id ?? null;
    ui.zoom = false;
    if (ui.mode === "compare") ui.mode = "loupe";
  }

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
    const photo = current;
    const handled = () => event.preventDefault();
    switch (key) {
      case "ArrowRight":
        handled();
        return step(1);
      case "ArrowLeft":
        handled();
        return step(-1);
      case "ArrowDown":
        handled();
        return ui.mode === "grid" ? step(gridColumns) : burstStep(1);
      case "ArrowUp":
        handled();
        return ui.mode === "grid" ? step(-gridColumns) : burstStep(-1);
      case "Enter":
        handled();
        ui.mode = ui.mode === "grid" ? "loupe" : "grid";
        ui.zoom = false;
        return;
      case "Escape":
        handled();
        if (ui.help) ui.help = false;
        else if (ui.zoom) ui.zoom = false;
        else if (ui.mode === "compare") ui.mode = "loupe";
        else if (ui.mode === "loupe") ui.mode = "grid";
        return;
      case "Tab":
        handled();
        return cyclePile(event.shiftKey ? -1 : 1);
      case "?":
        handled();
        ui.help = !ui.help;
        return;
    }
    if (!photo) return;
    switch (key.toLowerCase()) {
      case "p":
        handled();
        if (ui.mode === "compare") return pick(photo, event.shiftKey);
        return decide(photo, "keep");
      case "x":
        handled();
        return decide(photo, "reject", ui.mode !== "compare");
      case "u":
        handled();
        return decide(photo, null, false);
      case "z":
        handled();
        if (ui.mode === "grid") ui.mode = "loupe";
        if (ui.mode === "compare") ui.mode = "loupe";
        if (!ui.zoom) startTiming("zoom", isDecoded(urls.full(photo)));
        ui.zoom = !ui.zoom;
        return;
      case "f":
        handled();
        if (ui.mode === "grid") ui.mode = "loupe";
        ui.heat = !ui.heat;
        return;
      case "c":
        handled();
        ui.zoom = false;
        ui.mode = ui.mode === "compare" ? "loupe" : "compare";
        return;
      case "a": {
        handled();
        const frames = burstOf(photo, ui.photos);
        const best = frames.find((p) => p.burst.rank === 1);
        if (frames.length > 1 && best) return pick(best, false);
        return say("not a burst: nothing to accept");
      }
      case "d":
        handled();
        ui.debug = !ui.debug;
        return;
    }
    if (/^[0-5]$/.test(key)) {
      handled();
      rate(photo, Number(key));
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
            say("analysis done: the piles are sorted with focus, faces and bursts");
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
    <button class="logo" onclick={() => (ui.view = "folders")} title="Folders">rie</button>
    {#if ui.view === "cull" && ui.folder}
      <span class="folder" title={ui.folder.path}>{ui.folder.name}</span>
      <nav>
        {#each PILES as pile}
          <button
            class:active={ui.pile === pile.id}
            onclick={() => {
              ui.pile = pile.id;
              ui.currentId = ui.photos.find((p) => p.state === "present" && p.pile === pile.id)?.id ?? null;
            }}>{pile.label} <span class="count">{counts[pile.id]}</span></button
          >
        {/each}
      </nav>
      <span class="muted">{undecided} undecided in your call</span>
      <span class="spacer"></span>
      {#if ui.folder.culled_at}<span class="badge keep">culled</span>{/if}
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
    <button onclick={() => (ui.help = !ui.help)} title="Keyboard (?)">?</button>
  </header>

  {#if ui.offline}
    <div class="offline">
      Can't reach <code>rie serve</code>. Start it again in a terminal with <code>uv run rie serve</code> and keep it
      running; this page reconnects by itself.
    </div>
  {/if}

  <main>
    {#if ui.view === "folders"}
      <Folders />
    {:else if ui.mode === "grid"}
      <Grid {tiles} bind:columns={gridColumns} onopen={(id: number) => { select(id, false); ui.mode = "loupe"; }} />
    {:else if ui.mode === "compare" && current}
      <Compare photo={current} onselect={(id: number) => select(id)} onpick={(p: Photo, keep: boolean) => pick(p, keep)} />
    {:else if current}
      <Loupe photo={current} {items} />
    {:else}
      <p class="empty muted">Nothing in this pile. <kbd>Tab</kbd> switches piles.</p>
    {/if}
  </main>

  {#if current && ui.view === "cull"}
    <footer>
      <span class="name">{current.name}{current.jpeg ? ` + ${current.jpeg.split(".").pop()}` : ""}</span>
      {#if current.decision}<span class="badge {current.decision}">{current.decision}</span>{/if}
      {#if current.rating}<span class="badge">{"★".repeat(current.rating)}</span>{/if}
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
  .offline { background: var(--reject); color: #1d0505; padding: 6px 12px; font-weight: 600; }
  .offline code { background: #0002; padding: 0 4px; border-radius: 3px; }
  header {
    display: flex; align-items: center; gap: 10px; padding: 6px 10px; border-bottom: 1px solid var(--line);
    background: var(--panel); min-height: 42px;
  }
  .logo { font-weight: 700; letter-spacing: 0.05em; }
  .folder { font-weight: 600; max-width: 28ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  nav { display: flex; gap: 4px; }
  nav button.active { border-color: var(--accent); background: #24324a; }
  .count { color: var(--muted); margin-left: 4px; }
  .spacer { flex: 1; }
  .job { color: var(--warn); font-size: 12px; }
  .drive { font-size: 12px; color: var(--muted); }
  .drive.bad { color: var(--reject); }
  main { position: relative; overflow: hidden; min-height: 0; flex: 1 1 auto; }
  footer {
    display: flex; align-items: center; gap: 10px; padding: 5px 10px; border-top: 1px solid var(--line);
    background: var(--panel); font-size: 13px; min-height: 32px; white-space: nowrap; overflow: hidden;
  }
  .name { font-weight: 600; }
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
