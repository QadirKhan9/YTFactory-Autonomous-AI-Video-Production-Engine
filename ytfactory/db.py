"""SQLite WAL blackboard + transactional job queue (D-006).

Connection-per-object, autocommit mode with explicit BEGIN IMMEDIATE for
queue leasing, per architecture.md §1.2 / design.md §3.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "schema.sql"
DEFAULT_DB = Path(__file__).resolve().parent.parent / "store" / "ytfactory.db"

LEASE_MINUTES = 15


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def slugify(topic: str) -> str:
    base = unicodedata.normalize("NFKD", topic)
    base = base.encode("ascii", "ignore").decode("ascii")
    base = re.sub(r"[^a-zA-Z0-9]+", "-", base).strip("-").lower()[:64] or "project"
    digest = hashlib.sha256(topic.encode("utf-8")).hexdigest()[:8]
    return f"{base}-{digest}"


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Blackboard:
    def __init__(self, db_path: str | Path = DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")

    def close(self) -> None:
        self.conn.close()

    # -- schema ----------------------------------------------------------
    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        # additive migrations for DBs created before these columns existed
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(media_assets)")}
        for col, decl in (("source_title", "TEXT"), ("credit", "TEXT")):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE media_assets ADD COLUMN {col} {decl}")
        vcols = {r["name"] for r in self.conn.execute("PRAGMA table_info(vislib_assets)")}
        if "embedding_model" not in vcols:
            self.conn.execute("ALTER TABLE vislib_assets ADD COLUMN embedding_model TEXT")

    # -- projects --------------------------------------------------------
    def create_project(self, topic: str, config: dict) -> int:
        slug = slugify(topic)
        cfg_json = canonical_json(config)
        cfg_hash = hashlib.sha256(cfg_json.encode("utf-8")).hexdigest()
        cur = self.conn.execute(
            """INSERT INTO projects (slug, topic, status, config_json, config_hash)
               VALUES (?, ?, 'NEW', ?, ?)
               ON CONFLICT(slug) DO UPDATE SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
               RETURNING id""",
            (slug, topic, cfg_json, cfg_hash),
        )
        return int(cur.fetchone()["id"])

    def get_project(self, project_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()

    def resolve_project(self, ref: str) -> Optional[sqlite3.Row]:
        if ref.isdigit():
            row = self.get_project(int(ref))
            if row:
                return row
        return self.conn.execute(
            "SELECT * FROM projects WHERE slug = ?", (ref,)
        ).fetchone()

    def set_project_status(self, project_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE projects SET status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE id = ?",
            (status, project_id),
        )

    def load_config(self, project_id: int) -> dict:
        row = self.get_project(project_id)
        return json.loads(row["config_json"]) if row else {}

    # -- steps -----------------------------------------------------------
    def get_step(self, project_id: int, step_name: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM steps WHERE project_id = ? AND step_name = ?",
            (project_id, step_name),
        ).fetchone()

    def list_steps(self, project_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM steps WHERE project_id = ? ORDER BY step_name",
            (project_id,),
        ).fetchall()

    def begin_step(self, project_id: int, step_name: str, idem_key: str) -> None:
        self.conn.execute(
            """INSERT INTO steps (project_id, step_name, idem_key, status, started_at)
               VALUES (?, ?, ?, 'running', ?)
               ON CONFLICT(project_id, step_name) DO UPDATE SET
                 idem_key = excluded.idem_key,
                 status = 'running',
                 gate = NULL,
                 error = NULL,
                 started_at = excluded.started_at,
                 finished_at = NULL""",
            (project_id, step_name, idem_key, utcnow()),
        )

    def finish_step(
        self,
        project_id: int,
        step_name: str,
        outputs: dict,
        gate: str | None = None,
        status: str = "done",
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            """UPDATE steps SET status = ?, gate = ?, outputs_json = ?,
               error = ?, finished_at = ?
               WHERE project_id = ? AND step_name = ?""",
            (
                status,
                gate,
                json.dumps(outputs, ensure_ascii=False) if outputs is not None else None,
                error,
                utcnow(),
                project_id,
                step_name,
            ),
        )

    def invalidate_from(self, project_id: int, first_step: str, order: list[str]) -> int:
        """`rerun --from <step>`: delete target + downstream step rows (FR-Res-03)."""
        if first_step not in order:
            raise ValueError(f"unknown step: {first_step}")
        doomed = order[order.index(first_step):]
        qmarks = ",".join("?" for _ in doomed)
        cur = self.conn.execute(
            f"DELETE FROM steps WHERE project_id = ? AND step_name IN ({qmarks})",
            (project_id, *doomed),
        )
        return cur.rowcount

    # -- jobs (BEGIN IMMEDIATE lease queue) -------------------------------
    def enqueue_job(self, project_id: int, step_name: str, capability: str, payload: dict) -> int:
        cur = self.conn.execute(
            """INSERT INTO jobs (project_id, step_name, capability, status, payload_json)
               VALUES (?, ?, ?, 'queued', ?)""",
            (project_id, step_name, capability, canonical_json(payload)),
        )
        return int(cur.lastrowid)

    def claim_job(self, capability: str, worker_id: str) -> Optional[sqlite3.Row]:
        now = utcnow()
        lease = (datetime.now(timezone.utc) + timedelta(minutes=LEASE_MINUTES)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )[:-3] + "Z"
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                """SELECT * FROM jobs
                   WHERE status = 'queued' AND capability = ? AND available_at <= ?
                   ORDER BY id LIMIT 1""",
                (capability, now),
            ).fetchone()
            if row is None:
                self.conn.execute("COMMIT")
                return None
            self.conn.execute(
                """UPDATE jobs SET status = 'running', worker_id = ?,
                   lease_expires_at = ?, attempts = attempts + 1 WHERE id = ?""",
                (worker_id, lease, row["id"]),
            )
            self.conn.execute("COMMIT")
            return row
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def complete_job(self, job_id: int) -> None:
        self.conn.execute("UPDATE jobs SET status = 'done' WHERE id = ?", (job_id,))

    def fail_job(self, job_id: int, error: str) -> None:
        self.conn.execute(
            "UPDATE jobs SET status = 'failed', last_error = ? WHERE id = ?",
            (error, job_id),
        )

    # -- observability ----------------------------------------------------
    def add_event(
        self,
        project_id: int | None,
        event_type: str,
        message: str,
        level: str = "info",
        metadata: dict | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO events (project_id, event_type, level, message, metadata_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                project_id,
                event_type,
                level,
                message,
                json.dumps(metadata, ensure_ascii=False) if metadata else None,
            ),
        )

    def add_usage(
        self,
        project_id: int,
        step_name: str,
        provider: str,
        model: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> None:
        self.conn.execute(
            """INSERT INTO usage_ledger
               (project_id, step_name, provider, model, tokens_in, tokens_out)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (project_id, step_name, provider, model, tokens_in, tokens_out),
        )
