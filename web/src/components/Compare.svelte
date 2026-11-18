<script lang="ts">
  import { urls, type Analysis, type Face, type Photo } from "../lib/api";
  import { analysisFor, burstOf, ui } from "../lib/state.svelte";

  let { photo, onselect, onpick }: { photo: Photo; onselect: (id: number) => void; onpick: (p: Photo, keepOthers: boolean) => void } = $props();

  const frames = $derived(burstOf(photo, ui.photos));
  let analyses = $state<Record<number, Analysis>>({});

  $effect(() => {
    for (const frame of frames) {
      if (!analyses[frame.id]) analysisFor(frame.id).then((a) => (analyses = { ...analyses, [frame.id]: a }));
    }
  });

  // One row per person (face track), one column per frame.
  const tracks = $derived.by(() => {
    const ids = new Set<number>();
    for (const frame of frames) for (const face of analyses[frame.id]?.faces ?? []) if (face.track !== null && face.blink !== null) ids.add(face.track);
    return [...ids].sort((a, b) => a - b);
  });

  function faceOf(frame: Photo, track: number): Face | undefined {
    return analyses[frame.id]?.faces.find((f) => f.track === track);
  }

  function eyes(face: Face): { text: string; cls: string } {
    if (face.blink === null) return { text: "?", cls: "" };
    return face.blink >= 0.5 ? { text: "eyes closed?", cls: "warn" } : { text: "eyes open", cls: "keep" };
  }

  const columns = $derived(Math.min(Math.max(frames.length, 2), 6));
</script>

<div class="compare" style:--cols={columns}>
  <div class="frames">
    {#each frames as frame (frame.id)}
      <button class="frame" class:active={frame.id === photo.id} class:reject={frame.decision === "reject"} onclick={() => onselect(frame.id)} ondblclick={() => onpick(frame, false)}>
        <img src={urls.screen(frame)} alt={frame.name} />
        <div class="label">
          <b>#{frame.burst.position}</b>
          {#if frame.burst.rank === 1}<span class="badge" title="the app's pick">★ best</span>{/if}
          {#if frame.decision}<span class="badge {frame.decision}">{frame.decision}</span>{/if}
          {#if frame.focus.verdict}<span class="badge">{frame.focus.verdict}</span>{/if}
          {#if frame.closed}<span class="badge warn">{frame.closed} blinking</span>{/if}
        </div>
        {#if frame.burst.issues.length}<div class="issues muted">{frame.burst.issues.join(", ")}</div>{/if}
      </button>
    {/each}
  </div>
  {#if tracks.length}
    <div class="strips">
      {#each tracks as track, row}
        <div class="strip">
          <div class="who muted">person {row + 1}</div>
          {#each frames as frame (frame.id)}
            {@const face = faceOf(frame, track)}
            <div class="cell" class:active={frame.id === photo.id}>
              {#if face}
                <img src={urls.face(frame.id, face.idx, frame.v)} alt="face, frame {frame.burst.position}" loading="lazy" />
                <div class="face-badges">
                  <span class="badge {eyes(face).cls}">{eyes(face).text}</span>
                  {#if face.smile !== null}<span class="badge">smile {Math.round(face.smile * 100)}%</span>{/if}
                  {#if face.sharpness !== null}<span class="badge" title="blur at the eyes (lower is sharper)">blur {face.sharpness.toFixed(2)}</span>{/if}
                </div>
              {:else}
                <span class="muted">not found</span>
              {/if}
            </div>
          {/each}
        </div>
      {/each}
    </div>
  {/if}
  <p class="hint muted"><kbd>←</kbd><kbd>→</kbd> choose · <kbd>P</kbd> pick (rejects the others) · <kbd>Shift</kbd>+<kbd>P</kbd> keep this one too · <kbd>X</kbd> reject · <kbd>C</kbd> back</p>
</div>

<style>
  .compare { height: 100%; overflow: auto; padding: 8px; }
  .frames, .strip { display: grid; grid-template-columns: repeat(var(--cols), minmax(0, 1fr)); gap: 8px; }
  .strip { grid-template-columns: 80px repeat(var(--cols), minmax(0, 1fr)); margin-top: 8px; }
  .frame { padding: 0; background: #000; border: 2px solid transparent; border-radius: 6px; overflow: hidden; text-align: left; }
  .frame.active, .cell.active { border-color: var(--accent); }
  .frame.reject img { opacity: 0.5; }
  .frame img { width: 100%; aspect-ratio: 3 / 2; object-fit: contain; display: block; background: #000; }
  .label { display: flex; gap: 4px; align-items: center; padding: 4px 6px; }
  .issues { padding: 0 6px 6px; font-size: 12px; }
  .who { align-self: center; font-size: 12px; }
  .cell { position: relative; border: 2px solid transparent; border-radius: 6px; background: #000; min-height: 120px; display: flex; align-items: center; justify-content: center; overflow: hidden; }
  .cell img { width: 100%; height: 220px; object-fit: contain; display: block; }
  .face-badges { position: absolute; bottom: 4px; left: 4px; display: flex; gap: 3px; flex-wrap: wrap; }
  .hint { margin-top: 10px; }
</style>
