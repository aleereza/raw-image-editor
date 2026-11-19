<script lang="ts">
  let { onclose }: { onclose: () => void } = $props();
  const keys: [string, string][] = [
    ["← →", "previous / next photo (in the grid: burst)"],
    ["↑ ↓", "previous / next burst (in the grid: row)"],
    ["Enter", "grid ↔ loupe"],
    ["Tab", "next pile (Shift+Tab: previous)"],
    ["P X U", "keep / reject / unflag (in burst compare, P picks: the rest are rejected; Shift+P keeps more)"],
    ["1–5, 0", "rating / clear (written to the RawTherapee sidecar)"],
    ["Z", "100% on the eyes or AF point"],
    ["F", "focus heatmap, faces and AF point"],
    ["C", "burst compare with face strips"],
    ["A", "accept the app's pick for this burst"],
    ["Esc", "back"],
    ["D", "show flip and zoom timings"],
    ["?", "this help"],
  ];
</script>

<div class="overlay" role="presentation" onclick={onclose}>
  <div class="help" role="dialog" aria-label="Keyboard">
    <h2>Keyboard</h2>
    <table>
      <tbody>
        {#each keys as [key, what]}
          <tr><td><kbd>{key}</kbd></td><td>{what}</td></tr>
        {/each}
      </tbody>
    </table>
    <p class="muted">When you overrule the app, a quick reason picker appears: <kbd>1</kbd> blur · <kbd>2</kbd> eyes · <kbd>3</kbd> expression · <kbd>4</kbd> duplicate · <kbd>5</kbd> composition. It is optional.</p>
    <p class="muted">Rejects are only flags until you press Done culling; then they go to the drive's trash, which only you empty.</p>
  </div>
</div>

<style>
  .overlay { position: fixed; inset: 0; background: #000a; display: flex; align-items: center; justify-content: center; }
  .help { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 16px 22px; max-width: 640px; }
  h2 { margin: 0 0 10px; font-size: 16px; }
  td { padding: 3px 10px 3px 0; vertical-align: top; }
</style>
