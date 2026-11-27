<script lang="ts">
  // The review page (plan §2 step 6, §6 Review): everything kept is edited; this shows the exceptions first, with
  // before/after, a chip per agent with its reason and confidence, and the owner's nudges, resets, locks, apply to
  // scene, the look lab, open in RawTherapee and export.
  import { onMount } from "svelte";
  import { reviewApi, reviewUrls, type Edit, type FolderEdits, type Job, type Proposal } from "./api";
  import LookLab from "./LookLab.svelte";

  const LABELS: Record<string, string> = {
    geometry: "Straighten",
    white_balance: "White balance",
    exposure: "Exposure",
    color: "Colour",
    tone: "Tone",
    detail: "Detail",
  };
  const AGENT_GROUP: Record<string, string> = {
    geometry: "geometry",
    wb: "white_balance",
    exposure: "exposure",
    color: "color",
    baseline: "detail",
  };
  const RESETTABLE = ["white_balance", "exposure", "geometry", "color"];

  let folderId = $state<number | null>(null);
  let data = $state<FolderEdits | null>(null);
  let filter = $state<"review" | "all">("review");
  let currentId = $state<number | null>(null);
  let showBefore = $state(false);
  let group = $state("exposure");
  let message = $state("");
  let error = $state("");
  let lab = $state(false);
  let help = $state(false);
  let jobs = $state<Job[]>([]);
  let pending = $state({ ev: 0, warmth: 0, rotate: 0 });
  let busy = $state(false);

  const needing = (e: Edit) => e.review && !e.reviewed;
  const shown = $derived(data ? data.edits.filter((e) => filter === "all" || needing(e) || e.id === currentId) : []);
  const current = $derived(data?.edits.find((e) => e.id === currentId) ?? null);
  const needCount = $derived(data ? data.edits.filter(needing).length : 0);
  const activeJob = $derived(
    jobs.find((j) => j.folder_id === folderId && (j.state === "running" || j.state === "queued")) ?? null,
  );
  const sceneNumbers = $derived.by(() => {
    const order = new Map<number, number>();
    for (const e of data?.edits ?? []) if (e.scene !== null && !order.has(e.scene)) order.set(e.scene, order.size + 1);
    return order;
  });
  const warmMatrix = $derived(
    `${1 + 0.03 * pending.warmth} 0 0 0 0  0 1 0 0 0  0 0 ${1 - 0.03 * pending.warmth} 0 0  0 0 0 1 0`,
  );
  const previewStyle = $derived(
    `filter: brightness(${Math.pow(2, (0.15 * pending.ev) / 2.2).toFixed(3)}) url(#warmth);` +
      ` transform: rotate(${(-0.1 * pending.rotate).toFixed(2)}deg)`,
  );

  function readHash() {
    folderId = Number(new URLSearchParams(location.hash.slice(1)).get("folder")) || null;
  }

  async function load() {
    if (folderId === null) return;
    try {
      data = await reviewApi.edits(folderId);
      document.title = `Review · ${data.folder.name} · Raw Image Editor`;
      if (!data.edits.some((e) => e.id === currentId)) {
        currentId = (data.edits.find(needing) ?? data.edits[0])?.id ?? null;
      }
      if (filter === "review" && !data.edits.some(needing)) filter = "all";
      error = "";
    } catch (e) {
      error = (e as Error).message;
    }
  }

  function replace(e: Edit) {
    if (!data) return;
    const i = data.edits.findIndex((x) => x.id === e.id);
    if (i >= 0) data.edits[i] = e;
  }

  function groupOf(p: Proposal): string {
    return AGENT_GROUP[p.agent.split("@")[0]] ?? p.agent;
  }

  function value(e: Edit, g: string): string {
    const p = e.params[g] ?? {};
    if (g === "white_balance") return p.as_shot ? "as shot" : p.temperature_k ? `${p.temperature_k} K · ${Number(p.tint).toFixed(2)}` : "as shot";
    if (g === "exposure") return `${Number(p.ev ?? 0) >= 0 ? "+" : ""}${Number(p.ev ?? 0).toFixed(2)} EV` + (p.shadows ? ` · shadows ${p.shadows}` : "") + (p.highlight_compression ? ` · highlights ${p.highlight_compression}` : "");
    if (g === "geometry") return p.rotate_deg ? `${Number(p.rotate_deg) > 0 ? "+" : ""}${Number(p.rotate_deg).toFixed(2)}°` : "level";
    if (g === "color") return p.vibrance ? `vibrance +${p.vibrance}` : "as shot";
    if (g === "detail") return p.nr_chroma ? `noise ${p.nr_luma}/${p.nr_chroma}` : "no noise reduction";
    return "";
  }

  function locked(e: Edit, g: string): boolean {
    const keys = Object.keys(e.params[g] ?? {});
    return keys.length > 0 && keys.every((k) => e.provenance[`${g}.${k}`]?.locked);
  }

  function owner(e: Edit, g: string): boolean {
    return Object.keys(e.params[g] ?? {}).some((k) => (e.provenance[`${g}.${k}`]?.source ?? "").startsWith("human"));
  }

  function move(delta: number) {
    if (!shown.length) return;
    const i = shown.findIndex((e) => e.id === currentId);
    const next = shown[Math.min(Math.max((i < 0 ? 0 : i) + delta, 0), shown.length - 1)];
    select(next.id);
  }

  function select(id: number) {
    if (id === currentId) return;
    flushNow();
    currentId = id;
    showBefore = false;
  }

  function nextNeeding() {
    if (!data) return;
    const list = data.edits;
    const i = list.findIndex((e) => e.id === currentId);
    for (let k = 1; k <= list.length; k++) {
      const e = list[(i + k) % list.length];
      if (needing(e)) return select(e.id);
    }
    message = "Nothing else needs a look.";
  }

  let timer: ReturnType<typeof setTimeout> | undefined;
  let pendingFor: number | null = null;

  function nudge(kind: "ev" | "warmth" | "rotate", steps: number) {
    if (!current) return;
    if (pendingFor !== null && pendingFor !== current.id) flushNow();
    pendingFor = current.id;
    pending[kind] += steps;
    group = kind === "ev" ? "exposure" : kind === "warmth" ? "white_balance" : "geometry";
    clearTimeout(timer);
    timer = setTimeout(flushNow, 450);
  }

  async function flushNow() {
    clearTimeout(timer);
    const id = pendingFor;
    const steps = { ...pending };
    pending = { ev: 0, warmth: 0, rotate: 0 };
    pendingFor = null;
    if (id === null || (!steps.ev && !steps.warmth && !steps.rotate)) return;
    busy = true;
    try {
      for (const kind of ["ev", "warmth", "rotate"] as const) {
        if (steps[kind]) replace(await reviewApi.nudge(id, kind, steps[kind]));
      }
      message = "Re-rendered.";
    } catch (e) {
      error = (e as Error).message;
    } finally {
      busy = false;
    }
  }

  async function run<T>(what: () => Promise<T>, done?: (r: T) => void) {
    busy = true;
    error = "";
    try {
      const result = await what();
      done?.(result);
    } catch (e) {
      error = (e as Error).message;
    } finally {
      busy = false;
    }
  }

  function reset() {
    if (!current || !RESETTABLE.includes(group)) return void (message = `${LABELS[group]} cannot be reset; pick another chip.`);
    run(() => reviewApi.reset(current!.id, group), (e) => {
      replace(e);
      message = `${LABELS[group]} back to as shot, locked.`;
    });
  }

  async function toggleLock() {
    const e = current;
    if (!e) return;
    const keys = Object.keys(e.params[group] ?? {});
    if (!keys.length) return void (message = `Nothing set for ${LABELS[group]}.`);
    const lock = !locked(e, group);
    await run(async () => {
      let last = e;
      for (const k of keys) last = await reviewApi.lock(e.id, `${group}.${k}`, lock);
      return last;
    }, (updated) => {
      replace(updated);
      message = `${LABELS[group]} ${lock ? "locked: the agents keep it" : "unlocked"}.`;
    });
  }

  function applyScene() {
    if (!current) return;
    run(() => reviewApi.scene(current!.id, [group]), (r) => {
      replace(r.photo);
      message = r.changed.length ? `${LABELS[group]} given to ${r.changed.length} more photos of the scene; rendering.` : "The scene already matches.";
    });
  }

  function markReviewed() {
    if (!current) return;
    const id = current.id;
    run(() => reviewApi.reviewed(id, !current!.reviewed), (e) => {
      replace(e);
      if (e.reviewed) nextNeeding();
    });
  }

  function startEdit(all: boolean) {
    if (folderId === null) return;
    run(() => reviewApi.edit(folderId!, all), () => (message = "Editing in the background…"));
  }

  function startExport() {
    if (folderId === null) return;
    run(() => reviewApi.exportFolder(folderId!), () => (message = "Exporting full-size JPEGs into jpg/…"));
  }

  function openRawTherapee() {
    if (!current) return;
    run(() => reviewApi.rawtherapee(current!.id), (r) => (message = `Opened ${r.opened} in RawTherapee.`));
  }

  function onKey(event: KeyboardEvent) {
    if (lab) {
      if (event.key === "Escape") lab = false;
      return;
    }
    if (event.target instanceof HTMLInputElement) return;
    const keys: Record<string, () => void> = {
      ArrowRight: () => move(1),
      ArrowLeft: () => move(-1),
      ArrowDown: () => move(1),
      ArrowUp: () => move(-1),
      n: nextNeeding,
      "\\": () => (showBefore = !showBefore),
      "[": () => nudge("ev", -1),
      "]": () => nudge("ev", 1),
      ",": () => nudge("warmth", -1),
      ".": () => nudge("warmth", 1),
      "{": () => nudge("rotate", -1),
      "}": () => nudge("rotate", 1),
      r: reset,
      l: toggleLock,
      s: applyScene,
      e: openRawTherapee,
      g: () => (lab = !!current),
      " ": markReviewed,
      Tab: () => (filter = filter === "review" ? "all" : "review"),
      "?": () => (help = !help),
      Escape: () => (help = false),
    };
    const action = keys[event.key];
    if (action && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      action();
    }
  }

  onMount(() => {
    readHash();
    load();
    window.addEventListener("hashchange", () => {
      readHash();
      currentId = null;
      load();
    });
    const events = new EventSource("/api/events");
    let wasActive = false;
    events.onmessage = (event) => {
      jobs = JSON.parse(event.data).jobs ?? [];
      const active = jobs.some((j) => j.folder_id === folderId && (j.state === "running" || j.state === "queued"));
      if (wasActive && !active) {
        const last = jobs.filter((j) => j.folder_id === folderId).at(-1);
        if (last) message = last.message;
        load();
      }
      wasActive = active;
    };
    return () => events.close();
  });
</script>

<svelte:window onkeydown={onKey} />

<svg width="0" height="0" style="position: absolute" aria-hidden="true">
  <filter id="warmth"><feColorMatrix type="matrix" values={warmMatrix} /></filter>
</svg>

<div class="page">
  <header>
    <a class="home" href="/" title="Back to the folders">Raw Image Editor</a>
    <span class="sep">›</span>
    <h1>Review</h1>
    {#if data}
      <span class="folder">{data.folder.name}</span>
      <button class:on={filter === "review"} onclick={() => (filter = "review")}>Needs a look {needCount}</button>
      <button class:on={filter === "all"} onclick={() => (filter = "all")}>All {data.edits.length}</button>
      <span class="grow"></span>
      {#if data.no_override_share !== null}
        <span class="muted" title="Edited photos you have not changed (the Phase 2 exit metric)">
          untouched {Math.round(data.no_override_share * 100)} %
        </span>
      {/if}
      <button onclick={() => startEdit(false)} disabled={busy || !!activeJob} title="Run the edit agents on the kept photos again; your changes stay">
        Edit again
      </button>
      <button class="primary" onclick={startExport} disabled={busy || !!activeJob || !data.edits.length} title="Write full-size JPEGs into jpg/">
        Export {data.exports.count ? `(${data.exports.count} done)` : ""}
      </button>
      <button onclick={() => (help = !help)}>?</button>
    {/if}
  </header>

  {#if activeJob}
    <div class="job">{activeJob.kind}: {activeJob.step} {activeJob.done}/{activeJob.total}</div>
  {/if}
  {#if error}<div class="error">{error}</div>{/if}

  {#if folderId === null}
    <p class="empty">Open a folder from the <a href="/">folders page</a> and choose Review.</p>
  {:else if data && !data.edits.length}
    <div class="empty">
      <p>No edits in this folder yet.</p>
      <button class="primary" onclick={() => startEdit(false)} disabled={busy || !!activeJob}>Edit the kept photos</button>
      <button onclick={() => startEdit(true)} disabled={busy || !!activeJob}>Edit every photo</button>
    </div>
  {:else if data}
    <div class="body">
      <nav class="strip">
        {#each shown as e (e.id)}
          <button class="tile" class:current={e.id === currentId} onclick={() => select(e.id)} title={e.reasons.join("\n")}>
            <img src={reviewUrls.thumb(e)} alt={e.name} loading="lazy" />
            <span class="name">{e.name}</span>
            {#if needing(e)}<span class="badge warn">needs a look</span>{:else if e.reviewed}<span class="badge keep">seen</span>{/if}
          </button>
        {:else}
          <p class="muted">Nothing needs a look. <button class="link" onclick={() => (filter = "all")}>Show all</button></p>
        {/each}
      </nav>

      {#if current}
        <main>
          <div class="view">
            {#if showBefore ? current.has_before : current.has_after}
              <img
                class="photo"
                src={reviewUrls.preview(current, showBefore ? "before" : "after")}
                alt={current.name}
                style={showBefore ? "" : previewStyle}
              />
            {:else}
              <p class="muted">No preview yet.</p>
            {/if}
            <button class="label" onclick={() => (showBefore = !showBefore)} title="Before / after">
              {showBefore ? "as shot" : "edited"}{busy ? " · rendering…" : ""} <kbd>\</kbd>
            </button>
          </div>
          <div class="info">
            <div class="title">
              <strong>{current.name}</strong>
              {#if current.scene !== null}<span class="muted">scene {sceneNumbers.get(current.scene)}</span>{/if}
              <span class="muted">look {current.look} · recipe v{current.version}</span>
            </div>
            <ul class="chips">
              {#each current.proposals as p (p.agent)}
                {#if p.band !== "abstained" || p.flags.length}
                  <li>
                    <button
                      class="chip band-{p.band}"
                      class:selected={group === groupOf(p)}
                      onclick={() => (group = groupOf(p))}
                      title={p.why}
                    >
                      <b>{LABELS[groupOf(p)] ?? p.agent}</b>
                      {value(current, groupOf(p))} · {Math.round(p.confidence * 100)} %
                      {#if locked(current, groupOf(p))}🔒{/if}
                      {#if owner(current, groupOf(p))}<span class="badge">yours</span>{/if}
                      <span class="why">{p.why}</span>
                    </button>
                  </li>
                {/if}
              {/each}
            </ul>
            {#if current.reasons.length}
              <ul class="reasons">{#each current.reasons as r}<li>{r}</li>{/each}</ul>
            {/if}
          </div>
        </main>

        <aside class="actions">
          <h3>{LABELS[group]}</h3>
          <div class="row"><button onclick={() => nudge("ev", -1)}><kbd>[</kbd> darker</button><button onclick={() => nudge("ev", 1)}><kbd>]</kbd> brighter</button></div>
          <div class="row"><button onclick={() => nudge("warmth", -1)}><kbd>,</kbd> cooler</button><button onclick={() => nudge("warmth", 1)}><kbd>.</kbd> warmer</button></div>
          <div class="row"><button onclick={() => nudge("rotate", -1)}><kbd>{"{"}</kbd> turn ↻</button><button onclick={() => nudge("rotate", 1)}><kbd>{"}"}</kbd> turn ↺</button></div>
          <button onclick={reset}><kbd>R</kbd> reset {LABELS[group].toLowerCase()} to as shot</button>
          <button onclick={toggleLock}><kbd>L</kbd> {locked(current, group) ? "unlock" : "lock"} {LABELS[group].toLowerCase()}</button>
          <button onclick={applyScene}><kbd>S</kbd> apply {LABELS[group].toLowerCase()} to the scene</button>
          <button onclick={() => (lab = true)}><kbd>G</kbd> look lab</button>
          <button onclick={openRawTherapee}><kbd>E</kbd> open in RawTherapee</button>
          <button class="primary" onclick={markReviewed}><kbd>Space</kbd> {current.reviewed ? "not seen yet" : "seen, next"}</button>
          {#if message}<p class="message">{message}</p>{/if}
        </aside>
      {/if}
    </div>
  {/if}

  {#if help}
    <div class="help" role="dialog" aria-label="Keys">
      <h2>Review keys</h2>
      <table>
        <tbody>
          <tr><td><kbd>←</kbd> <kbd>→</kbd></td><td>previous / next photo</td><td><kbd>N</kbd></td><td>next photo that needs a look</td></tr>
          <tr><td><kbd>\</kbd></td><td>before / after</td><td><kbd>Tab</kbd></td><td>needs a look / all</td></tr>
          <tr><td><kbd>[</kbd> <kbd>]</kbd></td><td>exposure −/+ 0.15 EV</td><td><kbd>,</kbd> <kbd>.</kbd></td><td>cooler / warmer (10 mired)</td></tr>
          <tr><td><kbd>{"{"}</kbd> <kbd>{"}"}</kbd></td><td>turn −/+ 0.1°</td><td><kbd>R</kbd></td><td>reset the chosen chip's group to as shot</td></tr>
          <tr><td><kbd>L</kbd></td><td>lock / unlock it</td><td><kbd>S</kbd></td><td>apply it to the whole scene</td></tr>
          <tr><td><kbd>G</kbd></td><td>look lab</td><td><kbd>E</kbd></td><td>open in RawTherapee</td></tr>
          <tr><td><kbd>Space</kbd></td><td>seen, go to the next</td><td><kbd>?</kbd></td><td>this help</td></tr>
        </tbody>
      </table>
      <p class="muted">Click a chip to choose what R, L and S act on. Every change you make is locked, so the next edit run keeps it. The preview changes at once roughly, and the real render replaces it a moment later.</p>
      <button onclick={() => (help = false)}>Close</button>
    </div>
  {/if}

  {#if lab && current && data}
    <LookLab
      photo={current}
      looks={data.looks}
      onclose={() => (lab = false)}
      onchanged={(text) => {
        message = text;
        lab = false;
        load();
      }}
    />
  {/if}
</div>

<style>
  .page { height: 100%; display: flex; flex-direction: column; }
  header { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
  header h1 { font-size: 16px; margin: 0; }
  .home { color: var(--text); text-decoration: none; font-weight: 600; }
  .sep { color: var(--muted); }
  .folder { color: var(--muted); margin-right: 8px; }
  .grow { flex: 1; }
  button.on { border-color: var(--accent); background: rgba(106, 162, 255, 0.15); }
  .job { padding: 4px 12px; background: var(--panel); color: var(--muted); }
  .error { padding: 6px 12px; color: var(--reject); }
  .empty { padding: 40px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .body { flex: 1; min-height: 0; display: grid; grid-template-columns: 180px 1fr 240px; }
  .strip { overflow-y: auto; border-right: 1px solid var(--line); padding: 6px; display: flex; flex-direction: column; gap: 6px; }
  .tile { display: flex; flex-direction: column; gap: 3px; padding: 4px; text-align: left; }
  .tile img { width: 100%; height: auto; border-radius: 3px; }
  .tile.current { border-color: var(--accent); }
  .tile .name { font-size: 12px; }
  main { min-width: 0; display: flex; flex-direction: column; }
  .view { flex: 1; min-height: 0; position: relative; display: flex; align-items: center; justify-content: center; overflow: hidden; background: #000; }
  .photo { max-width: 100%; max-height: 100%; object-fit: contain; }
  .label { position: absolute; left: 10px; top: 8px; background: rgba(0, 0, 0, 0.6); padding: 2px 8px; border-radius: 4px; }
  .primary kbd { background: transparent; color: inherit; border-color: rgba(0, 0, 0, 0.35); }
  .info { padding: 8px 12px; border-top: 1px solid var(--line); max-height: 40%; overflow-y: auto; }
  .title { display: flex; gap: 12px; align-items: baseline; margin-bottom: 6px; }
  .chips { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  .chip { width: 100%; text-align: left; border-left-width: 4px; }
  .chip.selected { border-color: var(--accent); }
  .chip.band-apply { border-left-color: var(--keep); }
  .chip.band-conservative { border-left-color: var(--warn); }
  .chip.band-as_shot { border-left-color: var(--reject); }
  .chip.band-locked { border-left-color: var(--accent); }
  .chip .why { display: block; color: var(--muted); font-size: 12px; }
  .reasons { margin: 6px 0 0; padding-left: 18px; color: var(--warn); }
  .actions { border-left: 1px solid var(--line); padding: 10px; display: flex; flex-direction: column; gap: 6px; overflow-y: auto; }
  .actions h3 { margin: 0 0 4px; font-size: 14px; }
  .actions .row { display: flex; gap: 6px; }
  .actions .row button { flex: 1; }
  .message { color: var(--muted); font-size: 12px; }
  .help { position: fixed; inset: 10% 15%; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; z-index: 20; overflow: auto; }
  .help td { padding: 3px 10px 3px 0; }
  .badge.warn { background: var(--warn); color: #201800; }
  .badge.keep { background: var(--keep); color: #04170b; }
  button.link { background: none; border: none; color: var(--accent); padding: 0; }
  @media (max-width: 900px) { .body { grid-template-columns: 1fr; } .strip { flex-direction: row; overflow-x: auto; max-height: 140px; } .tile { width: 120px; } }
</style>
