<script lang="ts">
  import { blurColour } from "../lib/focus";

  let { onclose }: { onclose: () => void } = $props();
  let tab = $state<"guide" | "keys">("guide");

  const keys: [string, string][] = [
    ["← →", "previous / next photo (in the grid: burst; in compare: frame)"],
    ["↑ ↓", "previous / next burst (in the grid: row)"],
    ["Enter", "grid ↔ loupe"],
    ["Tab", "next pile (Shift+Tab: previous)"],
    ["P X U", "keep / reject / clear your decision (in burst compare, P picks: the rest are rejected; Shift+P keeps more)"],
    ["Z", "100% on the eyes or AF point; drag or scroll to move around"],
    ["F", "focus map, faces and AF point"],
    ["C", "burst compare with face strips"],
    ["A", "accept the app's pick for this burst"],
    ["Esc", "back"],
    ["D", "show flip and zoom timings"],
    ["?", "this guide"],
  ];
</script>

<div class="overlay" role="presentation" onclick={(event) => event.target === event.currentTarget && onclose()}>
  <div class="help" role="dialog" aria-label="Guide and keys">
    <div class="tabs">
      <button class:active={tab === "guide"} onclick={() => (tab = "guide")}>Guide</button>
      <button class:active={tab === "keys"} onclick={() => (tab = "keys")}>Keys</button>
      <span class="spacer"></span>
      <button onclick={onclose} title="Close (Esc)">Close</button>
    </div>

    {#if tab === "guide"}
      <h3>The three piles</h3>
      <dl>
        <dt>Your call</dt>
        <dd>The app isn't sure, or isn't allowed to decide on its own. It shows a suggestion, but nothing happens until you decide. Photos you leave undecided here are <b>kept</b>.</dd>
        <dt>Auto-reject</dt>
        <dd>Clear technical failures, and near-copies of a better frame in the same burst. At Done culling they go to the drive's trash unless you <b>keep</b> them.</dd>
        <dt>Auto-keep</dt>
        <dd>Sharp photos that aren't part of a burst. They are kept unless you reject them.</dd>
      </dl>
      <p class="muted">One burst can be split across the piles; <kbd>C</kbd> shows all of its frames.</p>

      <h3>Signs on a photo</h3>
      <table>
        <tbody>
          <tr><td><span class="frame selected"></span></td><td>the selected photo</td></tr>
          <tr><td><span class="frame kept"></span> <span class="frame dimmed"></span></td><td>you kept it / you rejected it</td></tr>
          <tr><td><span class="badge keep">✓</span> <span class="badge reject">✗</span></td><td>your keep / reject</td></tr>
          <tr><td><span class="badge keep">sharp</span></td><td>the subject is in focus: the eyes of the face the camera focused on, else the AF point, else the sharpest detail</td></tr>
          <tr><td><span class="badge reject">soft</span></td><td>the subject isn't sharp</td></tr>
          <tr><td><span class="badge reject">missed</span></td><td>the background is sharper than the subject: focus landed elsewhere</td></tr>
          <tr><td><span class="badge reject">motion</span></td><td>the subject is blurred in one direction, or the shutter was slower than about 1/(1.5 × focal length)</td></tr>
          <tr><td><span class="badge reject">shake</span></td><td>the whole frame is blurred in one direction</td></tr>
          <tr><td><span class="badge warn">eyes 2</span></td><td>two people blink, compared with their own other frames in the burst</td></tr>
          <tr><td><span class="badge warn">clip</span></td><td>over 3% of the frame is blown-out highlights</td></tr>
          <tr><td><span class="badge">best</span></td><td>the app's pick in its burst</td></tr>
          <tr><td><span class="stack">×6</span></td><td>six frames of one burst are in this pile, stacked; the one shown is the best of them</td></tr>
        </tbody>
      </table>
      <p class="muted">No focus sign means there wasn't enough detail to measure. Hover over a photo to read the app's reason.</p>

      <h3>The bottom bar</h3>
      <p>The file (<span class="muted">+ JPG</span> when the camera's JPEG is paired with it), your decision, then the app's suggestion, such as <b class="reject">reject</b> <span class="muted">(technical, 92% reject)</span>, and its reason. The kind says why: <b>technical</b> (focus or exposure), <b>duplicates</b> (almost the same as a better frame), <b>eyes</b>, <b>expression</b> (not the burst's best moment, or "best of 7") or <b>composition</b> (a face cut by the edge). The percentage is how likely you are to reject it. On the right: the frame's place in its burst and the app's rank for it ("burst #3/7, rank 2"), the exposure, and <span class="badge">RT edits</span> when RawTherapee already has edits for the photo; they are kept.</p>

      <h3>Focus map <kbd>F</kbd></h3>
      <p>
        <span class="swatch" style:background={blurColour(0.3, 0.9)}></span> sharp
        <span class="swatch" style:background={blurColour(1, 0.9)}></span> a little soft
        <span class="swatch" style:background={blurColour(2, 0.9)}></span> blurred.
        Faces get <span class="line blue"></span> blue boxes, <span class="line yellow"></span> yellow when the eyes look closed; eyes are <span class="dot"></span> pink dots; the camera's AF point is a <span class="line dashed"></span> dashed white box (its position is estimated).
      </p>

      <h3>Burst compare <kbd>C</kbd></h3>
      <p>Every frame of the burst side by side, from all three piles, each with its problems ("eyes closed, 85% as sharp as the sharpest frame"). Below them, one strip per person, followed by where they are in the frame, never by who they are: eyes open or closed, the smile, and the blur at the eyes (lower is sharper). <kbd>P</kbd> picks a frame and rejects the others; <kbd>Shift</kbd>+<kbd>P</kbd> keeps it as well.</p>

      <h3>Done culling</h3>
      <p>Rejects move to the drive's trash with their camera JPEGs and sidecars; nothing is deleted. The folders page can restore them, and only you empty the trash.</p>
      <p class="muted">Every key has a button in the Actions panel on the right.</p>
    {:else}
      <table>
        <tbody>
          {#each keys as [key, what]}
            <tr><td><kbd>{key}</kbd></td><td>{what}</td></tr>
          {/each}
        </tbody>
      </table>
      <p class="muted">When you overrule the app, a quick reason picker appears: <kbd>1</kbd> blur · <kbd>2</kbd> eyes · <kbd>3</kbd> expression · <kbd>4</kbd> duplicate · <kbd>5</kbd> composition. It is optional.</p>
      <p class="muted">Rejects are only flags until you press Done culling; then they go to the drive's trash, which only you empty.</p>
    {/if}
  </div>
</div>

<style>
  .overlay { position: fixed; inset: 0; background: #000a; display: flex; align-items: center; justify-content: center; z-index: 10; }
  .help {
    background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 12px 22px 16px;
    width: min(760px, calc(100vw - 32px)); max-height: 86vh; overflow-y: auto;
  }
  .tabs { display: flex; gap: 6px; align-items: center; position: sticky; top: -12px; background: var(--panel); padding: 4px 0 8px; }
  .tabs .active { border-color: var(--accent); background: #24324a; }
  .spacer { flex: 1; }
  h3 { font-size: 14px; margin: 16px 0 6px; }
  p { margin: 6px 0; line-height: 1.45; }
  dl { margin: 0; display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; }
  dt { font-weight: 600; }
  dd { margin: 0; line-height: 1.45; }
  td { padding: 3px 12px 3px 0; vertical-align: top; line-height: 1.4; }
  td:first-child { white-space: nowrap; }
  b.reject { color: var(--reject); }
  .frame { display: inline-block; width: 26px; height: 18px; border-radius: 4px; background: #555; vertical-align: middle; }
  .frame.selected { border: 2px solid var(--accent); }
  .frame.kept { box-shadow: inset 0 -4px 0 var(--keep); }
  .frame.dimmed { opacity: 0.45; }
  .stack { background: var(--accent); color: #0b1020; font-weight: 700; border-radius: 10px; padding: 0 7px; font-size: 12px; }
  .swatch { display: inline-block; width: 18px; height: 10px; vertical-align: middle; margin-left: 4px; }
  .line { display: inline-block; width: 16px; height: 10px; vertical-align: middle; border: 2px solid; }
  .line.blue { border-color: #6aa2ff; }
  .line.yellow { border-color: #e3b341; }
  .line.dashed { border: 2px dashed #fff; }
  .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: #ff5cf0; vertical-align: middle; }
</style>
