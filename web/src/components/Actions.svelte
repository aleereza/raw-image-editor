<script lang="ts">
  import type { Photo } from "../lib/api";
  import { burstOf, ui, type CullActions } from "../lib/state.svelte";

  let { photo, act, onhide }: { photo: Photo | null; act: CullActions; onhide: () => void } = $props();

  const compare = $derived(ui.mode === "compare");
  const frames = $derived(photo ? burstOf(photo, ui.photos) : []);
  const best = $derived(frames.length > 1 ? frames.find((p) => p.burst.rank === 1) : undefined);

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
    <h3>{compare ? "This frame" : "This photo"}</h3>
    {#if compare}
      <button class="big keep" class:on={photo?.decision === "keep"} onclick={run(act.keep)} disabled={!photo}>
        <span>✓ Pick this frame</span><kbd>P</kbd>
      </button>
      <p class="note">Keeps it and rejects the burst's other frames.</p>
      <button onclick={run(act.keepToo)} disabled={!photo}><span>✓ Keep this one too</span><kbd>⇧P</kbd></button>
    {:else}
      <button class="big keep" class:on={photo?.decision === "keep"} onclick={run(act.keep)} disabled={!photo}>
        <span>✓ Keep</span><kbd>P</kbd>
      </button>
    {/if}
    <button class="big reject" class:on={photo?.decision === "reject"} onclick={run(act.reject)} disabled={!photo}>
      <span>✗ Reject</span><kbd>X</kbd>
    </button>
    <button onclick={run(act.clear)} disabled={!photo?.decision}><span>Clear my decision</span><kbd>U</kbd></button>
    {#if ui.mode === "grid" && frames.length > 1}
      <p class="note">On a stacked burst these apply to the frame shown; Compare picks among all of its frames.</p>
    {/if}
  </section>

  <section>
    <h3>Burst</h3>
    <button class:on={compare} onclick={run(act.compare)} disabled={!photo}>
      <span>{compare ? "Back to the loupe" : "Compare its frames"}</span><kbd>C</kbd>
    </button>
    <button onclick={run(act.acceptPick)} disabled={!best}>
      <span>Accept the app's pick{best ? ` (#${best.burst.position})` : ""}</span><kbd>A</kbd>
    </button>
  </section>

  <section>
    <h3>View</h3>
    <button onclick={run(act.loupe)}>
      <span>{ui.mode === "grid" ? "Open in the loupe" : "Back to the grid"}</span><kbd>Enter</kbd>
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
      <button onclick={run(act.up)} title="↑">{ui.mode === "grid" ? "↑ Row up" : "↑ Burst"}</button>
      <button onclick={run(act.down)} title="↓">{ui.mode === "grid" ? "↓ Row down" : "↓ Burst"}</button>
    </div>
    <button onclick={run(act.nextPile)}><span>Next pile</span><kbd>Tab</kbd></button>
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
