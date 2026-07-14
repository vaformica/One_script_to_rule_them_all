from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Any
from contextlib import contextmanager
from datetime import datetime, timezone

from .models import AnalysisUnit


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS analysis_units (
    analysis_unit_id TEXT PRIMARY KEY,
    video_path TEXT NOT NULL,
    toml_path TEXT NOT NULL,
    video_filename TEXT NOT NULL,
    toml_filename TEXT NOT NULL,
    cell_label TEXT NOT NULL,
    assay_type TEXT NOT NULL,
    animal_count INTEGER,
    roi_count INTEGER,
    match_method TEXT NOT NULL,
    match_score INTEGER NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(video_path, toml_path)
);

CREATE TABLE IF NOT EXISTS scans (
    scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    video_count INTEGER DEFAULT 0,
    toml_count INTEGER DEFAULT 0,
    matched_count INTEGER DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    run_label TEXT,
    mode TEXT NOT NULL,
    assay_profile TEXT NOT NULL,
    status TEXT NOT NULL,
    local_run_dir TEXT NOT NULL,
    remote_run_dir TEXT NOT NULL,
    idtracker_job_id TEXT,
    postprocess_job_id TEXT,
    settings_json TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS run_units (
    run_id TEXT NOT NULL,
    analysis_unit_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    run_toml_local_path TEXT NOT NULL,
    run_toml_remote_path TEXT NOT NULL,
    idtracker_status TEXT NOT NULL DEFAULT 'not_submitted',
    idtracker_completed_at TEXT,
    session_remote_path TEXT,
    postprocess_status TEXT NOT NULL DEFAULT 'blocked',
    postprocess_completed_at TEXT,
    track_local_path TEXT,
    summary_local_path TEXT,
    last_error TEXT,
    PRIMARY KEY(run_id, analysis_unit_id),
    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY(analysis_unit_id) REFERENCES analysis_units(analysis_unit_id)
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    submitted_by TEXT NOT NULL,
    state TEXT NOT NULL,
    last_checked_at TEXT,
    raw_status TEXT,
    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS qc_reviews (
    qc_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    analysis_unit_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    notes TEXT,
    track_local_path TEXT,
    UNIQUE(run_id, analysis_unit_id),
    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY(analysis_unit_id) REFERENCES analysis_units(analysis_unit_id)
);

CREATE INDEX IF NOT EXISTS idx_analysis_units_video ON analysis_units(video_filename);
CREATE INDEX IF NOT EXISTS idx_analysis_units_toml ON analysis_units(toml_filename);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at);
CREATE INDEX IF NOT EXISTS idx_run_units_status ON run_units(idtracker_status, postprocess_status);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def begin_scan(self) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO scans(started_at) VALUES (?)",
                (utcnow(),),
            )
            return int(cur.lastrowid)

    def finish_scan(self, scan_id: int, video_count: int, toml_count: int, matched_count: int, notes: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE scans
                   SET completed_at=?, video_count=?, toml_count=?, matched_count=?, notes=?
                   WHERE scan_id=?""",
                (utcnow(), video_count, toml_count, matched_count, notes, scan_id),
            )

    def upsert_analysis_units(self, units: Iterable[AnalysisUnit]) -> None:
        now = utcnow()
        with self.connect() as conn:
            for u in units:
                conn.execute(
                    """
                    INSERT INTO analysis_units(
                        analysis_unit_id, video_path, toml_path, video_filename,
                        toml_filename, cell_label, assay_type, animal_count,
                        roi_count, match_method, match_score, first_seen_at,
                        last_seen_at, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(analysis_unit_id) DO UPDATE SET
                        video_path=excluded.video_path,
                        toml_path=excluded.toml_path,
                        video_filename=excluded.video_filename,
                        toml_filename=excluded.toml_filename,
                        cell_label=excluded.cell_label,
                        assay_type=excluded.assay_type,
                        animal_count=excluded.animal_count,
                        roi_count=excluded.roi_count,
                        match_method=excluded.match_method,
                        match_score=excluded.match_score,
                        last_seen_at=excluded.last_seen_at,
                        active=1
                    """,
                    (
                        u.analysis_unit_id, u.video_path, u.toml_path,
                        u.video_filename, u.toml_filename, u.cell_label,
                        u.assay_type, u.animal_count, u.roi_count,
                        u.match_method, u.match_score, now, now,
                    ),
                )

    def list_analysis_units(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT au.*,
                       COUNT(ru.run_id) AS prior_run_count,
                       MAX(r.created_at) AS latest_run_at,
                       MAX(CASE WHEN q.decision='accepted' THEN q.reviewed_at END) AS accepted_at
                FROM analysis_units au
                LEFT JOIN run_units ru ON au.analysis_unit_id=ru.analysis_unit_id
                LEFT JOIN runs r ON ru.run_id=r.run_id
                LEFT JOIN qc_reviews q
                  ON q.analysis_unit_id=au.analysis_unit_id AND q.run_id=ru.run_id
                WHERE au.active=1
                GROUP BY au.analysis_unit_id
                ORDER BY au.video_filename, au.cell_label, au.toml_filename
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def create_run(self, run_record: dict[str, Any], unit_records: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO runs(
                    run_id, created_at, created_by, run_label, mode,
                    assay_profile, status, local_run_dir, remote_run_dir,
                    settings_json, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_record["run_id"], run_record["created_at"],
                    run_record["created_by"], run_record.get("run_label", ""),
                    run_record["mode"], run_record["assay_profile"],
                    run_record["status"], run_record["local_run_dir"],
                    run_record["remote_run_dir"], run_record["settings_json"],
                    run_record.get("notes", ""),
                ),
            )
            for rec in unit_records:
                conn.execute(
                    """
                    INSERT INTO run_units(
                        run_id, analysis_unit_id, ordinal,
                        run_toml_local_path, run_toml_remote_path,
                        idtracker_status, postprocess_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_record["run_id"], rec["analysis_unit_id"],
                        rec["ordinal"], rec["run_toml_local_path"],
                        rec["run_toml_remote_path"],
                        rec.get("idtracker_status", "not_submitted"),
                        rec.get("postprocess_status", "blocked"),
                    ),
                )

    def list_runs(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*,
                       COUNT(ru.analysis_unit_id) AS unit_count,
                       SUM(CASE WHEN ru.idtracker_status='completed' THEN 1 ELSE 0 END) AS idtracker_completed,
                       SUM(CASE WHEN ru.postprocess_status='completed' THEN 1 ELSE 0 END) AS postprocess_completed,
                       SUM(CASE WHEN q.decision='accepted' THEN 1 ELSE 0 END) AS accepted_count
                FROM runs r
                LEFT JOIN run_units ru ON r.run_id=ru.run_id
                LEFT JOIN qc_reviews q
                  ON q.run_id=ru.run_id AND q.analysis_unit_id=ru.analysis_unit_id
                GROUP BY r.run_id
                ORDER BY r.created_at DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def get_run_units(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT ru.*, au.video_filename, au.toml_filename, au.cell_label,
                       au.assay_type, au.video_path, au.toml_path,
                       q.decision AS qc_decision, q.notes AS qc_notes,
                       q.reviewed_at AS qc_reviewed_at
                FROM run_units ru
                JOIN analysis_units au ON au.analysis_unit_id=ru.analysis_unit_id
                LEFT JOIN qc_reviews q
                  ON q.run_id=ru.run_id AND q.analysis_unit_id=ru.analysis_unit_id
                WHERE ru.run_id=?
                ORDER BY ru.ordinal
                """,
                (run_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_run(self, run_id: str, **fields: Any) -> None:
        allowed = {
            "status", "idtracker_job_id", "postprocess_job_id", "notes"
        }
        items = [(k, v) for k, v in fields.items() if k in allowed]
        if not items:
            return
        sql = "UPDATE runs SET " + ", ".join(f"{k}=?" for k, _ in items) + " WHERE run_id=?"
        values = [v for _, v in items] + [run_id]
        with self.connect() as conn:
            conn.execute(sql, values)

    def update_run_unit(self, run_id: str, analysis_unit_id: str, **fields: Any) -> None:
        allowed = {
            "idtracker_status", "idtracker_completed_at", "session_remote_path",
            "postprocess_status", "postprocess_completed_at", "track_local_path",
            "summary_local_path", "last_error"
        }
        items = [(k, v) for k, v in fields.items() if k in allowed]
        if not items:
            return
        sql = "UPDATE run_units SET " + ", ".join(f"{k}=?" for k, _ in items)
        sql += " WHERE run_id=? AND analysis_unit_id=?"
        values = [v for _, v in items] + [run_id, analysis_unit_id]
        with self.connect() as conn:
            conn.execute(sql, values)

    def record_job(self, job_id: str, run_id: str, stage: str, submitted_by: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs(job_id, run_id, stage, submitted_at, submitted_by, state)
                VALUES (?, ?, ?, ?, ?, 'submitted')
                ON CONFLICT(job_id) DO UPDATE SET
                    state='submitted',
                    last_checked_at=NULL
                """,
                (job_id, run_id, stage, utcnow(), submitted_by),
            )

    def update_job(self, job_id: str, state: str, raw_status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET state=?, raw_status=?, last_checked_at=?
                WHERE job_id=?
                """,
                (state, raw_status, utcnow(), job_id),
            )

    def list_jobs(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT j.*, r.run_label
                FROM jobs j
                JOIN runs r ON j.run_id=r.run_id
                ORDER BY j.submitted_at DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def save_qc(
        self,
        run_id: str,
        analysis_unit_id: str,
        decision: str,
        reviewer: str,
        notes: str,
        track_local_path: str | None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO qc_reviews(
                    run_id, analysis_unit_id, decision, reviewer,
                    reviewed_at, notes, track_local_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, analysis_unit_id) DO UPDATE SET
                    decision=excluded.decision,
                    reviewer=excluded.reviewer,
                    reviewed_at=excluded.reviewed_at,
                    notes=excluded.notes,
                    track_local_path=excluded.track_local_path
                """,
                (
                    run_id, analysis_unit_id, decision, reviewer,
                    utcnow(), notes, track_local_path,
                ),
            )

    def accepted_results(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT q.run_id, q.analysis_unit_id, q.reviewer, q.reviewed_at,
                       q.notes, ru.track_local_path, ru.summary_local_path,
                       au.video_filename, au.toml_filename, au.cell_label,
                       au.assay_type, r.created_at AS run_created_at,
                       r.run_label
                FROM qc_reviews q
                JOIN run_units ru
                  ON q.run_id=ru.run_id AND q.analysis_unit_id=ru.analysis_unit_id
                JOIN analysis_units au ON au.analysis_unit_id=q.analysis_unit_id
                JOIN runs r ON r.run_id=q.run_id
                WHERE q.decision='accepted'
                ORDER BY au.video_filename, au.cell_label, r.created_at
                """
            ).fetchall()
            return [dict(r) for r in rows]
