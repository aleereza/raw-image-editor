-- Phase 1: folders, images, analysis, groups, culling decisions, sidecars, the trash log and jobs.
-- Times are UTC ISO 8601 strings. JSON columns hold objects or arrays as text.

CREATE TABLE folders (
    id          INTEGER PRIMARY KEY,
    drive_uuid  TEXT NOT NULL DEFAULT '',   -- '' when the folder is not on the configured photo drive
    path        TEXT NOT NULL,              -- relative to the drive's mount point, or absolute when drive_uuid = ''
    name        TEXT NOT NULL,              -- the folder's own name, for display
    opened_at   TEXT NOT NULL,
    scanned_at  TEXT,
    analyzed_at TEXT,
    culled_at   TEXT,                       -- when Done culling last finished
    claude      INTEGER NOT NULL DEFAULT 1, -- Claude's passes allowed for this folder (Phase 3)
    UNIQUE (drive_uuid, path)
);

CREATE TABLE images (
    id            INTEGER PRIMARY KEY,
    folder_id     INTEGER NOT NULL REFERENCES folders(id),
    name          TEXT NOT NULL,            -- the raw file, or the JPEG of a JPEG-only shot
    kind          TEXT NOT NULL CHECK (kind IN ('raw', 'jpeg')),
    jpeg_name     TEXT,                     -- the paired camera JPEG of a RAW+JPEG shot
    size          INTEGER NOT NULL,
    mtime_ns      INTEGER NOT NULL,
    quick_hash    TEXT NOT NULL,            -- size and first 64 KiB: re-links moved or renamed files
    content_hash  TEXT,                     -- the whole file, once analysis has read it
    state         TEXT NOT NULL DEFAULT 'present' CHECK (state IN ('present', 'missing', 'trashed')),
    capture_time  TEXT,                     -- as recorded by the camera (local time), with tenths
    shutter_count INTEGER,
    width         INTEGER,                  -- upright size of the camera's full-size JPEG
    height        INTEGER,
    orientation   INTEGER NOT NULL DEFAULT 1,
    meta          TEXT NOT NULL DEFAULT '{}',  -- normalized metadata
    exif          TEXT NOT NULL DEFAULT '{}',  -- exiftool -json -n -G1 -a
    previews      TEXT NOT NULL DEFAULT '{}',  -- embedded JPEG byte ranges
    scanned_at    TEXT NOT NULL,
    UNIQUE (folder_id, name)
);
CREATE INDEX images_quick_hash ON images (quick_hash);
CREATE INDEX images_content_hash ON images (content_hash);

-- RawTherapee sidecars next to the photos: what was imported, whether the app created the file, and the backup
-- made before the app first added to a sidecar it did not create.
CREATE TABLE sidecars (
    id             INTEGER PRIMARY KEY,
    folder_id      INTEGER NOT NULL REFERENCES folders(id),
    image_id       INTEGER REFERENCES images(id),
    name           TEXT NOT NULL,           -- e.g. DSC_0001.NEF.pp3
    created_by_app INTEGER NOT NULL DEFAULT 0,
    backup_path    TEXT,
    sha256         TEXT,                    -- of the content the app last read or wrote
    imported_at    TEXT,
    rank           INTEGER,
    color_label    INTEGER,
    in_trash       INTEGER NOT NULL DEFAULT 0,
    edits          TEXT NOT NULL DEFAULT '{}',   -- recipe parameters imported as locked edits
    unmodeled      TEXT NOT NULL DEFAULT '[]',   -- changed keys the recipe cannot express, kept as they are
    pending_rank   INTEGER,                 -- a rating not yet written because the drive was away
    UNIQUE (folder_id, name)
);

CREATE TABLE recipes (
    image_id   INTEGER PRIMARY KEY REFERENCES images(id),
    version    INTEGER NOT NULL,
    recipe     TEXT NOT NULL,               -- rie.recipe/1 with provenance and locks
    updated_at TEXT NOT NULL
);

CREATE TABLE recipe_history (
    id       INTEGER PRIMARY KEY,
    image_id INTEGER NOT NULL REFERENCES images(id),
    version  INTEGER NOT NULL,
    recipe   TEXT NOT NULL,
    source   TEXT NOT NULL,
    at       TEXT NOT NULL
);

CREATE TABLE analyses (
    image_id   INTEGER NOT NULL REFERENCES images(id),
    kind       TEXT NOT NULL,               -- look, sharpness, faces, focus
    version    TEXT NOT NULL,
    input_hash TEXT NOT NULL,               -- what the result was computed from
    result     TEXT NOT NULL,
    seconds    REAL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (image_id, kind)
);

-- Faces: boxes and landmarks normalized to the upright frame. No identities; `track` follows one face through a
-- burst by position only.
CREATE TABLE faces (
    id        INTEGER PRIMARY KEY,
    image_id  INTEGER NOT NULL REFERENCES images(id),
    idx       INTEGER NOT NULL,
    box       TEXT NOT NULL,
    landmarks TEXT,
    score     REAL NOT NULL,
    source    TEXT NOT NULL,
    blink_l   REAL,
    blink_r   REAL,
    smile     REAL,
    yaw       REAL,
    pitch     REAL,
    roll      REAL,
    sharpness REAL,
    cut_off   INTEGER NOT NULL DEFAULT 0,
    track     INTEGER,
    UNIQUE (image_id, idx)
);
CREATE INDEX faces_image ON faces (image_id);

CREATE TABLE groups (
    id        INTEGER PRIMARY KEY,
    folder_id INTEGER NOT NULL REFERENCES folders(id),
    kind      TEXT NOT NULL CHECK (kind IN ('scene', 'burst')),
    parent_id INTEGER REFERENCES groups(id),
    position  INTEGER NOT NULL,
    info      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX groups_folder ON groups (folder_id);

CREATE TABLE group_members (
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    image_id INTEGER NOT NULL REFERENCES images(id),
    position INTEGER NOT NULL,
    rank     INTEGER,
    score    REAL,
    PRIMARY KEY (group_id, image_id)
);
CREATE INDEX group_members_image ON group_members (image_id);

-- The app's sort: one row per image, replaced whenever the folder is sorted again.
CREATE TABLE suggestions (
    image_id    INTEGER PRIMARY KEY REFERENCES images(id),
    pile        TEXT NOT NULL CHECK (pile IN ('auto_reject', 'auto_keep', 'your_call')),
    decision    TEXT CHECK (decision IN ('keep', 'reject')),
    kind        TEXT NOT NULL,              -- technical, duplicates, eyes, expression, composition
    probability REAL,                       -- that the owner rejects the photo
    reason      TEXT NOT NULL,
    model       TEXT NOT NULL,
    features    TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

-- The owner's current decision per image. Undecided photos have no row or a NULL decision.
CREATE TABLE decisions (
    image_id    INTEGER PRIMARY KEY REFERENCES images(id),
    decision    TEXT CHECK (decision IN ('keep', 'reject')),
    rating      INTEGER CHECK (rating BETWEEN 0 AND 5),
    color_label INTEGER,
    decided_by  TEXT,                       -- owner, or rawtherapee for an imported rating
    reason      TEXT,
    updated_at  TEXT NOT NULL
);

-- Every culling action, in order: the raw material for agreement statistics and the personal model.
CREATE TABLE decision_log (
    id         INTEGER PRIMARY KEY,
    folder_id  INTEGER NOT NULL REFERENCES folders(id),
    image_id   INTEGER REFERENCES images(id),
    action     TEXT NOT NULL,
    value      TEXT,
    pile       TEXT,
    suggestion TEXT,
    kind       TEXT,
    reason     TEXT,
    at         TEXT NOT NULL
);
CREATE INDEX decision_log_folder ON decision_log (folder_id);

-- One row per photo at Done culling: the final call, who made it, and what the app had suggested.
CREATE TABLE cull_outcomes (
    id          INTEGER PRIMARY KEY,
    folder_id   INTEGER NOT NULL REFERENCES folders(id),
    image_id    INTEGER NOT NULL REFERENCES images(id),
    final       TEXT NOT NULL CHECK (final IN ('keep', 'reject')),
    decided_by  TEXT NOT NULL,              -- owner, accepted (the app's automatic pile), default (undecided: kept)
    pile        TEXT,
    suggestion  TEXT,
    kind        TEXT,
    probability REAL,
    model       TEXT,
    features    TEXT NOT NULL DEFAULT '{}',
    burst_id    INTEGER,
    picked      INTEGER NOT NULL DEFAULT 0, -- the owner picked this frame in its burst
    at          TEXT NOT NULL
);
CREATE INDEX cull_outcomes_folder ON cull_outcomes (folder_id);

CREATE TABLE trust (
    kind       TEXT PRIMARY KEY,
    mode       TEXT NOT NULL CHECK (mode IN ('suggest', 'automatic')),
    changed_at TEXT NOT NULL
);
INSERT INTO trust (kind, mode, changed_at) VALUES
    ('technical', 'automatic', '1970-01-01T00:00:00+00:00'),
    ('duplicates', 'automatic', '1970-01-01T00:00:00+00:00'),
    ('eyes', 'suggest', '1970-01-01T00:00:00+00:00'),
    ('expression', 'suggest', '1970-01-01T00:00:00+00:00'),
    ('composition', 'suggest', '1970-01-01T00:00:00+00:00');

-- Versions of the personal culling model; every version is kept so a worse one can be rolled back.
CREATE TABLE cull_models (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    path       TEXT NOT NULL,
    n_train    INTEGER NOT NULL,
    agreement  REAL,
    baseline   REAL,
    active     INTEGER NOT NULL DEFAULT 0,
    info       TEXT NOT NULL DEFAULT '{}'
);

-- Where each trashed file went, so a photo or a whole folder's rejects can be restored.
CREATE TABLE trash_entries (
    id          INTEGER PRIMARY KEY,
    folder_id   INTEGER REFERENCES folders(id),
    image_id    INTEGER REFERENCES images(id),
    drive_uuid  TEXT NOT NULL DEFAULT '',
    topdir      TEXT NOT NULL,              -- the volume's mount point at the time
    original    TEXT NOT NULL,              -- relative to topdir
    trashed     TEXT NOT NULL,              -- relative to topdir: <trash>/files/<name>
    info        TEXT NOT NULL,              -- relative to topdir: <trash>/info/<name>.trashinfo
    size        INTEGER NOT NULL,
    trashed_at  TEXT NOT NULL,
    restored_at TEXT
);
CREATE INDEX trash_entries_folder ON trash_entries (folder_id);

CREATE TABLE jobs (
    id          INTEGER PRIMARY KEY,
    folder_id   INTEGER REFERENCES folders(id),
    kind        TEXT NOT NULL,
    state       TEXT NOT NULL CHECK (state IN ('queued', 'running', 'paused', 'done', 'failed')),
    step        TEXT,
    done        INTEGER NOT NULL DEFAULT 0,
    total       INTEGER NOT NULL DEFAULT 0,
    message     TEXT,
    started_at  TEXT,
    finished_at TEXT
);
