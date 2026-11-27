# ADR 0011: The culling view: every photo, whole bursts, verdict marks, piles as filters

- **Status:** accepted
- **Date:** 2026-11-20 (after Phase 1)
- **Code:** `web/src/` (`App.svelte`, `lib/state.svelte.ts`, `components/`), `src/rie/cull/actions.py`, `src/rie/api/app.py`, `tests/test_cull.py`, `tests/test_api.py`

## Context

Phase 1's culling page showed one pile at a time, in three tabs: Your call (opened first), Auto-reject and Auto-keep. Each tab stacked the frames of a burst that were in that pile. The app sorts every frame on its own, so the piles split bursts: a burst's best frame sat in Your call while its near-copies sat in Auto-reject, one burst showed up as stacks in two or three tabs, and only burst compare showed it whole. A ✓ or ✗ badge showed the owner's decisions, but nothing showed what the app would do with a photo nobody had decided: a photo in Auto-reject and an undecided one in Your call looked the same.

After culling a first real shoot, the owner asked for every photo in one place, by scene, quick to review with the arrow keys, with each photo's verdict visible and each burst as one tile that opens burst compare. The owner settled the details: the piles become filters on that one view (there is no "All" tab), `Shift+P` still keeps a second frame of a burst, the marks and burst dots of the mockup are right, and rejecting every frame of a burst is an action of its own.

## Decision

### One view, with filters

- The culling page shows every present photo of the folder in one grid, grouped by scene in shooting order. A burst is one tile holding all of its present frames, whatever their piles. Scene headers count the photos and the tiles still to decide ("Scene 4 · 14 photos · 2 to decide").
- The pile tabs become three filter chips in the top bar, each counting the tiles it shows: To decide (open photos, see the marks below), Rejected by the app (the Auto-reject pile) and Kept by the app (the Auto-keep pile). At most one is on, and with none on, everything shows. A burst shows when any of its frames matches.
- `Tab` and `Shift+Tab` cycle off → To decide → Rejected by the app → Kept by the app → off. Clicking the chip that is on, or `Esc` in the grid, turns it off. Turning a filter on keeps the selection when its tile matches, and otherwise moves it to the filter's first tile.
- The two app filters follow the piles, so a photo the owner rescued from Auto-reject stays in Rejected by the app, with the owner's mark. To decide follows the marks: a tile leaves it once it is decided, but the selected tile stays until the selection moves on, so a photo doesn't vanish from under the cursor. When nothing is left, the filter says so.
- A folder opens with no filter, in the grid, on the first tile still to decide (else the first tile).

### Marks

Every photo carries one mark at the top left, saying what Done culling will do with it and who decided:

| Mark | Meaning | Comes from |
|---|---|---|
| filled ✓ or ✗ | the owner kept or rejected it | the owner's decision |
| outlined ✓ or ✗ | the app keeps or rejects it on its own; Done culling does that unless the owner changes it | no owner decision; pile Auto-keep or Auto-reject |
| empty ring | open: nobody has decided; Done culling keeps it | no owner decision; pile Your call, or not sorted yet |

Rejected photos are dimmed, whoever rejected them. The mark replaces the decision badge; the analysis badges (focus verdict, eyes, clipping, best) stay. Burst compare shows the mark on every frame, and the bottom bar says it in words ("kept by you", "rejected by the app", "to decide"). For a frame of a burst, the bottom bar's second line gives every frame's mark ("#2 kept by you; #1 rejected by the app; #3, #4 rejected by you").

### Bursts

- A burst's tile shows its kept frame (the best-ranked one when several are kept), else the app's best frame, and while one of its frames is selected, that frame. Two stacked edges behind the tile mark it as a burst, replacing the `×N` count.
- One dot per frame, in shooting order and in the frame's mark style, is followed by the counts ("1 keep · 2 reject", "1 reject · 3 open"). Bursts of more than eight frames show the counts only.
- The tile's own mark is ✓ once a frame is kept and none is open (filled when a keep is the owner's), ✗ when every frame is rejected (filled when any of those rejects is the owner's), and the empty ring otherwise.
- `G` on a frame picks it: the frame is kept and the burst's open frames become the owner's rejects, while the frames the owner kept or rejected stay as they are, so `G` on another frame adds a keep, and `F` on the old keep moves it. In the grid, `G` on a burst tile picks the frame it shows. (Until 2026-11-22 a pick also rejected the owner's earlier keeps, so picking another frame moved the keep, and `Shift+P` kept a frame without touching the others. The owner chose adding keeps, which leaves `Shift+P` nothing to do.)
- `F` on a burst tile in the grid rejects every frame, and `U` clears the owner's decision on every frame. In the loupe and in compare, `F` and `U` act on one frame. Whole-burst actions don't show the reason picker.
- A frame the app already rejects on its own (no owner decision, Auto-reject) keeps the app's call when the owner picks another frame or rejects the whole burst. Its final call is a reject either way, and the share of photos decided automatically stays right.
- A click on a tile selects it, a single photo or a burst alike, and a double-click opens it: a single photo in the loupe, a burst in burst compare. (The owner asked for this on 2026-11-20; a click on a burst first opened compare.) In compare, a double-click picks a frame only when both clicks land in compare, so double-clicking a burst tile in the grid never picks whichever frame ends up under the pointer.

### Keys

| Key | Grid | Loupe | Burst compare |
|---|---|---|---|
| ← → | previous / next tile | previous / next photo; a burst opens in compare | previous / next frame, then the previous / next tile |
| ↑ ↓ | the tile in the row above / below on screen, across scene breaks | previous / next tile | previous / next tile |
| PgUp PgDn | previous / next scene | previous / next scene | previous / next scene |
| N, Shift+N | next / previous tile to decide, going round at the ends | the same | the same |
| Enter | a photo: loupe; a burst: compare | back to the grid | back to the grid |
| C | a burst: compare | a burst's frame: compare | the selected frame in the loupe |
| Esc | turns the filter off | back to the grid | back to the grid |
| Tab, Shift+Tab | next / previous filter | next / previous filter | next / previous filter |
| G | a photo: keep; a burst: pick the frame shown | a photo: keep; a burst's frame: pick it | pick this frame |
| F | a photo: reject; a burst: reject every frame | reject | reject this frame |
| U | a photo: clear; a burst: clear every frame | clear | clear this frame |

`A`, `D` and `?` work as before, and `M` (the focus map) and `Z` open the photo or frame in the loupe. The loupe and burst compare are one walk in shooting order: a single photo shows in the loupe and a burst in compare, and a move that selects another tile switches between them by itself, as does a filter change. `→` enters a burst at its first frame and `←` at its last; in compare they go frame by frame, and past the burst's ends on to the next or previous tile. The moves by tile (`↑` `↓`, `N`, `PgUp` `PgDn`) select the tile's cover, as in the grid. A burst's frames shown in the loupe with `C`, `M` or `Z` stay in the loupe frame by frame until the walk enters another burst. After `G` or `F`, the grid moves to the next tile and the loupe on as `→` does, to the next frame of a burst or the next tile; burst compare stays on the frame, so the result shows, and `→` walks on through the frames while `↓` skips to the next tile. `[` and `]` stay free for Phase 2's exposure keys. The action panel has a button for every key. It looks the same for a photo, a burst tile and a frame, in every view: the same buttons and labels in the same places, without notes, and disabled where they do nothing (the owner asked for this on 2026-11-22). The guide (`?`) explains the marks, the filters and the burst tile.

The owner moved keep and pick from `P` to `G` and reject from `X` to `F` on 2026-11-22, and the focus map from `F` to `M` to make room; `P`, `Shift+P` and `X` do nothing now.

The owner made the loupe and burst compare one walk on 2026-11-27, after walking into bursts in the loupe without noticing them while culling a real shoot: `←` `→` went through a burst's frames as if they were single photos, and `G` on a frame picked it and jumped past the burst, so its other frames were rejected unseen. Until then, `↑` `↓` and `N` in compare moved between bursts only and `PgUp` `PgDn` did nothing there, `Esc` and `C` went back to wherever compare was opened from, and a filter change always fell back from compare to the loupe. The owner chose to keep `G` in compare on the frame rather than move on.

### Server

- `actions.pick` keeps the frame and rejects the burst's open frames, leaving the owner's keeps and rejects and the frames the app rejects on its own as they are, and `decision_log` records the pick with the value `reject_open`. Until 2026-11-22 it also rejected the owner's keeps (logged as `reject_others`), and with `keep_others` it left the other frames alone (logged as `keep_others`). It returns the picked frame, then the other frames whose decision changed.
- `actions.decide_burst` and `POST /api/photos/{id}/burst-decision` (`{"decision": "reject"}` or `{"decision": null}`) reject every present frame of a burst, with the same exception, or clear the owner's decision on each. Each changed frame is logged in `decision_log` with the action "burst".
- Done culling records a frame as picked (`cull_outcomes.picked`) only when it is kept in the end, since the owner may reject it after picking it.
- Done culling's preview also counts the bursts that have open frames and no keep (`open_bursts`). The dialog says how many photos and bursts are still open, and that their open frames are kept.
- Unchanged: the photos endpoint (the client derives the marks from `decision` and `pile`), the final calls, the piles, the trust dial, the metrics and the rules.

## Consequences for the plan

- §2 and §6: the cull view is one grid of every photo with the piles as filters, instead of three pile tabs with Your call first; bursts are whole tiles instead of stacks per pile; the keyboard draft gains `N`, `PgUp` and `PgDn`, and `Tab` for the filters, and since 2026-11-22 has `G`, `F` and `M` for keep, reject and the focus map; since 2026-11-27 its `←` `→` and `↑` `↓` rows walk the loupe and burst compare as one.
- §4.4: the app's own rejects stay the app's when the owner picks a frame. From 2026-11-20 to 2026-11-22 picking another frame also moved the owner's keep, replacing ADR 0009's pick rule; since then a pick leaves the owner's earlier keeps alone again, as ADR 0009's did.
- §10: Phase 1's pile tabs became this view.
- §12: decision 18 stands, since the piles and the trust dial still decide what the app does on its own; they are now shown as marks and filters instead of tabs. Decision 24 records this view.
