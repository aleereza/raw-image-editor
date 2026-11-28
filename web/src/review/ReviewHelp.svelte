<script lang="ts">
  let { onclose }: { onclose: () => void } = $props();
  let tab = $state<"guide" | "keys">("guide");

  const keys: [string, string][] = [
    ["← →  ↑ ↓", "previous / next photo"],
    ["N", "next photo that needs a look"],
    ["Tab", "switch between Needs a look and All"],
    ["\\", "toggle before (as shot) / after (edited)"],
    ["Z", "zoom in / fit (drag or scroll to pan)"],
    ["[  ]", "exposure −/+ 0.15 EV (Shift+click button: ±0.45 EV)"],
    [",  .  /  <  >", "cooler / warmer (±10 mired, or ±50 mired with < >)"],
    ["{  }", "turn clockwise / counter-clockwise (±0.1°; 0.5° buttons on panel)"],
    ["A", "apply the agent's full suggested value and lock it"],
    ["R", "reset the chosen chip's group to as shot (and lock it)"],
    ["L", "lock / unlock the chosen chip's group"],
    ["S", "apply the chosen chip's group to the whole scene"],
    ["G", "open the look lab"],
    ["E", "write the managed sidecar and open in RawTherapee"],
    ["Space", "mark seen and jump to the next photo that needs a look"],
    ["Esc", "close this guide, the look lab, or exit zoom"],
    ["?", "this guide"],
  ];
</script>

<div class="overlay" role="presentation" onclick={(event) => event.target === event.currentTarget && onclose()}>
  <div class="help" role="dialog" aria-label="Review guide and keys">
    <div class="tabs">
      <button class:active={tab === "guide"} onclick={() => (tab = "guide")}>Guide</button>
      <button class:active={tab === "keys"} onclick={() => (tab = "keys")}>Keys</button>
      <span class="spacer"></span>
      <button onclick={onclose} title="Close (Esc)">Close</button>
    </div>

    {#if tab === "guide"}
      <h3>An exception queue, not a manual editor</h3>
      <p>
        Every kept photo is edited automatically (straightening, white balance, exposure, colour and noise reduction),
        harmonized across each scene, and checked by QA. Most photos should need nothing from you: this page puts the
        uncertain ones first so you can skim, nudge or fix them, and export.
      </p>

      <h3>Top bar</h3>
      <dl>
        <dt>Needs a look / All <kbd>Tab</kbd></dt>
        <dd>Switches between just the flagged exceptions and every kept photo.</dd>
        <dt>untouched X %</dt>
        <dd>The share of edited photos you haven't had to change — tracks how well the agents are doing (target: 70 % or more).</dd>
        <dt>Edit again</dt>
        <dd>Re-runs the edit agents on the kept photos while keeping everything you changed or locked.</dd>
        <dt>Export</dt>
        <dd>Renders full-size JPEGs into <code>jpg/</code> inside the shoot folder (with EXIF copied from the NEF) and writes the managed <code>.pp3</code> sidecars.</dd>
      </dl>

      <h3>Left strip</h3>
      <p>
        Thumbnails in the current filter, badged <span class="badge warn">needs a look</span> when flagged or
        <span class="badge keep">seen</span> once you mark them reviewed.
      </p>

      <h3>The photo</h3>
      <p>
        Shows the RawTherapee-rendered <b>edited</b> preview. Press <kbd>\</kbd> (or click the top-left <b>edited</b>
        pill) to toggle <b>as shot</b> before/after. Press <kbd>Z</kbd> or double-click to zoom in, then drag or scroll
        to pan around; toggling <kbd>\</kbd> while zoomed keeps your place.
      </p>

      <h3>Agent chips &amp; reasons (below the photo)</h3>
      <p>
        Each chip is one edit agent (<b>Straighten</b>, <b>White balance</b>, <b>Exposure</b>, <b>Colour</b>,
        <b>Detail</b>) showing what it chose, its confidence %, and why. Its left border colour shows how confidence
        routing handled it:
      </p>
      <table>
        <tbody>
          <tr><td><span class="swatch apply">applied</span></td><td>confident — applied in full</td></tr>
          <tr><td><span class="swatch conservative">conservative</span></td><td>unsure — applied partway and flagged for a look</td></tr>
          <tr><td><span class="swatch as-shot">as shot</span></td><td>low confidence — left as shot and flagged for a look</td></tr>
          <tr>
            <td><span class="swatch locked">locked 🔒</span> <span class="badge">yours</span></td>
            <td>locked by you (or imported from your existing RawTherapee sidecar) — future edit runs keep it</td>
          </tr>
        </tbody>
      </table>
      <p>
        The <span class="warn-text">yellow bullets</span> under the chips list why the photo landed in <b>Needs a look</b>
        (an uncertain agent or a QA warning). <b>Clicking a chip</b> chooses which group <kbd>R</kbd>, <kbd>L</kbd> and
        <kbd>S</kbd> act on — the heading at the top of the right panel shows the active group.
      </p>

      <h3>Right panel (Actions)</h3>
      <dl>
        <dt>Nudges <kbd>[</kbd> <kbd>]</kbd> <kbd>,</kbd> <kbd>.</kbd> <kbd>{"{"}</kbd> <kbd>{"}"}</kbd></dt>
        <dd>
          Exposure (±0.15 EV), cooler/warmer (±10 mired; <kbd>&lt;</kbd> <kbd>&gt;</kbd> for ±50 mired) and turn
          (±0.1°, plus ±0.5° buttons on the panel; <kbd>Shift</kbd>+click any nudge button for larger steps). They work
          anytime and select that group automatically. The preview updates at once roughly in the browser, and
          RawTherapee swaps in the real render a moment after you pause.
        </dd>
        <dt>Apply <kbd>A</kbd> · Reset <kbd>R</kbd> · Lock <kbd>L</kbd> · Scene <kbd>S</kbd></dt>
        <dd>
          Act on the chosen chip's group. When an agent left a suggestion unapplied (as shot or halfway), <kbd>A</kbd>
          applies its full value in one click. Every nudge, apply or reset locks that group so future edit runs keep
          your choice. <kbd>S</kbd> copies that group to every other photo in the same scene and re-renders them in the
          background.
        </dd>
        <dt>Look lab <kbd>G</kbd></dt>
        <dd>
          Side-by-side variations (contrast, warmth, saturation and lift), like an eye test, to save reusable named
          looks and apply one to a photo or its whole scene.
        </dd>
        <dt>Open in RawTherapee <kbd>E</kbd></dt>
        <dd>Escape hatch: writes the managed <code>.pp3</code> sidecar and opens the NEF in the RawTherapee GUI for full manual edits.</dd>
        <dt>Seen, next <kbd>Space</kbd></dt>
        <dd>Marks the photo reviewed and jumps to the next one in <b>Needs a look</b>.</dd>
      </dl>

      <h3>Fast workflow</h3>
      <ol>
        <li>Start in <b>Needs a look</b> (the default).</li>
        <li>Glance at the photo (tap <kbd>\</kbd> for before/after if unsure) and skim the yellow reasons.</li>
        <li>Looks good? Press <kbd>Space</kbd> to mark it seen and jump to the next exception.</li>
        <li>
          Slightly off? Tap <kbd>[</kbd> <kbd>]</kbd> / <kbd>,</kbd> <kbd>.</kbd> / <kbd>{"{"}</kbd> <kbd>{"}"}</kbd>
          to nudge, or click a chip and press <kbd>R</kbd> to undo an agent's call. If the whole scene shares the issue,
          press <kbd>S</kbd>, then <kbd>Space</kbd>.
        </li>
        <li>Use <kbd>G</kbd> (Look lab) to set a scene look, or <kbd>E</kbd> for heavy manual work in RawTherapee.</li>
        <li>When <b>Needs a look</b> hits 0 (plus an optional <kbd>Tab</kbd> skim of <b>All</b>), click <b>Export</b>.</li>
      </ol>
    {:else}
      <table class="keys">
        <thead>
          <tr><th>Key</th><th>Action</th></tr>
        </thead>
        <tbody>
          {#each keys as [key, desc]}
            <tr><td><kbd>{key}</kbd></td><td>{desc}</td></tr>
          {/each}
        </tbody>
      </table>
      <p class="muted">
        Mouse: click a chip to choose what <kbd>R</kbd>, <kbd>L</kbd> and <kbd>S</kbd> act on; double-click the photo to
        zoom in on that spot (or back to fit); drag or scroll with the wheel or touchpad while zoomed to pan.
      </p>
      <p class="muted">
        Every change you make is locked automatically, so the next edit run keeps it. Nudges preview roughly at once
        and the real RawTherapee render replaces them a moment later.
      </p>
    {/if}
  </div>
</div>

<style>
  .overlay { position: fixed; inset: 0; background: #000a; display: flex; align-items: center; justify-content: center; z-index: 20; }
  .help {
    background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 12px 22px 16px;
    width: min(860px, calc(100vw - 32px)); max-height: 86vh; overflow-y: auto;
  }
  .tabs { display: flex; gap: 6px; align-items: center; position: sticky; top: -12px; background: var(--panel); padding: 4px 0 8px; }
  .tabs .active { border-color: var(--accent); background: #24324a; }
  .spacer { flex: 1; }
  h3 { font-size: 14px; margin: 16px 0 6px; }
  p { margin: 6px 0; line-height: 1.45; }
  ol { margin: 6px 0; padding-left: 20px; line-height: 1.5; }
  ol li { margin: 3px 0; }
  dl { margin: 0; display: grid; grid-template-columns: auto 1fr; gap: 6px 14px; }
  dt { font-weight: 600; white-space: nowrap; }
  dd { margin: 0; line-height: 1.45; }
  code { background: rgba(255, 255, 255, 0.08); padding: 1px 4px; border-radius: 3px; font-size: 12px; }
  td, th { padding: 4px 12px 4px 0; vertical-align: middle; line-height: 1.4; text-align: left; }
  td:first-child { white-space: nowrap; }
  th { font-size: 12px; color: var(--muted); font-weight: 600; }
  .keys td:first-child kbd { white-space: pre; }
  .swatch {
    display: inline-block; padding: 2px 8px; border-radius: 5px; border: 1px solid var(--line);
    border-left-width: 4px; background: var(--bg); font-size: 12px; font-weight: 600;
  }
  .swatch.apply { border-left-color: var(--keep); }
  .swatch.conservative { border-left-color: var(--warn); }
  .swatch.as-shot { border-left-color: var(--reject); }
  .swatch.locked { border-left-color: var(--accent); }
  .warn-text { color: var(--warn); font-weight: 600; }
  .badge.warn { background: var(--warn); color: #201800; }
  .badge.keep { background: var(--keep); color: #04170b; }
</style>
