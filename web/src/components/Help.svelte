<script lang="ts">
  import { blurColour } from "../lib/focus";
  import Mark from "./Mark.svelte";

  let { onclose }: { onclose: () => void } = $props();
  let tab = $state<"guide" | "keys">("guide");

  // Key, then what it does in the grid, the loupe and burst compare (ADR 0011).
  const keys: [string, string, string, string][] = [
    ["← →", "previous / next tile", "previous / next photo; a burst opens in compare", "previous / next frame, then the previous / next tile"],
    ["↑ ↓", "the tile in the row above / below", "previous / next tile", "previous / next tile"],
    ["PgUp PgDn", "previous / next scene", "previous / next scene", "previous / next scene"],
    ["N  Shift+N", "next / previous tile to decide", "next / previous tile to decide", "next / previous tile to decide"],
    ["Enter", "a photo: loupe; a burst: compare", "back to the grid", "back to the grid"],
    ["C", "a burst: compare", "a burst's frame: compare", "this frame in the loupe"],
    ["Esc", "show every photo (turns the filter off)", "back to the grid", "back to the grid"],
    ["Mouse back", "like Esc; with every photo shown, back to the folders", "like Esc", "like Esc"],
    ["Tab  Shift+Tab", "next / previous filter", "next / previous filter", "next / previous filter"],
    ["G", "a photo: keep; a burst: pick the frame shown", "a photo: keep; a burst's frame: pick it", "pick this frame"],
    ["F", "a photo: reject; a burst: reject every frame", "reject", "reject this frame"],
    ["U", "a photo: clear; a burst: clear every frame", "clear your decision", "clear this frame"],
    ["A", "accept the app's pick for the burst", "the same", "the same"],
    ["M", "focus map, in the loupe", "focus map, faces and AF point", "focus map, in the loupe"],
    ["Z", "100% on the eyes or AF point, in the loupe", "100% on the eyes or AF point; drag or scroll to move", "100%, in the loupe"],
    ["D", "flip and zoom timings", "the same", "the same"],
    ["?", "this guide", "the same", "the same"],
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
      <h3>Every photo, by scene</h3>
      <p>The grid shows every photo of the folder, grouped by scene in shooting order. A single photo is one tile, and a burst is one tile for all of its frames. A folder opens on the first photo or burst still to decide.</p>

      <h3>Marks: what Done culling does</h3>
      <table>
        <tbody>
          <tr><td><Mark mark={{ verdict: "keep", by: "you" }} /> <Mark mark={{ verdict: "reject", by: "you" }} /></td><td>you kept it / you rejected it</td></tr>
          <tr><td><Mark mark={{ verdict: "keep", by: "app" }} /> <Mark mark={{ verdict: "reject", by: "app" }} /></td><td>the app keeps it / rejects it on its own; Done culling does that unless you change it</td></tr>
          <tr><td><Mark mark={{ verdict: "open", by: null }} /></td><td>to decide: nobody has decided yet; Done culling keeps it</td></tr>
        </tbody>
      </table>
      <p class="muted">Rejected photos are dimmed, whoever rejected them. <kbd>U</kbd> clears your decision: the photo goes back to the app's mark, or to open.</p>

      <h3>What the app decides on its own</h3>
      <dl>
        <dt>Rejected by the app</dt>
        <dd>Clear technical failures (a missed focus, a blurred subject, a very dark frame), and near-copies of a better frame in the same burst. At Done culling they go to the drive's trash unless you <b>keep</b> them.</dd>
        <dt>Kept by the app</dt>
        <dd>Sharp photos that aren't part of a burst and aren't dark. They are kept unless you reject them.</dd>
      </dl>
      <p>The app does this only for the kinds of decision switched to automatic, and only when it is sure. Its marks are outlined. Everything else is open until you decide, and open photos are kept.</p>

      <h3>Filters</h3>
      <p>The chips in the top bar narrow the grid: <b>To decide</b> (photos and bursts nobody has decided yet), <b>Rejected by the app</b> and <b>Kept by the app</b>, each with how many tiles it shows. A burst shows when any of its frames matches. Click the chip again, or press <kbd>Esc</kbd> in the grid, to see every photo; <kbd>Tab</kbd> goes through the filters.</p>

      <h3>Signs on a photo</h3>
      <table>
        <tbody>
          <tr><td><span class="frame selected"></span></td><td>the selected photo</td></tr>
          <tr><td><span class="frame dimmed"></span></td><td>rejected, by you or by the app</td></tr>
          <tr>
            <td><span class="frame stack"><span class="dots"><i class="keep you"></i><i class="reject app"></i><i class="reject you"></i></span></span></td>
            <td>a burst: the edges of more photos behind it, and one dot per frame in shooting order with its mark, followed by the counts ("1 keep · 2 reject"); bursts of more than 8 frames show the counts only</td>
          </tr>
          <tr><td><span class="badge keep">sharp</span></td><td>the subject is in focus: the eyes of the face the camera focused on, else the AF point, else the sharpest detail</td></tr>
          <tr><td><span class="badge reject">soft</span></td><td>the subject isn't sharp</td></tr>
          <tr><td><span class="badge reject">missed</span></td><td>the background is sharper than the subject: focus landed elsewhere</td></tr>
          <tr><td><span class="badge reject">motion</span></td><td>the subject is blurred in one direction, or the shutter was slower than about 1/(1.5 × focal length)</td></tr>
          <tr><td><span class="badge reject">shake</span></td><td>the whole frame is blurred in one direction</td></tr>
          <tr><td><span class="badge warn">eyes 2</span></td><td>two people blink, compared with their own other frames in the burst</td></tr>
          <tr><td><span class="badge warn">clip</span></td><td>over 3% of the frame is blown-out highlights</td></tr>
          <tr><td><span class="badge">best</span></td><td>the app's pick in its burst</td></tr>
        </tbody>
      </table>
      <p class="muted">No focus sign means there wasn't enough detail to measure. Hover over a photo to read the app's reason.</p>

      <h3>Bursts</h3>
      <p>A burst's tile shows its kept frame (the app's best of them when several are kept), else the app's best frame, and while you look through it, the frame you selected. Its mark is ✓ once a frame is kept and none is open, ✗ when every frame is rejected, and open otherwise, for example with one frame rejected and two open. In the grid, <kbd>G</kbd> picks the frame shown, <kbd>F</kbd> rejects every frame and <kbd>U</kbd> clears every frame. A click selects the burst, as it does a single photo; a double-click, <kbd>Enter</kbd> or <kbd>C</kbd> opens burst compare.</p>

      <h3>The loupe and burst compare: one walk</h3>
      <p>Opened from the grid, the loupe and burst compare walk through the folder in shooting order: a single photo shows in the loupe and a burst in burst compare, which opens by itself when the walk reaches the burst. <kbd>←</kbd> <kbd>→</kbd> go photo by photo, and through a burst frame by frame, entering it at its first frame going forward and at its last going back. <kbd>↑</kbd> <kbd>↓</kbd>, <kbd>N</kbd> and <kbd>PgUp</kbd> <kbd>PgDn</kbd> go to the previous or next photo or burst, the next one still to decide and the next scene, the same in both. In compare, <kbd>C</kbd> shows the selected frame in the loupe, as <kbd>M</kbd> and <kbd>Z</kbd> do; there <kbd>←</kbd> <kbd>→</kbd> go on frame by frame until the walk reaches another burst. <kbd>Esc</kbd> goes back to the grid.</p>

      <h3>Burst compare <kbd>C</kbd></h3>
      <p>Every frame of the burst side by side, each with its mark and its problems ("eyes closed, 85% as sharp as the sharpest frame"). Below them, one strip per person seen in at least two frames, followed by where they are in the frame, never by who they are: eyes open or closed, the smile, and the blur at the eyes (lower is sharper). Faces seen in only one frame, or small in the background, fold away under Other faces.</p>
      <p><kbd>G</kbd> picks a frame: it is kept, and the burst's open frames become your rejects. Frames you kept stay kept, so <kbd>G</kbd> on another frame keeps it too; to move the keep instead, press <kbd>F</kbd> on the frame you kept before. Frames the app already rejects on its own keep the app's mark; Done culling rejects them anyway. Compare stays on the frame so you see the result: <kbd>→</kbd> goes on through the frames and past the last one, and <kbd>↓</kbd> skips to the next photo or burst.</p>

      <h3>The bottom bar</h3>
      <p>The file (<span class="muted">+ JPG</span> when the camera's JPEG is paired with it), its mark in words ("kept by you", "rejected by the app", "to decide"), then the app's suggestion, such as <b class="reject">reject</b> <span class="muted">(technical, 92% reject)</span>, and its reason. The kind says why: <b>technical</b> (focus or exposure), <b>duplicates</b> (almost the same as a better frame), <b>eyes</b>, <b>expression</b> (not the burst's best moment, or "best of 7") or <b>composition</b> (a face cut by the edge). The percentage is how likely you are to reject it. On the right: the frame's place in its burst and the app's rank for it ("burst #3/7, rank 2"), the exposure, and <span class="badge">RT edits</span> when RawTherapee already has edits for the photo; they are kept. For a frame of a burst, a second line gives every frame's mark: "#2 kept by you; #1 rejected by the app; #3, #4 rejected by you".</p>

      <h3>Focus map <kbd>M</kbd></h3>
      <p>
        <span class="swatch" style:background={blurColour(0.3, 0.9)}></span> sharp
        <span class="swatch" style:background={blurColour(1, 0.9)}></span> a little soft
        <span class="swatch" style:background={blurColour(2, 0.9)}></span> blurred.
        Faces get <span class="line blue"></span> blue boxes, <span class="line yellow"></span> yellow when the eyes look closed; eyes are <span class="dot"></span> pink dots; the camera's AF point is a <span class="line dashed"></span> dashed white box (its position is estimated).
      </p>

      <h3>Done culling</h3>
      <p>Rejects move to the drive's trash with their camera JPEGs and sidecars; nothing is deleted. Open photos are kept, and the dialog says how many photos are still open and how many bursts have no keep yet. The folders page can restore rejects, and only you empty the trash.</p>
      <p class="muted">Every key has a button in the Actions panel on the right.</p>
    {:else}
      <table class="keys">
        <thead>
          <tr><th>Key</th><th>Grid</th><th>Loupe</th><th>Burst compare</th></tr>
        </thead>
        <tbody>
          {#each keys as [key, grid, loupe, compare]}
            <tr><td><kbd>{key}</kbd></td><td>{grid}</td><td>{loupe}</td><td>{compare}</td></tr>
          {/each}
        </tbody>
      </table>
      <p class="muted">A tile is a single photo, or a whole burst. The loupe and burst compare are one walk in shooting order: a single photo shows in the loupe and a burst in compare, and a move onto another tile switches between them by itself. <kbd>→</kbd> enters a burst at its first frame and <kbd>←</kbd> at its last. A burst's frames shown in the loupe with <kbd>C</kbd>, <kbd>M</kbd> or <kbd>Z</kbd> go on one by one there until the walk reaches another burst.</p>
      <p class="muted">After <kbd>G</kbd> or <kbd>F</kbd> the grid moves to the next tile and the loupe on as <kbd>→</kbd> does, to the next frame of a burst or the next tile, once you answer or skip the question when there is one. Burst compare stays on the frame.</p>
      <p class="muted">Mouse: a click selects a photo or a burst, and a double-click opens it: a photo in the loupe, a burst in burst compare. In compare, a double-click picks a frame. The mouse's back button (or the browser's Back, or <kbd>Alt</kbd>+<kbd>←</kbd>) does what <kbd>Esc</kbd> does, one step at a time, and from the grid showing every photo it goes back to the folders.</p>
      <p class="muted">When you overrule the app on one photo, the bar at the bottom asks why: <kbd>1</kbd> blur · <kbd>2</kbd> eyes · <kbd>3</kbd> expression · <kbd>4</kbd> duplicate · <kbd>5</kbd> composition · <kbd>6</kbd> exposure (too dark or too bright), or Skip. It is optional. The photo stays on screen until you answer, and then the view moves on; any other key skips the question and does its own job.</p>
      <p class="muted">Rejects are only flags until you press Done culling; then they go to the drive's trash, which only you empty.</p>
    {/if}
  </div>
</div>

<style>
  .overlay { position: fixed; inset: 0; background: #000a; display: flex; align-items: center; justify-content: center; z-index: 10; }
  .help {
    background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 12px 22px 16px;
    width: min(860px, calc(100vw - 32px)); max-height: 86vh; overflow-y: auto;
  }
  .tabs { display: flex; gap: 6px; align-items: center; position: sticky; top: -12px; background: var(--panel); padding: 4px 0 8px; }
  .tabs .active { border-color: var(--accent); background: #24324a; }
  .spacer { flex: 1; }
  h3 { font-size: 14px; margin: 16px 0 6px; }
  p { margin: 6px 0; line-height: 1.45; }
  dl { margin: 0; display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; }
  dt { font-weight: 600; }
  dd { margin: 0; line-height: 1.45; }
  td, th { padding: 3px 12px 3px 0; vertical-align: top; line-height: 1.4; text-align: left; }
  td:first-child { white-space: nowrap; }
  th { font-size: 12px; color: var(--muted); font-weight: 600; }
  .keys td:first-child kbd { white-space: pre; }
  b.reject { color: var(--reject); }
  .frame { display: inline-block; position: relative; width: 26px; height: 18px; border-radius: 4px; background: #555; vertical-align: middle; }
  .frame.selected { border: 2px solid var(--accent); }
  .frame.dimmed { opacity: 0.4; }
  .frame.stack { width: 44px; height: 26px; margin-top: 8px; }
  .frame.stack::before, .frame.stack::after {
    content: ""; position: absolute; border: 1px solid #77778a; border-bottom: none; border-radius: 4px 4px 0 0;
  }
  .frame.stack::before { left: 4px; right: 4px; top: -4px; height: 3px; }
  .frame.stack::after { left: 8px; right: 8px; top: -7px; height: 3px; }
  .dots { position: absolute; top: 3px; right: 3px; display: flex; gap: 2px; padding: 1px 3px; border-radius: 6px; background: #000a; }
  .dots i { width: 6px; height: 6px; border-radius: 50%; box-sizing: border-box; border: 1.5px solid #c4c4cc; }
  .dots i.keep.you { background: var(--keep); border-color: var(--keep); }
  .dots i.reject.you { background: var(--reject); border-color: var(--reject); }
  .dots i.reject.app { border-color: var(--reject); }
  .swatch { display: inline-block; width: 18px; height: 10px; vertical-align: middle; margin-left: 4px; }
  .line { display: inline-block; width: 16px; height: 10px; vertical-align: middle; border: 2px solid; }
  .line.blue { border-color: #6aa2ff; }
  .line.yellow { border-color: #e3b341; }
  .line.dashed { border: 2px dashed #fff; }
  .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: #ff5cf0; vertical-align: middle; }
</style>
