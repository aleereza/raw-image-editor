<script lang="ts">
  // The review page (plan §2 step 6, §6 Review): everything kept is edited; this shows the exceptions first, with
  // before/after, a chip per agent with its reason and confidence, and the owner's nudges, resets, locks, apply to
  // scene, the look lab, open in RawTherapee and export.
  import { onMount, untrack } from "svelte";
  import { reviewApi, reviewUrls, type Edit, type FolderEdits, type Job, type Proposal } from "./api";
  import LookLab from "./LookLab.svelte";
  import ReviewHelp from "./ReviewHelp.svelte";

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
  let viewEl: HTMLDivElement | undefined = $state();
  let photoEl: HTMLImageElement | undefined = $state();
  let viewW = $state(0);
  let viewH = $state(0);
  let imgNatural = $state({ id: -1, w: 0, h: 0 });
  let zoom = $state(false);
  let offset = $state({ x: 0, y: 0 });
  let dragging = $state(false);
  let zoomFocus = $state<[number, number] | null>(null);

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
  const zoomedSize = $derived.by(() => {
    if (!zoom || currentId === null || imgNatural.id !== currentId || !imgNatural.w || !imgNatural.h) return null;
    const fitScale = viewW && viewH ? Math.min(viewW / imgNatural.w, viewH / imgNatural.h, 1) : 1;
    const scale = Math.max(1, fitScale * 2);
    return { w: imgNatural.w * scale, h: imgNatural.h * scale };
  });
  const photoStyle = $derived.by(() => {
    const parts: string[] = [];
    if (!showBefore) {
      parts.push(`filter: brightness(${Math.pow(2, (0.15 * pending.ev) / 2.2).toFixed(3)}) url(#warmth)`);
    }
    const transforms: string[] = [];
    if (zoomedSize) {
      parts.push(`width: ${zoomedSize.w}px`, `height: ${zoomedSize.h}px`);
      transforms.push(`translate(${offset.x}px, ${offset.y}px)`);
    }
    if (!showBefore && pending.rotate) {
      transforms.push(`rotate(${(-0.1 * pending.rotate).toFixed(2)}deg)`);
    }
    if (transforms.length) parts.push(`transform: ${transforms.join(" ")}`);
    return parts.join("; ");
  });

  function clampOffset(x: number, y: number): { x: number; y: number } {
    if (!zoomedSize) return { x, y };
    const along = (value: number, size: number, view: number) =>
      size <= view ? (view - size) / 2 : Math.min(0, Math.max(view - size, value));
    return { x: along(x, zoomedSize.w, viewW), y: along(y, zoomedSize.h, viewH) };
  }

  function onPhotoLoad() {
    if (photoEl && photoEl.naturalWidth > 0 && currentId !== null) {
      imgNatural = { id: currentId, w: photoEl.naturalWidth, h: photoEl.naturalHeight };
    }
  }

  function toggleZoom(focus: [number, number] = [0.5, 0.5]) {
    if (!current) return;
    zoom = !zoom;
    zoomFocus = zoom ? focus : null;
  }

  function onDoubleClick(event: MouseEvent) {
    if (!current || !photoEl || (event.target as HTMLElement).closest("button")) return;
    if (zoom) {
      zoom = false;
      zoomFocus = null;
      return;
    }
    const rect = photoEl.getBoundingClientRect();
    const u = rect.width ? Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)) : 0.5;
    const v = rect.height ? Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)) : 0.5;
    toggleZoom([u, v]);
  }

  // Drag or scroll (mouse wheel / two-finger touchpad) to pan while zoomed, clamped to the photo's edges.
  let drag: { x: number; y: number; ox: number; oy: number } | null = null;
  function onPointerDown(event: PointerEvent) {
    if (!zoomedSize || event.button !== 0 || (event.target as HTMLElement).closest("button")) return;
    drag = { x: event.clientX, y: event.clientY, ox: offset.x, oy: offset.y };
    dragging = true;
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  }
  function onPointerMove(event: PointerEvent) {
    if (!drag) return;
    offset = clampOffset(drag.ox + event.clientX - drag.x, drag.oy + event.clientY - drag.y);
  }
  function onPointerUp() {
    drag = null;
    dragging = false;
  }

  $effect(() => {
    const id = currentId;
    void showBefore;
    if (id !== null && photoEl?.complete && photoEl.naturalWidth > 0) {
      imgNatural = { id, w: photoEl.naturalWidth, h: photoEl.naturalHeight };
    }
  });

  $effect(() => {
    const size = zoomedSize;
    const w = viewW;
    const h = viewH;
    const focus = zoomFocus;
    if (!size || !w || !h) return;
    if (focus) {
      const [u, v] = focus;
      offset = clampOffset(w / 2 - u * size.w, h / 2 - v * size.h);
      zoomFocus = null;
    } else {
      const cur = untrack(() => offset);
      const next = clampOffset(cur.x, cur.y);
      if (next.x !== cur.x || next.y !== cur.y) offset = next;
    }
  });

  $effect(() => {
    const el = viewEl;
    if (!el) return;
    const onWheel = (event: WheelEvent) => {
      if (!zoomedSize || event.ctrlKey) return;
      event.preventDefault();
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? el.clientHeight : 1;
      offset = clampOffset(offset.x - event.deltaX * unit, offset.y - event.deltaY * unit);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  function readHash() {
    folderId = Number(new URLSearchParams(location.hash.slice(1)).get("folder")) || null;
  }

  async function load() {
    if (folderId === null) return;
    try {
      data = await reviewApi.edits(folderId);
      document.title = `Review · ${data.folder.name} · Raw Image Editor`;
      if (!data.edits.some((e) => e.id === currentId)) {
        const picked = data.edits.find(needing) ?? data.edits[0] ?? null;
        currentId = picked?.id ?? null;
        pickDefaultGroup(picked);
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

  function formatParams(g: string, p: Record<string, unknown>): string {
    if (g === "white_balance") return p.as_shot ? "as shot" : p.temperature_k ? `${p.temperature_k} K · ${Number(p.tint).toFixed(2)}` : "as shot";
    if (g === "exposure") return `${Number(p.ev ?? 0) >= 0 ? "+" : ""}${Number(p.ev ?? 0).toFixed(2)} EV` + (p.shadows ? ` · shadows ${p.shadows}` : "") + (p.highlight_compression ? ` · highlights ${p.highlight_compression}` : "");
    if (g === "geometry") return p.rotate_deg ? `${Number(p.rotate_deg) > 0 ? "+" : ""}${Number(p.rotate_deg).toFixed(2)}°` : "level";
    if (g === "color") return p.vibrance ? `vibrance +${p.vibrance}` : "as shot";
    if (g === "detail") return p.nr_chroma ? `noise ${p.nr_luma}/${p.nr_chroma}` : "no noise reduction";
    return "";
  }

  function value(e: Edit, g: string): string {
    return formatParams(g, e.params[g] ?? {});
  }

  function suggestedFor(e: Edit, g: string): Record<string, unknown> | null {
    const proposal = e.proposals.find((p) => groupOf(p) === g && p.band !== "abstained");
    const proposed = proposal?.params[g];
    if (!proposed || !Object.keys(proposed).length) return null;
    const cur = e.params[g] ?? {};
    const differs = Object.entries(proposed).some(([k, v]) => {
      const c = cur[k];
      if (typeof v === "number") return Math.abs(v - Number(c ?? 0)) > 1e-4;
      return c !== v;
    });
    return differs ? proposed : null;
  }

  const acceptTarget = $derived.by(() => {
    if (!current) return null;
    const direct = suggestedFor(current, group);
    if (direct) return { group, params: direct };
    for (const p of current.proposals) {
      const g = groupOf(p);
      const s = suggestedFor(current, g);
      if (s) return { group: g, params: s };
    }
    return null;
  });

  function pickDefaultGroup(e: Edit | null) {
    if (!e) return;
    const unapplied = e.proposals.find((p) => suggestedFor(e, groupOf(p)) !== null);
    if (unapplied) {
      group = groupOf(unapplied);
      return;
    }
    const flagged = e.proposals.find((p) => p.band !== "abstained" && p.flags.length > 0);
    if (flagged) group = groupOf(flagged);
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
    if (zoom) zoomFocus = [0.5, 0.5];
    pickDefaultGroup(data?.edits.find((e) => e.id === id) ?? null);
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

  function acceptProposal() {
    if (!current || !acceptTarget) return void (message = "Nothing suggested to apply.");
    const target = acceptTarget;
    group = target.group;
    run(() => reviewApi.accept(current!.id, target.group), (e) => {
      replace(e);
      message = `${LABELS[target.group]} set to ${formatParams(target.group, target.params)}, locked.`;
    });
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
      "<": () => nudge("warmth", -5),
      ">": () => nudge("warmth", 5),
      "{": () => nudge("rotate", -1),
      "}": () => nudge("rotate", 1),
      a: acceptProposal,
      A: acceptProposal,
      r: reset,
      l: toggleLock,
      s: applyScene,
      e: openRawTherapee,
      g: () => (lab = !!current),
      z: () => toggleZoom(),
      Z: () => toggleZoom(),
      " ": markReviewed,
      Tab: () => (filter = filter === "review" ? "all" : "review"),
      "?": () => (help = !help),
      Escape: () => {
        if (help) help = false;
        else if (zoom) {
          zoom = false;
          zoomFocus = null;
        }
      },
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
      <button onclick={() => (help = !help)} title="Guide and keys (?)">?</button>
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
          <div
            class="view"
            class:zoom={!!zoomedSize}
            class:dragging
            bind:this={viewEl}
            bind:clientWidth={viewW}
            bind:clientHeight={viewH}
            onpointerdown={onPointerDown}
            onpointermove={onPointerMove}
            onpointerup={onPointerUp}
            onpointercancel={onPointerUp}
            ondblclick={onDoubleClick}
            role="presentation"
          >
            {#if showBefore ? current.has_before : current.has_after}
              <img
                class="photo"
                class:zoomed={!!zoomedSize}
                bind:this={photoEl}
                onload={onPhotoLoad}
                draggable={false}
                src={reviewUrls.preview(current, showBefore ? "before" : "after")}
                alt={current.name}
                style={photoStyle}
              />
            {:else}
              <p class="muted">No preview yet.</p>
            {/if}
            <button class="label" onclick={() => (showBefore = !showBefore)} title="Before / after">
              {showBefore ? "as shot" : "edited"}{busy ? " · rendering…" : ""} <kbd>\</kbd>
            </button>
            {#if zoomedSize}
              <div class="legend">zoomed · drag or scroll to move around · <kbd>Z</kbd> fit</div>
            {/if}
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
          <div class="row">
            <button onclick={(e) => nudge("ev", e.shiftKey ? -3 : -1)} title="Darker ([; Shift+click: −0.45 EV)"><kbd>[</kbd> darker</button>
            <button onclick={(e) => nudge("ev", e.shiftKey ? 3 : 1)} title="Brighter (]; Shift+click: +0.45 EV)"><kbd>]</kbd> brighter</button>
          </div>
          <div class="row">
            <button onclick={(e) => nudge("warmth", e.shiftKey ? -5 : -1)} title="Cooler (,; < or Shift+click: 5×)"><kbd>,</kbd> cooler</button>
            <button onclick={(e) => nudge("warmth", e.shiftKey ? 5 : 1)} title="Warmer (.; > or Shift+click: 5×)"><kbd>.</kbd> warmer</button>
          </div>
          <div class="row turn">
            <button onclick={() => nudge("rotate", -5)} title="Turn 0.5° clockwise">↻ 0.5°</button>
            <button onclick={(e) => nudge("rotate", e.shiftKey ? -5 : -1)} title={"Turn 0.1° clockwise ({)"}><kbd>{"{"}</kbd> ↻</button>
            <button onclick={(e) => nudge("rotate", e.shiftKey ? 5 : 1)} title={"Turn 0.1° counter-clockwise (})"}><kbd>{"}"}</kbd> ↺</button>
            <button onclick={() => nudge("rotate", 5)} title="Turn 0.5° counter-clockwise">↺ 0.5°</button>
          </div>
          {#if acceptTarget}
            <button class="accept" onclick={acceptProposal} title="Apply the agent's full suggested value and lock it">
              <kbd>A</kbd> apply {LABELS[acceptTarget.group].toLowerCase()} ({formatParams(acceptTarget.group, acceptTarget.params)})
            </button>
          {/if}
          <button onclick={reset}><kbd>R</kbd> reset {LABELS[group].toLowerCase()} to as shot</button>
          <button onclick={toggleLock}><kbd>L</kbd> {locked(current, group) ? "unlock" : "lock"} {LABELS[group].toLowerCase()}</button>
          <button onclick={applyScene}><kbd>S</kbd> apply {LABELS[group].toLowerCase()} to the scene</button>
          <button onclick={() => toggleZoom()}><kbd>Z</kbd> {zoom ? "fit photo" : "zoom in"}</button>
          <button onclick={() => (lab = true)}><kbd>G</kbd> look lab</button>
          <button onclick={openRawTherapee}><kbd>E</kbd> open in RawTherapee</button>
          <button class="primary" onclick={markReviewed}><kbd>Space</kbd> {current.reviewed ? "not seen yet" : "seen, next"}</button>
          {#if message}<p class="message">{message}</p>{/if}
        </aside>
      {/if}
    </div>
  {/if}

  {#if help}
    <ReviewHelp onclose={() => (help = false)} />
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
  .page { height: 100%; display: flex; flex-direction: column; overflow: hidden; }
  header { flex: none; display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
  header h1 { font-size: 16px; margin: 0; }
  .home { color: var(--text); text-decoration: none; font-weight: 600; }
  .sep { color: var(--muted); }
  .folder { color: var(--muted); margin-right: 8px; }
  .grow { flex: 1; }
  button.on { border-color: var(--accent); background: rgba(106, 162, 255, 0.15); }
  .job { flex: none; padding: 4px 12px; background: var(--panel); color: var(--muted); }
  .error { flex: none; padding: 6px 12px; color: var(--reject); }
  .empty { padding: 40px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .body { flex: 1; min-height: 0; display: grid; grid-template-columns: 180px 1fr 240px; grid-template-rows: minmax(0, 1fr); overflow: hidden; }
  .strip { min-height: 0; overflow-y: auto; border-right: 1px solid var(--line); padding: 6px; display: flex; flex-direction: column; gap: 6px; }
  .tile { display: flex; flex-direction: column; gap: 3px; padding: 4px; text-align: left; }
  .tile img { width: 100%; height: auto; border-radius: 3px; }
  .tile.current { border-color: var(--accent); }
  .tile .name { font-size: 12px; }
  main { min-width: 0; min-height: 0; overflow: hidden; display: flex; flex-direction: column; }
  .view { flex: 1 1 0%; min-height: 0; position: relative; display: flex; align-items: center; justify-content: center; overflow: hidden; background: #000; }
  .view.zoom { display: block; cursor: grab; }
  .view.zoom.dragging { cursor: grabbing; }
  .photo { max-width: 100%; max-height: 100%; width: auto; height: auto; min-width: 0; min-height: 0; object-fit: contain; display: block; user-select: none; }
  .photo.zoomed { position: absolute; left: 0; top: 0; max-width: none; max-height: none; }
  .label { position: absolute; left: 10px; top: 8px; z-index: 2; background: rgba(0, 0, 0, 0.6); padding: 2px 8px; border-radius: 4px; }
  .legend { position: absolute; bottom: 10px; left: 10px; z-index: 2; background: #000b; padding: 4px 8px; border-radius: 4px; font-size: 12px; display: flex; gap: 4px; align-items: center; }
  .primary kbd { background: transparent; color: inherit; border-color: rgba(0, 0, 0, 0.35); }
  .info { flex: 0 1 auto; padding: 8px 12px; border-top: 1px solid var(--line); max-height: 38%; overflow-y: auto; }
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
  .actions { min-height: 0; border-left: 1px solid var(--line); padding: 10px; display: flex; flex-direction: column; gap: 6px; overflow-y: auto; }
  .actions h3 { margin: 0 0 4px; font-size: 14px; }
  .actions .row { display: flex; gap: 6px; }
  .actions .row button { flex: 1; }
  .actions .row.turn { gap: 4px; }
  .actions .row.turn button { padding: 5px 4px; font-size: 12px; }
  .actions .accept { border-color: var(--accent); }
  .message { color: var(--muted); font-size: 12px; }
  .badge.warn { background: var(--warn); color: #201800; }
  .badge.keep { background: var(--keep); color: #04170b; }
  button.link { background: none; border: none; color: var(--accent); padding: 0; }
  @media (max-width: 900px) { .body { grid-template-columns: 1fr; grid-template-rows: auto minmax(0, 1fr) auto; } .strip { flex-direction: row; overflow-x: auto; max-height: 140px; } .tile { width: 120px; } }
</style>
