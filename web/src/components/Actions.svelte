<script lang="ts">
  import type { Photo } from "../lib/api";
  import { burstOf, markOf, ui, type CullActions } from "../lib/state.svelte";

  let { photo, act, onhide }: { photo: Photo | null; act: CullActions; onhide: () => void } = $props();

  const compare = $derived(ui.mode === "compare");
  const frames = $derived(photo ? burstOf(photo, ui.photos) : []);
  const burst = $derived(frames.length > 1);
  const best = $derived(burst ? frames.find((p) => p.burst.rank === 1) : undefined);
  // What the first section acts on: a single photo, a whole burst (its tile in the grid), or one frame of a burst.
  const scope = $derived(!burst ? "photo" : ui.mode === "grid" ? "burst" : "frame");

  // A clicked button hands the focus back, so the keys keep working and Enter does not press it again.
  function run(action: () => void) {
    return (event: MouseEvent) => {
      (event.currentTarget as HTMLElement).blur();
      action();
    };
  }
</script>

<aside class="actions" aria-label="Actions">
  <div class="head">
    <h2>Actions</h2>
    <button class="hide" onclick={onhide} title="Hide this panel; the Actions button in the top bar brings it back">Hide</button>
  </div>

  <section>
    {#if scope === "burst" && photo}
      <h3>This burst</h3>
      <button class="big keep" class:on={photo.decision === "keep"} onclick={run(act.keep)}>
        <span>✓ Keep the frame shown (#{photo.burst.position})</span><kbd>P</kbd>
      </button>
      <p class="note">Picks it: the burst's other frames are rejected.</p>
      <button class="big reject" class:on={frames.every((f) => markOf(f).verdict === "reject")} onclick={run(act.reject)}>
        <span>✗ Reject every frame</span><kbd>X</kbd>
      </button>
      <button onclick={run(act.clear)} disabled={!frames.some((f) => f.decision)}><span>Clear the burst</span><kbd>U</kbd></button>
      <p class="note">Click the burst, or press <kbd>C</kbd>, to compare its frames and pick one.</p>
    {:else if scope === "frame"}
      <h3>This frame</h3>
      <button class="big keep" class:on={photo?.decision === "keep"} onclick={run(act.keep)} disabled={!photo}>
        <span>✓ Pick this frame</span><kbd>P</kbd>
      </button>
      <p class="note">Keeps it and rejects the burst's other frames, including one you kept before.</p>
      <button onclick={run(act.keepToo)} disabled={!photo}><span>✓ Keep this one too</span><kbd>⇧P</kbd></button>
      <button class="big reject" class:on={photo?.decision === "reject"} onclick={run(act.reject)} disabled={!photo}>
        <span>✗ Reject</span><kbd>X</kbd>
      </button>
      <button onclick={run(act.clear)} disabled={!photo?.decision}><span>Clear my decision</span><kbd>U</kbd></button>
    {:else}
      <h3>This photo</h3>
      <button class="big keep" class:on={photo?.decision === "keep"} onclick={run(act.keep)} disabled={!photo}>
        <span>✓ Keep</span><kbd>P</kbd>
      </button>
      <button class="big reject" class:on={photo?.decision === "reject"} onclick={run(act.reject)} disabled={!photo}>
        <span>✗ Reject</span><kbd>X</kbd>
      </button>
      <button onclick={run(act.clear)} disabled={!photo?.decision}><span>Clear my decision</span><kbd>U</kbd></button>
    {/if}
  </section>

  <section>
    <h3>Burst</h3>
    <button class:on={compare} onclick={run(act.compare)} disabled={!burst}>
      <span>{compare ? (ui.compareFrom === "loupe" ? "Back to the loupe" : "Back to the grid") : "Compare its frames"}</span><kbd>C</kbd>
    </button>
    <button onclick={run(act.acceptPick)} disabled={!best}>
      <span>Accept the app's pick{best ? ` (#${best.burst.position})` : ""}</span><kbd>A</kbd>
    </button>
  </section>

  <section>
    <h3>View</h3>
    <button onclick={run(act.open)} disabled={ui.mode === "grid" && !photo}>
      <span>{ui.mode !== "grid" ? "Back to the grid" : burst ? "Open burst compare" : "Open in the loupe"}</span><kbd>Enter</kbd>
    </button>
    <button class:on={ui.heat} onclick={run(act.focusMap)} disabled={!photo}><span>Focus map</span><kbd>F</kbd></button>
    <button class:on={ui.zoom} onclick={run(act.zoom)} disabled={!photo}><span>100 % on the eyes</span><kbd>Z</kbd></button>
  </section>

  <section>
    <h3>Move</h3>
    <div class="pair">
      <button onclick={run(act.previous)} title="Previous (←)">← Previous</button>
      <button onclick={run(act.next)} title="Next (→)">Next →</button>
    </div>
    <div class="pair">
      {#if ui.mode === "grid"}
        <button onclick={run(act.up)} title="↑">↑ Row up</button>
        <button onclick={run(act.down)} title="↓">↓ Row down</button>
      {:else if compare}
        <button onclick={run(act.up)} title="↑">↑ Burst</button>
        <button onclick={run(act.down)} title="↓">↓ Burst</button>
      {:else}
        <button onclick={run(act.up)} title="↑: the previous photo or whole burst">↑ Previous tile</button>
        <button onclick={run(act.down)} title="↓: the next photo or whole burst">↓ Next tile</button>
      {/if}
    </div>
    <button onclick={run(act.nextOpen)}><span>Next to decide</span><kbd>N</kbd></button>
    {#if !compare}
      <button onclick={run(act.previousScene)}><span>Previous scene</span><kbd>PgUp</kbd></button>
      <button onclick={run(act.nextScene)}><span>Next scene</span><kbd>PgDn</kbd></button>
    {/if}
    <button onclick={run(act.nextFilter)}><span>Next filter</span><kbd>Tab</kbd></button>
    {#if ui.filter !== null}
      <button onclick={run(act.clearFilter)}><span>Show every photo</span>{#if ui.mode === "grid"}<kbd>Esc</kbd>{/if}</button>
    {/if}
  </section>

  <section>
    <button onclick={run(act.help)}><span>Guide and keys</span><kbd>?</kbd></button>
  </section>
</aside>

<style>
  .actions {
    flex: 0 0 240px; width: 240px; overflow: hidden auto; padding: 8px 10px 14px; display: flex; flex-direction: column;
    gap: 14px; border-left: 1px solid var(--line); background: var(--panel);
  }
  .head { display: flex; align-items: center; justify-content: space-between; }
  h2 { font-size: 13px; margin: 0; }
  h3 { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); margin: 0 0 1px; }
  section { display: flex; flex-direction: column; gap: 5px; }
  section > button { display: flex; align-items: center; justify-content: space-between; gap: 8px; text-align: left; background: var(--bg); }
  button.on { border-color: var(--accent); background: #24324a; }
  .big { padding: 8px 10px; font-size: 15px; font-weight: 600; }
  .big.keep { background: var(--keep); border-color: var(--keep); color: #04170b; }
  .big.reject { background: var(--reject); border-color: var(--reject); color: #1d0505; }
  .big.on { box-shadow: 0 0 0 2px var(--text); }
  .big kbd { background: #0000001f; border-color: #00000033; color: inherit; }
  .note { margin: 0; font-size: 12px; color: var(--muted); }
  .pair { display: grid; grid-template-columns: 1fr 1fr; gap: 5px; }
  .pair button { background: var(--bg); padding: 5px 6px; }
  .hide { padding: 2px 8px; font-size: 12px; }
</style>
