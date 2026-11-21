-- Phase 2: edit runs, the agents' proposals, each edited photo's review state, the owner's overrides, exports into
-- jpg/, the owner's looks, and what the app last wrote into a managed RawTherapee sidecar.
-- Recipes and their versions stay in `recipes` and `recipe_history` (migration 0001).

CREATE TABLE edit_runs (
    id          INTEGER PRIMARY KEY,
    folder_id   INTEGER NOT NULL REFERENCES folders(id),
    look        TEXT NOT NULL,              -- the default look of the run, such as neutral@1
    settings    TEXT NOT NULL DEFAULT '{}',
    metrics     TEXT NOT NULL DEFAULT '{}',
    started_at  TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX edit_runs_folder ON edit_runs (folder_id);

-- What each agent proposed for a photo in a run, and what of it went into the recipe.
CREATE TABLE proposals (
    id         INTEGER PRIMARY KEY,
    image_id   INTEGER NOT NULL REFERENCES images(id),
    run_id     INTEGER REFERENCES edit_runs(id),
    agent      TEXT NOT NULL,               -- name@version, such as wb@1
    params     TEXT NOT NULL,
    as_shot    TEXT NOT NULL DEFAULT '{}',
    applied    TEXT NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL,
    band       TEXT NOT NULL,               -- apply, conservative, as_shot, abstained, locked
    rationale  TEXT NOT NULL,
    evidence   TEXT NOT NULL DEFAULT '{}',
    flags      TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX proposals_image ON proposals (image_id);

-- Each edited photo's review state: its look, why it needs a look, QA, and its latest cached previews.
CREATE TABLE edits (
    image_id    INTEGER PRIMARY KEY REFERENCES images(id),
    run_id      INTEGER REFERENCES edit_runs(id),
    look        TEXT NOT NULL,
    review      INTEGER NOT NULL DEFAULT 0, -- queued for a look
    reasons     TEXT NOT NULL DEFAULT '[]',
    qa          TEXT NOT NULL DEFAULT '[]',
    before      TEXT,                       -- the as-shot preview in the render cache
    after       TEXT,                       -- the edited preview in the render cache
    reviewed_at TEXT,                       -- when the owner marked it seen
    updated_at  TEXT NOT NULL
);

-- Every change the owner made to a value the app had set: training data for the style model (plan §4.11).
CREATE TABLE overrides (
    id          INTEGER PRIMARY KEY,
    image_id    INTEGER NOT NULL REFERENCES images(id),
    param       TEXT NOT NULL,              -- group.name, such as exposure.ev
    app_value   TEXT,                       -- JSON
    owner_value TEXT,                       -- JSON; NULL when reset to as shot
    source      TEXT,                       -- what had set the app's value
    action      TEXT NOT NULL,              -- nudge, set, reset, lock, unlock, scene
    at          TEXT NOT NULL
);
CREATE INDEX overrides_image ON overrides (image_id);

-- Full-size JPEGs written into the folder's jpg/ (plan §6.1); a re-export moves the old file to the trash first.
CREATE TABLE exports (
    id             INTEGER PRIMARY KEY,
    image_id       INTEGER NOT NULL REFERENCES images(id),
    name           TEXT NOT NULL,           -- relative to the folder, such as jpg/DSC_0001.jpg
    recipe_version INTEGER NOT NULL,
    recipe_sha256  TEXT NOT NULL,
    sha256         TEXT NOT NULL,
    size           INTEGER NOT NULL,
    exported_at    TEXT NOT NULL,
    trash_entry    INTEGER REFERENCES trash_entries(id)  -- where the file went when a re-export replaced it
);
CREATE INDEX exports_image ON exports (image_id);

-- The owner's looks (plan §4.12); saving a look again adds a version.
CREATE TABLE looks (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    version    INTEGER NOT NULL,
    definition TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (name, version)
);

-- What the app last wrote into a managed sidecar, so the owner's changes in RawTherapee can be told apart.
ALTER TABLE sidecars ADD COLUMN written TEXT;
