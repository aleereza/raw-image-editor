<script lang="ts">
  import { markText, type Mark } from "../lib/state.svelte";

  // What Done culling will do with a photo: filled ✓ or ✗ for your decision, outlined for the app's own call, and an
  // empty ring while nobody has decided (ADR 0011).
  let { mark, size = 18 }: { mark: Mark; size?: number } = $props();
</script>

<span class="mark {mark.verdict} {mark.by ?? ''}" style:--size="{size}px" title={markText(mark)} role="img" aria-label={markText(mark)}
  >{mark.verdict === "keep" ? "✓" : mark.verdict === "reject" ? "✗" : ""}</span
>

<style>
  .mark {
    display: inline-flex; align-items: center; justify-content: center; flex: none; box-sizing: border-box;
    width: var(--size); height: var(--size); border-radius: 50%; border: 1.5px solid #c4c4cc;
    background: rgba(0, 0, 0, 0.6); color: #fff; font-size: calc(var(--size) * 0.62); font-weight: 700; line-height: 1;
  }
  .keep.you { background: var(--keep); border-color: var(--keep); color: #04170b; }
  .reject.you { background: var(--reject); border-color: var(--reject); color: #1d0505; }
  .keep.app { border-color: var(--keep); color: var(--keep); }
  .reject.app { border-color: var(--reject); color: var(--reject); }
</style>
