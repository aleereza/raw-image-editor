<script lang="ts">
  import type { Photo } from "../lib/api";

  let { photo }: { photo: Photo } = $props();
  const verdict = $derived(photo.focus.verdict);
  const focusClass = $derived(verdict === "sharp" ? "keep" : verdict && verdict !== "unknown" ? "reject" : "");
</script>

<div class="badges">
  {#if photo.decision}<span class="badge {photo.decision}">{photo.decision === "keep" ? "✓" : "✗"}</span>{/if}
  {#if verdict && verdict !== "unknown"}<span class="badge {focusClass}" title={photo.focus.reason}>{verdict}</span>{/if}
  {#if photo.closed}<span class="badge warn" title="eyes closed">eyes {photo.closed}</span>{/if}
  {#if photo.clip !== null && photo.clip > 0.03}<span class="badge warn" title="clipped highlights">clip</span>{/if}
  {#if photo.rating}<span class="badge">{"★".repeat(photo.rating)}</span>{/if}
  {#if photo.burst.size > 1 && photo.burst.rank === 1}<span class="badge" title="the app's pick in this burst">best</span>{/if}
</div>

<style>
  .badges { position: absolute; left: 4px; bottom: 4px; display: flex; gap: 3px; flex-wrap: wrap; pointer-events: none; }
</style>
