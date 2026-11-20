# ADR 0010: No ratings

- **Status:** accepted
- **Date:** 2026-11-20 (after Phase 1)
- **Code:** `src/rie/cull/` (`rules.py`, `piles.py`, `model.py`, `actions.py`), `src/rie/sidecars.py`, `src/rie/api/app.py`, `web/src/`

## Context

Phase 1 used 1–5 ratings in two ways.

- **Ratings made in the app.** The owner could rate a photo with the number keys or the stars in the action panel, as the keyboard draft of plan §6 had it. The rating went into the catalog and was mirrored into the photo's RawTherapee sidecar (`[General] Rank`) so that RawTherapee's file browser showed it. To do that, the app backed up a sidecar it had not created before changing it, created a sidecar holding only the rating where none existed, and kept ratings in the catalog until the drive came back. Apart from Done culling, these were Phase 1's only writes into the photo folders.
- **Ratings made in RawTherapee.** Decision 5 imported them from the owner's existing sidecars (Phase 0 found a rating in 28 of 60 sampled sidecars, ADR 0002). A rated photo was a keep (`rules@1`'s first rule, kind "rated") and went straight to Auto-keep, and the rating was one of the personal model's 33 features.

Nothing after culling uses a rating. Phase 2 edits every kept photo, the export writes one JPEG per kept photo (decision 13), and Claude's passes, the taste notes and the look lab (Phases 3 and 4) don't read ratings. Keep and reject already say what the owner wants from a shoot. In the app, a rating didn't even keep a photo: a photo in Auto-reject that the owner rated still went to the trash at Done culling unless the folder was sorted again first.

After culling a first real shoot, the owner decided the app should have no ratings at all, and should ignore those made in RawTherapee too.

## Decision

- **No ratings in the app:** no rating keys, no stars on the photos, in the bottom bar or in the action panel, no `POST /api/photos/{id}/rating` and no `actions.set_rating`. Keys 1–5 only answer the optional reason picker.
- **No sidecar writes:** `sidecars.write_rank`, the rating-only sidecars and the ratings that waited for the drive are gone. In culling, the photo folders now change only at Done culling. Phase 2's managed sidecars will be the app's first sidecar writes, under ADR 0007's rules.
- **RawTherapee's ratings are ignored:** `rie open` no longer reads `[General] Rank`, so a photo rated in RawTherapee is sorted like any other. Its edits are still imported as the owner's locked parameters (decision 5).
- **`rules@2`** is `rules@1` without its first rule; the kind "rated" no longer exists.
- **The personal model** loses the rating feature (32 features). A version trained before this change is set aside: the rules sort until the next Done culling trains a new version, which isn't compared with the outdated one, and an outdated version can't be rolled back to (the API answers 409).
- **Earlier data stays:** the catalog keeps its `decisions.rating`, `sidecars.rank` and `sidecars.pending_rank` columns and whatever earlier builds stored in them, and the sidecars those builds wrote stay as they are. Nothing reads them.
- RawTherapee's trash flag (`[General] InTrash`) still makes the app suggest a reject, as before; it never decides on its own.

## Consequences for the plan

- §3.3: the app neither writes nor reads ratings in sidecars; their edits are still imported.
- §6: the keyboard draft's "1–5, 0: rating / clear" becomes "1–5: quick reason, when the picker shows".
- §10: Phase 1 imports only edits from existing sidecars, and its "ratings mirrored to RawTherapee" is dropped.
- §12: decision 5 keeps the edits and drops the ratings; decision 23 records this ADR.
- Photos that were rated earlier lose their automatic keep the next time their folder is sorted, so some may move to Auto-reject.
