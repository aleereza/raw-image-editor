<script lang="ts">
  import { urls, type Analysis, type Face, type Photo } from "../lib/api";
  import { analysisFor, burstOf, markOf, markText, ui } from "../lib/state.svelte";
  import Mark from "./Mark.svelte";

  let { photo, onselect, onpick }: { photo: Photo; onselect: (id: number) => void; onpick: (p: Photo) => void } = $props();

  const frames = $derived(burstOf(photo, ui.photos));
  let analyses = $state<Record<number, Analysis>>({});

  // A double-click picks a frame only when both clicks land in compare: double-clicking a burst in the grid opens
  // compare on the first click, and its second click must not pick or select the frame that happens to be there.
  let armed: number | null = null;
  function click(event: MouseEvent, frame: Photo) {
    if (event.detail > 1 && armed !== frame.id) return;
    armed = frame.id;
    onselect(frame.id);
  }

  $effect(() => {
    for (const frame of frames) {
      if (!analyses[frame.id]) analysisFor(frame.id).then((a) => (analyses = { ...analyses, [frame.id]: a }));
    }
  });

  // One row per face track the landmarker read, one column per frame. The people the burst's ranking counts come
  // first; faces seen in only one frame, or small in the background, fold away under "Other faces" (ADR 0014). A
  // folder grouped before ADR 0014 lists no people, and then every track gets a row, as before.
  const faceTracks = $derived.by(() => {
    const ids = new Set<number>();
    for (const frame of frames) for (const face of analyses[frame.id]?.faces ?? []) if (face.track !== null && face.blink !== null) ids.add(face.track);
    return [...ids].sort((a, b) => a - b);
  });
  const personTracks = $derived.by(() => {
    const listed = frames.map((frame) => analyses[frame.id]?.burst.person_tracks).find((tracks) => Array.isArray(tracks));
    return listed ? faceTracks.filter((track) => listed.includes(track)) : faceTracks;
  });
  const otherTracks = $derived(faceTracks.filter((track) => !personTracks.includes(track)));

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
      {@const mark = markOf(frame)}
      <button
        class="frame"
        class:active={frame.id === photo.id}
        class:rejected={mark.verdict === "reject"}
        onclick={(event) => click(event, frame)}
        ondblclick={() => armed === frame.id && onpick(frame)}
      >
        <div class="shot">
          <img src={urls.screen(frame)} alt={frame.name} />
          <span class="corner"><Mark {mark} size={20} /></span>
        </div>
        <div class="label">
          <b>#{frame.burst.position}</b>
          <span class="said">{markText(mark)}</span>
          {#if frame.burst.rank === 1}<span class="badge" title="the app's pick">★ best</span>{/if}
          {#if frame.focus.verdict}<span class="badge">{frame.focus.verdict}</span>{/if}
          {#if frame.closed}<span class="badge warn">{frame.closed} blinking</span>{/if}
        </div>
        {#if frame.burst.issues.length}<div class="issues muted">{frame.burst.issues.join(", ")}</div>{/if}
      </button>
    {/each}
  </div>
  {#snippet strip(track: number, who: string)}
    <div class="strip">
      <div class="who muted">{who}</div>
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
  {/snippet}
  {#if faceTracks.length}
    <div class="strips">
      {#each personTracks as track, row (track)}
        {@render strip(track, `person ${row + 1}`)}
      {/each}
      {#if otherTracks.length}
        <details class="others">
          <summary class="muted">Other faces ({otherTracks.length}): seen in one frame only, or small in the background</summary>
          {#each otherTracks as track (track)}
            {@render strip(track, "other face")}
          {/each}
        </details>
      {/if}
    </div>
  {/if}
  <p class="hint muted">
    <kbd>←</kbd><kbd>→</kbd> frame, then on past the burst · <kbd>↑</kbd><kbd>↓</kbd> photo or burst · <kbd>N</kbd> next to decide · <kbd>G</kbd> keep
    it and reject the open frames · <kbd>F</kbd> reject · <kbd>U</kbd> clear · <kbd>C</kbd> this frame in the loupe · <kbd>Esc</kbd> back to the grid
  </p>
</div>

<style>
  .compare { height: 100%; overflow: auto; padding: 8px; }
  .frames, .strip { display: grid; grid-template-columns: repeat(var(--cols), minmax(0, 1fr)); gap: 8px; }
  .strip { grid-template-columns: 80px repeat(var(--cols), minmax(0, 1fr)); margin-top: 8px; }
  .frame { padding: 0; background: #000; border: 2px solid transparent; border-radius: 6px; overflow: hidden; text-align: left; }
  .frame.active, .cell.active { border-color: var(--accent); }
  .frame.rejected img { opacity: 0.45; }
  .shot { position: relative; }
  .frame img { width: 100%; aspect-ratio: 3 / 2; object-fit: contain; display: block; background: #000; }
  .corner { position: absolute; top: 6px; left: 6px; display: flex; pointer-events: none; }
  .label { display: flex; gap: 4px; align-items: center; flex-wrap: wrap; padding: 4px 6px; }
  .said { color: var(--muted); font-size: 12px; margin-right: 2px; }
  .issues { padding: 0 6px 6px; font-size: 12px; }
  .who { align-self: center; font-size: 12px; }
  .cell { position: relative; border: 2px solid transparent; border-radius: 6px; background: #000; min-height: 120px; display: flex; align-items: center; justify-content: center; overflow: hidden; }
  .cell img { width: 100%; height: 220px; object-fit: contain; display: block; }
  .face-badges { position: absolute; bottom: 4px; left: 4px; display: flex; gap: 3px; flex-wrap: wrap; }
  .others { margin-top: 10px; }
  .others summary { cursor: pointer; font-size: 12px; }
  .others .strip { opacity: 0.75; }
  .hint { margin-top: 10px; }
</style>
