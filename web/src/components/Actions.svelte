<script lang="ts">
  import type { Photo } from "../lib/api";
  import { burstOf, markOf, ui, type CullActions } from "../lib/state.svelte";

  let { photo, act, onhide }: { photo: Photo | null; act: CullActions; onhide: () => void } = $props();

  // The panel shows the same buttons, with the same labels and in the same places, for a photo, a burst and a frame of
  // one, in every view; a button that does nothing there is disabled (the owner asked for this on 2026-11-22).
  const compare = $derived(ui.mode === "compare");
  const frames = $derived(photo ? burstOf(photo, ui.photos) : []);
  const burst = $derived(frames.length > 1);
  const best = $derived(burst ? frames.find((p) => p.burst.rank === 1) : undefined);
  // In the grid a burst's tile stands for the whole burst: Reject and Clear act on every frame (ADR 0011).
  const wholeBurst = $derived(burst && ui.mode === "grid");
  const rejected = $derived(wholeBurst ? frames.every((f) => markOf(f).verdict === "reject") : photo?.decision === "reject");
  const decided = $derived(wholeBurst ? frames.some((f) => f.decision) : Boolean(photo?.decision));

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
    <h3>Decide</h3>
    <button class="big keep" class:on={photo?.decision === "keep"} onclick={run(act.keep)} disabled={!photo}>
      <span>✓ Keep</span><kbd>G</kbd>
    </button>
    <button class="big reject" class:on={rejected} onclick={run(act.reject)} disabled={!photo}>
      <span>✗ Reject</span><kbd>F</kbd>
    </button>
    <button onclick={run(act.clear)} disabled={!decided}><span>Clear my decision</span><kbd>U</kbd></button>
  </section>

  <section>
    <h3>Burst</h3>
    <button class:on={compare} onclick={run(act.compare)} disabled={!burst}><span>Compare frames</span><kbd>C</kbd></button>
    <button onclick={run(act.acceptPick)} disabled={!best}><span>Accept the app's pick</span><kbd>A</kbd></button>
  </section>

  <section>
    <h3>View</h3>
    <button class:on={ui.mode !== "grid"} onclick={run(act.open)} disabled={ui.mode === "grid" && !photo}>
      <span>Open</span><kbd>Enter</kbd>
    </button>
    <button class:on={ui.heat && ui.mode === "loupe"} onclick={run(act.focusMap)} disabled={!photo}><span>Focus map</span><kbd>M</kbd></button>
    <button class:on={ui.zoom} onclick={run(act.zoom)} disabled={!photo}><span>100 % on the eyes</span><kbd>Z</kbd></button>
  </section>

  <section>
    <h3>Move</h3>
    <div class="pair">
      <button onclick={run(act.previous)} disabled={!photo} title="Previous (←)">← Previous</button>
      <button onclick={run(act.next)} disabled={!photo} title="Next (→)">Next →</button>
    </div>
    <div class="pair">
      <button onclick={run(act.up)} disabled={!photo} title="Up (↑)">↑ Up</button>
      <button onclick={run(act.down)} disabled={!photo} title="Down (↓)">↓ Down</button>
    </div>
    <button onclick={run(act.nextOpen)}><span>Next to decide</span><kbd>N</kbd></button>
    <button onclick={run(act.previousScene)} disabled={compare || !photo}><span>Previous scene</span><kbd>PgUp</kbd></button>
    <button onclick={run(act.nextScene)} disabled={compare || !photo}><span>Next scene</span><kbd>PgDn</kbd></button>
    <button onclick={run(act.nextFilter)}><span>Next filter</span><kbd>Tab</kbd></button>
    <button onclick={run(act.clearFilter)} disabled={ui.filter === null}><span>Show every photo</span></button>
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
  .pair { display: grid; grid-template-columns: 1fr 1fr; gap: 5px; }
  .pair button { background: var(--bg); padding: 5px 6px; }
  .hide { padding: 2px 8px; font-size: 12px; }
</style>
