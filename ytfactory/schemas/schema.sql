-- YTFactory blackboard schema (Phase 0).
-- PRAGMAs duplicated here so the file is self-describing; db.py issues them too.
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS projects (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    slug         TEXT NOT NULL UNIQUE,
    topic        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'NEW',
    config_json  TEXT NOT NULL,
    config_hash  TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- Idempotency IS this table: PRIMARY KEY (project_id, step_name) for the DAG,
-- UNIQUE (project_id, idem_key) for crash-safe resumability (FR-Res-01/02).
CREATE TABLE IF NOT EXISTS steps (
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    step_name    TEXT NOT NULL,
    idem_key     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','running','done','failed','blocked')),
    gate         TEXT,
    outputs_json TEXT,
    error        TEXT,
    started_at   TEXT,
    finished_at  TEXT,
    PRIMARY KEY (project_id, step_name),
    UNIQUE (project_id, idem_key)
);

CREATE TABLE IF NOT EXISTS jobs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    step_name        TEXT NOT NULL,
    capability       TEXT NOT NULL CHECK (capability IN ('cpu','net','gpu','render','human')),
    status           TEXT NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued','running','done','failed')),
    payload_json     TEXT,
    worker_id        TEXT,
    lease_expires_at TEXT,
    attempts         INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT,
    available_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_jobs_leasable ON jobs(status, capability, available_at);

CREATE TABLE IF NOT EXISTS artifacts (
    sha256      TEXT PRIMARY KEY,
    mime        TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    storage_path TEXT NOT NULL,
    role        TEXT,
    provenance  TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS project_artifacts (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    sha256     TEXT NOT NULL REFERENCES artifacts(sha256),
    role       TEXT NOT NULL,
    PRIMARY KEY (project_id, sha256, role)
);

CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    url          TEXT NOT NULL,
    url_sha      TEXT NOT NULL,
    title        TEXT,
    domain       TEXT,
    quality_tier TEXT,
    domain_score REAL,
    clean_text   TEXT,
    text_sha     TEXT,
    is_retracted INTEGER NOT NULL DEFAULT 0,
    fetched_at   TEXT,
    UNIQUE (project_id, url_sha)
);

CREATE TABLE IF NOT EXISTS evidence (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_id      INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    chunk_text     TEXT NOT NULL,
    char_span      TEXT NOT NULL,
    embedding_blob BLOB,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_evidence_project ON evidence(project_id);

CREATE TABLE IF NOT EXISTS claims (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id        INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    claim_text        TEXT NOT NULL,
    source_span       TEXT,
    verdict           TEXT NOT NULL DEFAULT 'unverified'
                      CHECK (verdict IN ('unverified','supported','partial','unsupported')),
    nli_score         REAL,
    verification_note TEXT,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS claim_evidence (
    claim_id    INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    evidence_id INTEGER NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    nli_score   REAL,
    PRIMARY KEY (claim_id, evidence_id)
);

-- sentences.duration_ms is the master clock (audio-first invariant).
CREATE TABLE IF NOT EXISTS sentences (
    project_id     INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    ord            INTEGER NOT NULL,
    text           TEXT NOT NULL,
    is_rhetorical  INTEGER NOT NULL DEFAULT 0,
    claim_ids_json TEXT NOT NULL DEFAULT '[]',
    emphasis       TEXT,
    start_ms       INTEGER,
    duration_ms    INTEGER,
    audio_sha256   TEXT,
    PRIMARY KEY (project_id, ord)
);

CREATE TABLE IF NOT EXISTS scenes (
    project_id         INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    ord                INTEGER NOT NULL,
    sentence_ids_json  TEXT NOT NULL,
    visual_intent      TEXT,
    stock_queries_json TEXT,
    gen_prompt         TEXT,
    motion             TEXT,
    asset_sha256       TEXT,
    license_id         TEXT,
    gate_status        TEXT NOT NULL DEFAULT 'pending',
    start_ms           INTEGER,
    end_ms             INTEGER,
    frames             INTEGER,
    PRIMARY KEY (project_id, ord)
);

-- License wall: renderer joins only gate_status='approved' (FR-V-03).
CREATE TABLE IF NOT EXISTS media_assets (
    sha256         TEXT NOT NULL,
    project_id     INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_url     TEXT,
    source_title   TEXT,
    credit         TEXT,
    license_id     TEXT,
    license_url    TEXT,
    commercial_ok  INTEGER NOT NULL DEFAULT 0,
    terms_html_sha TEXT,
    retrieved_at   TEXT,
    gate_status    TEXT NOT NULL DEFAULT 'pending'
                   CHECK (gate_status IN ('pending','approved','blocked')),
    PRIMARY KEY (project_id, sha256)
);

CREATE TABLE IF NOT EXISTS usage_ledger (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    step_name      TEXT NOT NULL,
    provider       TEXT,
    model          TEXT,
    tokens_in      INTEGER NOT NULL DEFAULT 0,
    tokens_out     INTEGER NOT NULL DEFAULT 0,
    estimated_cost REAL NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER,
    event_type    TEXT NOT NULL,
    level         TEXT NOT NULL DEFAULT 'info',
    message       TEXT NOT NULL,
    metadata_json TEXT,
    timestamp     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- Phase 6.2: reusable visual library (FR-V-09) — cross-project, commercial-safe
-- assets only (commercial_ok enforced in code at ingest).
CREATE TABLE IF NOT EXISTS vislib_assets (
    sha256      TEXT PRIMARY KEY,
    file_path   TEXT NOT NULL,
    width       INTEGER,
    height      INTEGER,
    license_id  TEXT NOT NULL,
    license_url TEXT,
    credit      TEXT,
    source_url  TEXT,
    source      TEXT NOT NULL DEFAULT 'generated',
    tags_json   TEXT NOT NULL DEFAULT '[]',
    phash       TEXT,
    hist_json   TEXT,
    embedding   BLOB,
    embedding_model TEXT,  -- 'visual_v1' (offline) or the CLIP model name
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
