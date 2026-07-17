from __future__ import annotations
import json
import math
import csv
import io
import sqlite3
import time
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Allow direct execution as well as ``python -m app.mac_gui``.
# When a package module is run by filename, Python otherwise places only the
# app/ directory on sys.path, so imports such as ``app.approval_matching`` fail.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PyQt5.QtCore import Qt, QObject, QThread, pyqtSignal, QTimer
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QPushButton, QLabel, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QFileDialog, QComboBox, QCheckBox, QTextEdit,
    QAbstractItemView, QProgressBar, QGroupBox, QSpinBox, QDoubleSpinBox,
    QRadioButton, QButtonGroup
)
import tomlkit
from app.approval_matching import approval_key, apply_qc_statuses, normalize_qc_status


BASE = REPO_ROOT
CONFIG = BASE / "config/user.json"
DEFAULT = BASE / "config/default.json"


class SSH:
    def __init__(self, host, key):
        self.host = host
        self.key = str(Path(key).expanduser())

    def command(self):
        return [
            "ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-i", self.key, self.host
        ]

    def run(self, command, input_text=None, timeout=900):
        result = subprocess.run(
            self.command() + ["bash", "-lc", shlex.quote(command)],
            input=input_text, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        return result.stdout

    def read(self, path):
        return self.run(f"cat -- {shlex.quote(path)}")

    def write(self, path, text):
        parent = str(Path(path).parent)
        self.run(
            f"mkdir -p {shlex.quote(parent)} && cat > {shlex.quote(path)}",
            input_text=text,
        )


def load_config():
    path = CONFIG if CONFIG.exists() else DEFAULT
    return json.loads(path.read_text())


def find_values(obj, keys):
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in keys:
                found.append(v)
            found.extend(find_values(v, keys))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(find_values(v, keys))
    return found


def extract_analysis_start_frame(obj):
    """Return the first frame configured in an IDtracker tracking interval."""
    candidates = []
    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).lower().replace("-", "_").replace(" ", "_")
                if "tracking_interval" in normalized:
                    pair = _find_first_pair(child)
                    if pair is not None:
                        candidates.append(int(pair[0]))
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(obj)
    return max(0, candidates[0]) if candidates else 0



def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _find_first_pair(value):
    if isinstance(value, list):
        if len(value) == 2 and all(_is_number(x) for x in value):
            return value[0], value[1]
        for item in value:
            pair = _find_first_pair(item)
            if pair is not None:
                return pair
    return None


def _coerce_homogeneous_numeric_pair(pair):
    """
    IDtracker.ai 6.0.10 uses the older `toml` parser, which requires every
    numeric element in an array to use the same TOML type. For example,
    [25.0, 255] is rejected even though both values are numeric.
    """
    if len(pair) != 2 or not all(_is_number(x) for x in pair):
        return

    if any(isinstance(x, float) for x in pair):
        pair[0] = float(pair[0])
        pair[1] = float(pair[1])
    else:
        pair[0] = int(pair[0])
        pair[1] = int(pair[1])


def _update_threshold_pairs(value, minimum=None, maximum=None):
    if not isinstance(value, list):
        return 0

    if len(value) == 2 and all(_is_number(x) for x in value):
        original_was_float = any(isinstance(x, float) for x in value)

        if minimum is not None:
            value[0] = minimum
        if maximum is not None:
            value[1] = maximum

        requires_float = (
            original_was_float
            or any(
                isinstance(x, float) and math.isinf(x)
                for x in value
            )
            or any(
                isinstance(x, float)
                and not math.isinf(x)
                and not float(x).is_integer()
                for x in value
            )
        )

        if requires_float:
            value[0] = float(value[0])
            value[1] = float(value[1])
        else:
            value[0] = int(value[0])
            value[1] = int(value[1])

        _coerce_homogeneous_numeric_pair(value)
        return 1

    updated = 0
    for item in value:
        updated += _update_threshold_pairs(
            item,
            minimum=minimum,
            maximum=maximum,
        )
    return updated


def _validate_threshold_array(value, path):
    if not isinstance(value, list) or not value:
        raise ValueError(f"{path} must be a non-empty array")
    numeric = [_is_number(x) for x in value]
    nested = [isinstance(x, list) for x in value]
    if any(numeric) and any(nested):
        raise ValueError(
            f"{path} mixes scalar values and nested arrays "
            "(IDtracker requires homogeneous arrays)"
        )
    if all(numeric):
        if len(value) != 2:
            raise ValueError(f"{path} must be [minimum, maximum]")
        numeric_types = {type(item) for item in value}
        if len(numeric_types) != 1:
            rendered_types = ", ".join(
                sorted(item_type.__name__ for item_type in numeric_types)
            )
            raise ValueError(
                f"{path} mixes numeric TOML types ({rendered_types}); "
                "IDtracker.ai 6.0.10 requires homogeneous arrays"
            )
        if value[0] > value[1]:
            raise ValueError(
                f"{path} minimum {value[0]} exceeds maximum {value[1]}"
            )
        return
    if all(nested):
        for index, item in enumerate(value):
            _validate_threshold_array(item, f"{path}[{index}]")
        return
    raise ValueError(f"{path} contains unsupported values")


def _validate_thresholds(obj, path="root"):
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{path}.{key}"
            if str(key).lower() in {
                "area_ths",
                "area_thresholds",
                "intensity_ths",
                "intensity_thresholds",
            }:
                _validate_threshold_array(value, child)
            _validate_thresholds(value, child)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            _validate_thresholds(value, f"{path}[{index}]")


class LocalIndex:
    def __init__(self, db_path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS indexed_rows (
                search_root TEXT NOT NULL,
                toml_path TEXT NOT NULL,
                row_json TEXT NOT NULL,
                indexed_at REAL NOT NULL,
                PRIMARY KEY (search_root, toml_path)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS summaries (
                search_root TEXT PRIMARY KEY,
                video_count INTEGER NOT NULL,
                toml_count INTEGER NOT NULL,
                matched_count INTEGER NOT NULL,
                indexed_at REAL NOT NULL
            )
        """)
        self.conn.commit()

    def replace(self, search_root, rows, video_count, toml_count):
        now = time.time()
        with self.conn:
            self.conn.execute(
                "DELETE FROM indexed_rows WHERE search_root = ?",
                (search_root,),
            )
            self.conn.executemany(
                """
                INSERT INTO indexed_rows
                (search_root, toml_path, row_json, indexed_at)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        search_root,
                        row["toml"],
                        json.dumps(row, allow_nan=True),
                        now,
                    )
                    for row in rows
                ],
            )
            self.conn.execute(
                """
                INSERT OR REPLACE INTO summaries
                (search_root, video_count, toml_count, matched_count, indexed_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    search_root,
                    video_count,
                    toml_count,
                    sum(row.get("status") == "Matched" for row in rows),
                    now,
                ),
            )

    def load(self, search_root):
        rows = [
            json.loads(row_json)
            for (row_json,) in self.conn.execute(
                """
                SELECT row_json
                FROM indexed_rows
                WHERE search_root = ?
                ORDER BY toml_path
                """,
                (search_root,),
            )
        ]
        summary = self.conn.execute(
            """
            SELECT video_count, toml_count, matched_count, indexed_at
            FROM summaries
            WHERE search_root = ?
            """,
            (search_root,),
        ).fetchone()
        return rows, summary

    def close(self):
        self.conn.close()


class IndexWorker(QObject):
    progress = pyqtSignal(str)
    finished = pyqtSignal(list, int, int, int, float)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, host, key, search_root, project_root, db_path, rebuild):
        super().__init__()
        self.host = host
        self.key = key
        self.search_root = search_root
        self.project_root = project_root.rstrip("/")
        self.db_path = db_path
        self.rebuild = rebuild
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _load_qc_statuses(self, ssh):
        if not self.project_root:
            return {}
        path = self.project_root + "/QC/run_status.csv"
        text = ssh.run(f"cat -- {shlex.quote(path)} 2>/dev/null || true")
        qc_records = {}
        if not text.strip():
            return qc_records
        for record in csv.DictReader(io.StringIO(text)):
            record["qc_decision"] = normalize_qc_status(record.get("qc_decision"))
            key = approval_key(record.get("video"), record.get("cell"), record.get("analysis"))
            rank = (record.get("date_run", ""), record.get("run_index", ""), record.get("collected_at", ""))
            current = qc_records.get(key)
            if current is None or rank >= current[0]:
                qc_records[key] = (rank, record)
        return {key: value[1] for key, value in qc_records.items()}

    def run(self):
        index = None
        try:
            index = LocalIndex(self.db_path)
            ssh = SSH(self.host, self.key)
            qc_records = self._load_qc_statuses(ssh)

            if not self.rebuild:
                rows, summary = index.load(self.search_root)
                if summary is None:
                    raise RuntimeError(
                        "No saved index exists for this Firebird search root. "
                        "Use Rebuild Firebird Index first."
                    )
                video_count, toml_count, matched_count, indexed_at = summary
                rows = apply_qc_statuses(rows, qc_records)
                self.finished.emit(
                    rows,
                    int(video_count),
                    int(toml_count),
                    int(matched_count),
                    float(indexed_at),
                )
                return

            self.progress.emit("Recursively listing videos and TOMLs on Firebird...")

            find_command = (
                f"find {shlex.quote(self.search_root)} "
                r"-type d \( -name '.git' -o -name '__pycache__' "
                r"-o -name 'node_modules' -o -name 'logs' "
                r"-o -name 'outputs' -o -name 'results' "
                r"-o -name 'archive' -o -name 'archives' "
                r"-o -name 'session_*' -o -name 'run_*' \) -prune "
                r"-o -type f \( -iname '*.mp4' -o -iname '*.avi' "
                r"-o -iname '*.toml' \) -print"
            )
            output = ssh.run(find_command, timeout=3600)
            if self._cancelled:
                self.cancelled.emit()
                return

            all_paths = [line.strip() for line in output.splitlines() if line.strip()]
            video_paths = [
                p for p in all_paths
                if Path(p).suffix.lower() in {".mp4", ".avi"}
            ]
            toml_paths = [
                p for p in all_paths
                if Path(p).suffix.lower() == ".toml"
            ]
            videos_by_name = {}
            for video_path in video_paths:
                videos_by_name.setdefault(
                    Path(video_path).name.lower(),
                    [],
                ).append(video_path)

            rows = []
            total = len(toml_paths)

            for number, toml_path in enumerate(toml_paths, start=1):
                if self._cancelled:
                    self.cancelled.emit()
                    return

                if number == 1 or number % 10 == 0 or number == total:
                    self.progress.emit(
                        f"Reading TOMLs: {number:,} of {total:,}"
                    )

                try:
                    source = ssh.read(toml_path)
                    doc = tomlkit.parse(source)

                    embedded_name = None
                    for value in find_values(
                        doc,
                        {
                            "video_path",
                            "video_paths",
                            "video",
                            "videos",
                            "video_file",
                        },
                    ):
                        candidates = (
                            [value]
                            if isinstance(value, str)
                            else value
                            if isinstance(value, list)
                            else []
                        )
                        for candidate in candidates:
                            if (
                                isinstance(candidate, str)
                                and candidate.lower().endswith((".mp4", ".avi"))
                            ):
                                embedded_name = Path(candidate).name
                                break
                        if embedded_name:
                            break

                    matches = videos_by_name.get(
                        (embedded_name or "").lower(),
                        [],
                    )
                    video = matches[0] if len(matches) == 1 else ""

                    area_pair = None
                    for value in find_values(
                        doc,
                        {"area_ths", "area_thresholds"},
                    ):
                        area_pair = _find_first_pair(value)
                        if area_pair is not None:
                            break

                    intensity_pair = None
                    for value in find_values(
                        doc,
                        {"intensity_ths", "intensity_thresholds"},
                    ):
                        intensity_pair = _find_first_pair(value)
                        if intensity_pair is not None:
                            break

                    animals = next(
                        iter(
                            find_values(
                                doc,
                                {"number_of_animals", "n_animals"},
                            )
                        ),
                        None,
                    )

                    status = "Matched" if video else "Unmatched"
                    reason = (
                        "Exact embedded video filename"
                        if video
                        else "No unique exact embedded video match"
                    )
                    if len(matches) > 1:
                        reason = "Multiple videos share the embedded filename"

                    area_pair = area_pair or (None, None)
                    intensity_pair = intensity_pair or (None, None)
                    stem = Path(toml_path).stem
                    cell = stem.rsplit("_", 1)[-1]

                    rows.append({
                        "use": bool(video),
                        "video": video,
                        "cell": cell,
                        "toml": toml_path,
                        "analysis": (
                            "ba"
                            if animals == 1
                            else "fight"
                            if animals == 2
                            else "ba"
                        ),
                        "area_min": area_pair[0],
                        "area_max": area_pair[1],
                        "background_min": intensity_pair[0],
                        "status": status,
                        "reason": reason,
                    })
                except Exception as exc:
                    rows.append({
                        "use": False,
                        "video": "",
                        "cell": "",
                        "toml": toml_path,
                        "analysis": "ba",
                        "area_min": None,
                        "area_max": None,
                        "background_min": None,
                        "status": "TOML error",
                        "reason": str(exc),
                    })

            rows = apply_qc_statuses(rows, qc_records)
            index.replace(
                self.search_root,
                rows,
                len(video_paths),
                len(toml_paths),
            )
            indexed_at = time.time()
            self.finished.emit(
                rows,
                len(video_paths),
                len(toml_paths),
                sum(row["status"] == "Matched" for row in rows),
                indexed_at,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if index is not None:
                index.close()




class SubmitWorker(QObject):
    progress = pyqtSignal(str)
    job_submitted = pyqtSignal(dict)
    finished = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, host, key, project_root, repo_root, selected_rows, run_mode, parameters):
        super().__init__()
        self.host = host
        self.key = key
        self.project_root = project_root.rstrip("/")
        self.repo_root = repo_root.rstrip("/")
        self.selected_rows = selected_rows
        self.run_mode = run_mode
        self.parameters = parameters

    @staticmethod
    def _postprocess_args(analysis, p):
        if analysis == "fight":
            vals = {
                "analysis-start-frame": p["analysis_start_frame"],
                "analysis-stop-frame": p["analysis_stop_frame"],
                "window-frames": p["window_frames"],
                "contact-px": p["contact_px"],
                "fight-px": p["fight_px"],
                "min-fight-frames": p["min_fight_frames"],
                "roi-wall-buffer-px": p["roi_wall_buffer_px"],
            }
        else:
            vals = {
                "analysis-start-frame": p["ba_analysis_start_frame"],
                "analysis-stop-frame": p["ba_analysis_stop_frame"],
                "move-threshold-px": p["move_threshold_px"],
                "movement-onset-consecutive-frames": p["movement_onset_consecutive_frames"],
                "roi-wall-buffer-px": p["ba_roi_wall_buffer_px"],
                "turtling-window-frames": p["turtling_window_frames"],
                "turtling-min-duration-frames": p["turtling_min_duration_frames"],
            }
        return " ".join(f"--{k} {v}" for k, v in vals.items())

    def run(self):
        messages = []
        try:
            ssh = SSH(self.host, self.key)
            total = len(self.selected_rows)
            for position, payload in enumerate(self.selected_rows, 1):
                row = payload["row"]
                analysis = payload["analysis"]
                area_min = payload["area_min"]
                area_max = payload["area_max"]
                background_min = payload["background_min"]
                label = f"{Path(row['video']).name} / {row['cell']}"
                self.progress.emit(f"Preparing {position} of {total}: {label}")
                try:
                    now = datetime.now()
                    stamp = now.strftime("%Y%m%d_%H%M%S")
                    video_stem = Path(row["video"]).stem
                    attempt_root = f"{self.project_root}/runs/{video_stem}/{row['cell']}"
                    attempt_index = int(ssh.run(
                        f"mkdir -p {shlex.quote(attempt_root)}; "
                        f"find {shlex.quote(attempt_root)} -mindepth 1 -maxdepth 1 -type d "
                        r"\( -name 'attempt_*' -o -name 'run_*' \) -printf '%f\n' 2>/dev/null | "
                        r"sed -E 's/^(attempt|run)_0*([0-9]+).*/\2/' | "
                        "awk '$1+0>m{m=$1+0} END{print m+1}'"
                    ).strip() or "1")
                    run_dir = (
                        f"{attempt_root}/attempt_{attempt_index:05d}_{stamp}"
                    )
                    input_dir = run_dir + "/input"
                    session_out = run_dir + "/idtracker"
                    metadata_path = run_dir + "/run_metadata.json"
                    ssh.run(f"mkdir -p {shlex.quote(input_dir)} {shlex.quote(session_out)}")
                    self.progress.emit(f"Reading TOML for {label}...")
                    source = ssh.read(row["toml"])
                    doc = tomlkit.parse(source)
                    toml_analysis_start = extract_analysis_start_frame(doc)
                    effective_parameters = dict(self.parameters)
                    effective_parameters["analysis_start_frame"] = toml_analysis_start
                    effective_parameters["ba_analysis_start_frame"] = toml_analysis_start
                    updates = {"area": 0, "intensity": 0}

                    def edit_thresholds(obj):
                        if isinstance(obj, dict):
                            for key, value in obj.items():
                                key_lower = str(key).lower()
                                if key_lower in {"area_ths", "area_thresholds"}:
                                    updates["area"] += _update_threshold_pairs(value, minimum=area_min, maximum=area_max)
                                elif key_lower in {"intensity_ths", "intensity_thresholds"}:
                                    updates["intensity"] += _update_threshold_pairs(value, minimum=background_min, maximum=None)
                                edit_thresholds(value)
                        elif isinstance(obj, list):
                            for value in obj:
                                edit_thresholds(value)

                    edit_thresholds(doc)
                    if updates["area"] == 0:
                        raise ValueError("No blob-area threshold pair was found in the TOML.")
                    if updates["intensity"] == 0:
                        raise ValueError("No intensity threshold pair was found in the TOML.")
                    _validate_thresholds(doc)
                    copied_toml = input_dir + "/" + Path(row["toml"]).name
                    rendered_toml = tomlkit.dumps(doc)
                    _validate_thresholds(tomlkit.parse(rendered_toml))
                    ssh.write(copied_toml, rendered_toml)
                    metadata = {
                        "attempt_index": attempt_index,
                        "run_index": attempt_index,
                        "run_timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                        "analysis_type": analysis,
                        "video_path": row["video"],
                        "video_filename": Path(row["video"]).name,
                        "toml_source_path": row["toml"],
                        "toml_run_copy_path": copied_toml,
                        "cell_label": row["cell"],
                        "remote_run_dir": run_dir,
                        "session_path": "",
                        "record_id": f"{video_stem}_{row['cell']}_A{attempt_index:05d}_{stamp}",
                        "parameters": {
                            "area_min": area_min,
                            "area_max": None if math.isinf(area_max) else area_max,
                            "area_max_is_infinite": math.isinf(area_max),
                            "background_intensity_min": background_min,
                            "run_mode": self.run_mode,
                            **effective_parameters,
                        },
                    }
                    ssh.write(metadata_path, json.dumps(metadata, indent=2, allow_nan=False))
                    env = {
                        "PIPELINE_REPO_ROOT": self.repo_root,
                        "PIPELINE_PROJECT_ROOT": self.project_root,
                        "PIPELINE_RUN_DIR": run_dir,
                        "PIPELINE_TOML": copied_toml,
                        "PIPELINE_VIDEO": row["video"],
                        "PIPELINE_ANALYSIS_TYPE": analysis,
                        "PIPELINE_METADATA_JSON": metadata_path,
                        "PIPELINE_SESSION_OUTPUT_DIR": session_out,
                        "PIPELINE_SESSION": "",
                        "PIPELINE_RUN_MODE": self.run_mode,
                        "PIPELINE_ARCHIVE_SESSION": "0",
                        "PIPELINE_POSTPROCESS_EXTRA_ARGS": self._postprocess_args(analysis, effective_parameters),
                    }
                    exports = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in env.items())
                    command = f"export {exports}; bash {shlex.quote(self.repo_root + '/scripts/firebird/submit_pipeline_run.sh')}"
                    self.progress.emit(f"Submitting SLURM jobs for {label}...")
                    result = ssh.run(command)
                    job_ids = {"IDTRACKER_JOB": "", "POSTPROCESS_JOB": "", "COLLECTOR_JOB": ""}
                    for output_line in result.splitlines():
                        if "=" in output_line:
                            key, value = output_line.split("=", 1)
                            if key in job_ids:
                                job_ids[key] = value.strip()
                    job = {
                        "attempt_index": attempt_index,
                        "run_index": attempt_index,
                        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                        "label": label,
                        "idtracker_job": job_ids["IDTRACKER_JOB"],
                        "postprocess_job": job_ids["POSTPROCESS_JOB"],
                        "collector_job": job_ids["COLLECTOR_JOB"],
                        "status": "Postprocess submitted" if self.run_mode == "postprocess" else "Tracking submitted",
                        "run_dir": run_dir,
                    }
                    self.job_submitted.emit(job)
                    messages.append(f"Attempt {attempt_index:05d}: {result.strip()}")
                except Exception as exc:
                    messages.append(f"{row['toml']}: ERROR {exc}")
                    self.progress.emit(f"Submission error for {label}: {exc}")
            self.finished.emit(messages)
        except Exception as exc:
            self.failed.emit(str(exc))

class RecoveryWorker(QObject):
    finished = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, host, key, project_root, repo_root):
        super().__init__()
        self.host = host; self.key = key
        self.project_root = project_root.rstrip('/')
        self.repo_root = repo_root.rstrip('/')

    def run(self):
        try:
            ssh = SSH(self.host, self.key)
            script = self.repo_root + '/scripts/firebird/recover_runs.py'
            command = (
                f"python {shlex.quote(script)} --project-root {shlex.quote(self.project_root)} "
                f"--repo-root {shlex.quote(self.repo_root)} --repair"
            )
            output = ssh.run(command, timeout=600)
            self.finished.emit(json.loads(output or '[]'))
        except Exception as exc:
            self.failed.emit(str(exc))


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.rows = []
        self.jobs = []
        self.index_thread = None
        self.submit_thread = None
        self.submit_worker = None
        self.index_worker = None
        self.recovery_thread = None
        self.recovery_worker = None
        self.index_db_path = str(
            Path.home() / '.beetle_idtracker' / 'firebird_index.sqlite3'
        )
        self.setWindowTitle("Beetle IDtracker Unified Pipeline")
        self.resize(1720, 980)
        self.setMinimumSize(1350, 780)
        tabs = QTabWidget()
        tabs.addTab(self.connection_page(), "1. Connection")
        tabs.addTab(self.scan_page(), "2. Scan and Match")
        tabs.addTab(self.parameters_page(), "3. Parameters")
        tabs.addTab(self.submit_page(), "4. Submit")
        tabs.addTab(self.diagnostics_page(), "5. Jobs and Diagnostics")
        tabs.addTab(self.qc_page(), "6. QC and Masters")
        self.setCentralWidget(tabs)
        QTimer.singleShot(800, self.recover_runs)

    def connection_page(self):
        page = QWidget()
        form = QFormLayout(page)
        self.host = QLineEdit(self.cfg["ssh_host"])
        self.key = QLineEdit(self.cfg["identity_file"])
        keyrow = QHBoxLayout()
        keyrow.addWidget(self.key)
        choose = QPushButton("Choose…")
        choose.clicked.connect(self.choose_key)
        keyrow.addWidget(choose)
        self.search_root = QLineEdit(self.cfg["remote_search_root"])
        self.project_root = QLineEdit(self.cfg["remote_project_root"])
        self.repo_root = QLineEdit(self.cfg["remote_repo_root"])
        form.addRow("SSH host", self.host)
        form.addRow("Private key", keyrow)
        form.addRow("Recursive Firebird search root", self.search_root)
        form.addRow("Firebird project-output root", self.project_root)
        form.addRow("Firebird unified repository root", self.repo_root)
        test = QPushButton("Test and Save")
        test.clicked.connect(self.test_save)
        form.addRow(test)
        return page

    def scan_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        note = QLabel(
            "Rebuild Firebird Index performs the remote recursive scan and "
            "stores parsed matches in a local SQLite index on this Mac. "
            "Search Existing Index reuses that local index without rescanning. "
            "Both actions refresh approval status from the QC master index on Firebird."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        controls = QHBoxLayout()

        self.search_index_button = QPushButton("Search Existing Index")
        self.search_index_button.clicked.connect(self.search_existing_index)
        controls.addWidget(self.search_index_button)

        self.rebuild_index_button = QPushButton("Rebuild Firebird Index")
        self.rebuild_index_button.clicked.connect(self.scan)
        controls.addWidget(self.rebuild_index_button)

        self.cancel_index_button = QPushButton("Cancel")
        self.cancel_index_button.setEnabled(False)
        self.cancel_index_button.clicked.connect(self.cancel_scan)
        controls.addWidget(self.cancel_index_button)

        layout.addLayout(controls)

        self.index_progress = QProgressBar()
        self.index_progress.setRange(0, 0)
        self.index_progress.setVisible(False)
        layout.addWidget(self.index_progress)

        self.summary = QLabel(
            "No index loaded. Rebuild the Firebird index the first time."
        )
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        search_filters = QHBoxLayout()
        search_filters.addWidget(QLabel("Search/filter:"))
        self.toml_filter = QLineEdit()
        self.toml_filter.setPlaceholderText(
            "Type any part of a video name, cell, TOML path, analysis type, QC status, record ID, notes, status, or reason"
        )
        self.toml_filter.textChanged.connect(self.apply_scan_filter)
        search_filters.addWidget(self.toml_filter, 1)
        clear_toml_filter = QPushButton("Clear Filter")
        clear_toml_filter.clicked.connect(self.toml_filter.clear)
        search_filters.addWidget(clear_toml_filter)
        layout.addLayout(search_filters)

        selection = QHBoxLayout()
        for label, action in [("Check All", self.check_all), ("Uncheck All", self.uncheck_all), ("Invert Selection", self.invert_selection)]:
            b = QPushButton(label); b.clicked.connect(action); selection.addWidget(b)
        selection.addStretch()
        selection.addWidget(QLabel("QC score/status:"))
        self.approved_filter = QComboBox()
        self.approved_filter.addItems(["All QC statuses", "Unscored only", "Scored only", "PENDING", "APPROVED", "NEEDS RERUN", "RERUNNING", "SUPERSEDED"])
        self.approved_filter.currentTextChanged.connect(self.apply_scan_filter)
        selection.addWidget(self.approved_filter)
        layout.addLayout(selection)

        self.table = QTableWidget(0, 11)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setHorizontalHeaderLabels([
            "Use",
            "Video",
            "Cell",
            "TOML",
            "Analysis",
            "Blob min",
            "Blob max",
            "Background threshold min",
            "QC score/status",
            "Status",
            "Reason",
        ])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            10,
            QHeaderView.Stretch,
        )
        layout.addWidget(self.table)
        return page

    def _set_checks(self, mode):
        for i in range(self.table.rowCount()):
            if self.table.isRowHidden(i):
                continue
            item=self.table.item(i,0)
            if item:
                if mode=='invert': item.setCheckState(Qt.Unchecked if item.checkState()==Qt.Checked else Qt.Checked)
                else: item.setCheckState(Qt.Checked if mode=='check' else Qt.Unchecked)
    def check_all(self): self._set_checks('check')
    def uncheck_all(self): self._set_checks('uncheck')
    def invert_selection(self): self._set_checks('invert')

    def parameters_page(self):
        page=QWidget(); layout=QVBoxLayout(page)
        self.run_full=QRadioButton('Run IDtracker + Postprocess'); self.run_post=QRadioButton('Postprocess Existing Session'); self.run_full.setChecked(True)
        group=QButtonGroup(page); group.addButton(self.run_full); group.addButton(self.run_post)
        layout.addWidget(self.run_full); layout.addWidget(self.run_post)
        archive_note=QLabel('IDtracker sessions remain in their canonical video-folder location. Copying or moving sessions is deferred and never performed during a run.'); archive_note.setWordWrap(True); layout.addWidget(archive_note)
        self.param={}
        specs={
          'Fight':[('analysis_start_frame','Analysis start frame (auto from TOML)',0,0,100000000,1),('analysis_stop_frame','Analysis stop frame',0,0,100000000,1),('contact_px','Contact distance (px)',60,0,10000,.1),('fight_px','Fight distance (px)',35,0,10000,.1),('min_fight_frames','Minimum fight duration (frames)',6,1,100000,1),('roi_wall_buffer_px','Wall buffer (px)',30,0,10000,.1),('window_frames','Window frames',7500,1,100000000,1)],
          'BA':[('ba_analysis_start_frame','Analysis start frame (auto from TOML)',0,0,100000000,1),('ba_analysis_stop_frame','Analysis stop frame',0,0,100000000,1),('ba_roi_wall_buffer_px','Wall buffer (px)',30,0,10000,.1),('move_threshold_px','Movement threshold (px)',30,0,10000,.1),('movement_onset_consecutive_frames','Movement onset duration (frames)',30,1,100000,1),('turtling_window_frames','Turtle window (frames)',300,1,100000,1),('turtling_min_duration_frames','Turtle minimum duration (frames)',300,1,100000,1)]}
        tips={'analysis_start_frame':'Automatically replaced with the first frame in the TOML tracking interval','ba_analysis_start_frame':'Automatically replaced with the first frame in the TOML tracking interval','analysis_stop_frame':'0 means use Window frames','ba_analysis_stop_frame':'0 means use all configured frames','contact_px':'Distance defining contact','fight_px':'Stricter fight-distance threshold','roi_wall_buffer_px':'Inward ROI border width','ba_roi_wall_buffer_px':'Inward ROI border width'}
        for title,rows in specs.items():
            box=QGroupBox(title); form=QFormLayout(box)
            for key,label,default,lo,hi,step in rows:
                w=QSpinBox() if step==1 else QDoubleSpinBox(); w.setRange(lo,hi); w.setValue(default); w.setSingleStep(step); w.setToolTip(tips.get(key,label)); self.param[key]=w
                if key in {'analysis_start_frame','ba_analysis_start_frame'}:
                    w.setEnabled(False)
                form.addRow(label,w)
            layout.addWidget(box)
        layout.addStretch(); return page

    def submit_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        text = QLabel(
            "Each selected row creates a run-specific TOML and metadata file, then "
            "submits either IDtracker + post-processing or post-processing only, followed by QC collection."
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        self.submit_button = QPushButton("Submit Selected Runs")
        self.submit_button.clicked.connect(self.submit)
        layout.addWidget(self.submit_button)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        layout.addStretch()
        return page


    def diagnostics_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        explanation = QLabel(
            "Select a submitted run, then retrieve SLURM state, accounting "
            "history, dependencies, exit codes, run metadata, session discovery, "
            "and the most recent stdout/stderr logs directly from Firebird."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.jobs_table = QTableWidget(0, 8)
        self.jobs_table.setHorizontalHeaderLabels([
            "Attempt", "Date/time", "Video/cell", "IDtracker job",
            "Post-process job", "Collector job", "Status", "Remote run folder"
        ])
        self.jobs_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.jobs_table.setAlternatingRowColors(True)
        self.jobs_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self.jobs_table.horizontalHeader().setSectionResizeMode(
            7, QHeaderView.Stretch
        )
        self.jobs_table.itemSelectionChanged.connect(
            self.retrieve_selected_diagnostics
        )
        layout.addWidget(self.jobs_table, 3)

        buttons = QHBoxLayout()

        recover = QPushButton("Recover Runs from Firebird")
        recover.clicked.connect(self.recover_runs)
        buttons.addWidget(recover)

        refresh_all = QPushButton("Refresh All Job States")
        refresh_all.clicked.connect(self.refresh_all_job_states)
        buttons.addWidget(refresh_all)

        diagnose = QPushButton("Diagnose Selected Run")
        diagnose.clicked.connect(self.retrieve_selected_diagnostics)
        buttons.addWidget(diagnose)

        logs = QPushButton("Fetch Selected Run Logs")
        logs.clicked.connect(self.fetch_selected_logs)
        buttons.addWidget(logs)

        sessions = QPushButton("Check Session Discovery")
        sessions.clicked.connect(self.check_selected_session)
        buttons.addWidget(sessions)

        download = QPushButton("Download Selected Results")
        download.clicked.connect(self.download_selected_results)
        buttons.addWidget(download)

        cancel_blocked = QPushButton("Cancel Blocked Dependents")
        cancel_blocked.clicked.connect(self.cancel_selected_blocked_jobs)
        buttons.addWidget(cancel_blocked)

        layout.addLayout(buttons)

        self.diagnostics_output = QTextEdit()
        self.diagnostics_output.setReadOnly(True)
        self.diagnostics_output.setPlaceholderText(
            "Diagnostics for the selected run will appear here."
        )
        self.diagnostics_output.setMaximumHeight(210)
        layout.addWidget(self.diagnostics_output, 1)
        return page

    def qc_page(self):
        page=QWidget();layout=QVBoxLayout(page)
        note=QLabel("Review completed runs. APPROVED adds a run to the appropriate master spreadsheet. NEEDS RERUN and RERUNNING remain excluded. When a newer replacement is approved, older matching rerun records are automatically marked SUPERSEDED.");note.setWordWrap(True);layout.addWidget(note)

        filters=QHBoxLayout();filters.addWidget(QLabel("Search/filter:"))
        self.qc_filter=QLineEdit();self.qc_filter.setPlaceholderText("Type any part of a record ID, date, camera/video name, cell, or status")
        self.qc_filter.textChanged.connect(self.apply_qc_filter);filters.addWidget(self.qc_filter,1)
        self.qc_status_filter=QComboBox();self.qc_status_filter.addItems(["All statuses","PENDING","APPROVED","NEEDS RERUN","RERUNNING","SUPERSEDED"]);self.qc_status_filter.currentTextChanged.connect(self.apply_qc_filter);filters.addWidget(self.qc_status_filter)
        clear=QPushButton("Clear Filter");clear.clicked.connect(lambda:(self.qc_filter.clear(),self.qc_status_filter.setCurrentIndex(0)));filters.addWidget(clear);layout.addLayout(filters)

        self.qc_table=QTableWidget(0,11);self.qc_table.setHorizontalHeaderLabels(["Record ID","Date run","Analysis","Video / Camera","Cell","Pipeline","QC status","Replaces","Replaced by","Notes","Run folder"]);self.qc_table.setSelectionBehavior(QAbstractItemView.SelectRows);self.qc_table.setSortingEnabled(True);self.qc_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents);self.qc_table.horizontalHeader().setSectionResizeMode(9,QHeaderView.Stretch);self.qc_table.horizontalHeader().setSectionResizeMode(10,QHeaderView.Stretch);layout.addWidget(self.qc_table)
        row=QHBoxLayout()
        for label,fn in [("Refresh QC",self.refresh_qc),("Save Notes",self.save_qc_notes),("Approve",lambda:self.set_qc_decision('APPROVED')),("Needs Rerun",lambda:self.set_qc_decision('RERUN')),("Mark Rerunning",lambda:self.set_qc_decision('RERUNNING')),("Mark Superseded",lambda:self.set_qc_decision('SUPERSEDED')),("Mark Pending",lambda:self.set_qc_decision('PENDING')),("Download Selected",self.download_selected_qc),("View Files",self.view_selected_qc_files),("Download Masters",self.download_master_spreadsheets)]:
            b=QPushButton(label);b.clicked.connect(fn);row.addWidget(b)
        layout.addLayout(row);self.qc_message=QLabel('');self.qc_message.setWordWrap(True);layout.addWidget(self.qc_message);return page

    def refresh_qc(self):
        try:
            text=self.ssh().run(f"cat {shlex.quote(self.project_root.text().rstrip('/')+'/QC/run_status.csv')} 2>/dev/null || true")
            import csv,io
            rows=list(csv.DictReader(io.StringIO(text))) if text.strip() else []
            self.qc_rows=rows;self.qc_table.setSortingEnabled(False);self.qc_table.setRowCount(len(rows))
            labels={'DONE':'APPROVED','RERUN':'NEEDS RERUN'}
            for i,r in enumerate(rows):
                status=labels.get((r.get('qc_decision') or 'PENDING').upper(),(r.get('qc_decision') or 'PENDING').upper())
                vals=[r.get('record_id',''),r.get('date_run',''),r.get('analysis',''),r.get('video',''),r.get('cell',''),r.get('pipeline_status',''),status,r.get('replaces',''),r.get('replaced_by',''),r.get('notes',''),r.get('run_dir','')]
                for c,v in enumerate(vals):
                    item=QTableWidgetItem(str(v));item.setData(Qt.UserRole,r)
                    if c != 9: item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    self.qc_table.setItem(i,c,item)
            self.qc_table.setSortingEnabled(True);self.apply_qc_filter();self.qc_message.setText(f"Loaded {len(rows)} collected runs.")
        except Exception as exc: QMessageBox.critical(self,'QC refresh failed',str(exc))

    def apply_qc_filter(self):
        if not hasattr(self,'qc_table'): return
        needle=(self.qc_filter.text() if hasattr(self,'qc_filter') else '').strip().lower()
        selected=(self.qc_status_filter.currentText() if hasattr(self,'qc_status_filter') else 'All statuses').upper()
        for row in range(self.qc_table.rowCount()):
            hay=' '.join((self.qc_table.item(row,c).text() if self.qc_table.item(row,c) else '') for c in range(self.qc_table.columnCount())).lower()
            status=(self.qc_table.item(row,6).text() if self.qc_table.item(row,6) else '').upper()
            visible=(not needle or needle in hay) and (selected=='ALL STATUSES' or status==selected)
            self.qc_table.setRowHidden(row,not visible)

    def selected_qc(self):
        rows=self.qc_table.selectionModel().selectedRows() if hasattr(self,'qc_table') else []
        return self.qc_table.item(rows[0].row(),0).data(Qt.UserRole) if rows else None

    def _selected_qc_note(self):
        rows=self.qc_table.selectionModel().selectedRows() if hasattr(self,'qc_table') else []
        if not rows:return ''
        item=self.qc_table.item(rows[0].row(),9)
        return item.text().strip() if item else ''

    def save_qc_notes(self):
        rec=self.selected_qc()
        if not rec:return
        decision=(rec.get('qc_decision') or 'PENDING').upper()
        try:
            note=self._selected_qc_note()
            cmd=f"{shlex.quote(self.repo_root.text().rstrip('/')+'/scripts/firebird/set_qc_status.sh')} {shlex.quote(self.project_root.text())} {shlex.quote(rec['record_id'])} {shlex.quote(decision)} {shlex.quote(note)}"
            result=self.ssh().run(cmd);self.qc_message.setText(f"Saved notes for {rec['record_id']}. {result.strip()}");self.refresh_qc()
        except Exception as exc: QMessageBox.critical(self,'QC notes update failed',str(exc))

    def set_qc_decision(self,decision):
        rec=self.selected_qc()
        if not rec:return
        try:
            note=self._selected_qc_note()
            cmd=f"{shlex.quote(self.repo_root.text().rstrip('/')+'/scripts/firebird/set_qc_status.sh')} {shlex.quote(self.project_root.text())} {shlex.quote(rec['record_id'])} {shlex.quote(decision)} {shlex.quote(note)}"
            result=self.ssh().run(cmd);self.qc_message.setText(f"{rec['record_id']} marked {decision}. {result.strip()}");self.refresh_qc()
        except Exception as exc: QMessageBox.critical(self,'QC update failed',str(exc))

    def _download_remote(self,remote_path,local_name):
        destination=Path.home()/"Downloads"/"IDtracker_Results"/local_name;destination.parent.mkdir(parents=True,exist_ok=True)
        cmd=['rsync','-av','-e',f'ssh -i {str(Path(self.key.text()).expanduser())}',f'{self.host.text().strip()}:{remote_path.rstrip("/")}/',str(destination)+'/']
        result=subprocess.run(cmd,capture_output=True,text=True)
        if result.returncode:raise RuntimeError(result.stderr or result.stdout)
        return destination

    def download_selected_results(self):
        job=self.selected_job()
        if not job:return
        try:
            dest=self._download_remote(job['run_dir']+'/outputs',f"{Path(job['run_dir']).name}_outputs");QMessageBox.information(self,'Download complete',f'Results downloaded to:\n{dest}')
        except Exception as exc:QMessageBox.critical(self,'Download failed',str(exc))

    def _download_qc_bundle(self,rec):
        # The collector creates one flat folder containing only the files needed
        # for QC: individual summaries plus fight PDF or BA track PNGs.
        remote=rec['run_dir'].rstrip('/')+'/outputs/QC_review_bundle'
        try:
            return self._download_remote(remote,rec['record_id'])
        except Exception:
            # Backward-compatible fallback for runs collected before v0.9.2.
            return self._download_remote(rec['run_dir'].rstrip('/')+'/outputs',rec['record_id'])

    def download_selected_qc(self):
        rec=self.selected_qc()
        if not rec:return
        try:
            dest=self._download_qc_bundle(rec);QMessageBox.information(self,'Download complete',f'QC files downloaded to one folder:\n{dest}')
        except Exception as exc:QMessageBox.critical(self,'Download failed',str(exc))

    def view_selected_qc_files(self):
        rec=self.selected_qc()
        if not rec:return
        try:
            dest=self._download_qc_bundle(rec)
            subprocess.run(['open',str(dest)],check=True)
            pdfs=sorted(dest.glob('*_tracks.pdf'))
            if pdfs: subprocess.run(['open',str(pdfs[0])],check=False)
            self.qc_message.setText(f'Opened QC files for {rec["record_id"]}: {dest}')
        except Exception as exc:QMessageBox.critical(self,'View files failed',str(exc))

    def download_master_spreadsheets(self):
        try:
            remote=self.project_root.text().rstrip('/')+'/QC/master_summaries';dest=self._download_remote(remote,'Master_Summaries');QMessageBox.information(self,'Download complete',f'Master spreadsheets downloaded to:\n{dest}')
        except Exception as exc:QMessageBox.critical(self,'Download failed',str(exc))

    def ssh(self):
        return SSH(self.host.text().strip(), self.key.text().strip())

    def choose_key(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose SSH private key", str(Path.home() / ".ssh")
        )
        if path:
            self.key.setText(path)

    def test_save(self):
        try:
            output = self.ssh().run("hostname && whoami")
            self.cfg = {
                "ssh_host": self.host.text().strip(),
                "identity_file": self.key.text().strip(),
                "remote_search_root": self.search_root.text().strip(),
                "remote_project_root": self.project_root.text().strip(),
                "remote_repo_root": self.repo_root.text().strip(),
            }
            CONFIG.write_text(json.dumps(self.cfg, indent=2))
            QMessageBox.information(self, "Connection works", output)
        except Exception as exc:
            QMessageBox.critical(self, "Connection failed", str(exc))

    def scan(self):
        self.start_index_operation(rebuild=True)

    def search_existing_index(self):
        self.start_index_operation(rebuild=False)

    def start_index_operation(self, rebuild):
        if self.index_thread is not None:
            QMessageBox.information(
                self,
                "Index operation running",
                "Please wait for the current index operation to finish.",
            )
            return

        search_root = self.search_root.text().strip()
        if not search_root:
            QMessageBox.warning(
                self,
                "Missing search root",
                "Enter the recursive Firebird search root on the Connection tab.",
            )
            return

        self.search_index_button.setEnabled(False)
        self.rebuild_index_button.setEnabled(False)
        self.cancel_index_button.setEnabled(True)
        self.index_progress.setVisible(True)
        self.summary.setText(
            "Rebuilding Firebird index..."
            if rebuild
            else "Loading existing Mac index..."
        )

        self.index_thread = QThread(self)
        self.index_worker = IndexWorker(
            self.host.text().strip(),
            self.key.text().strip(),
            search_root,
            self.project_root.text().strip(),
            self.index_db_path,
            rebuild,
        )
        self.index_worker.moveToThread(self.index_thread)

        self.index_thread.started.connect(self.index_worker.run)
        self.index_worker.progress.connect(self.on_index_progress)
        self.index_worker.finished.connect(self.on_index_finished)
        self.index_worker.failed.connect(self.on_index_failed)
        self.index_worker.cancelled.connect(self.on_index_cancelled)

        self.index_worker.finished.connect(self.index_thread.quit)
        self.index_worker.failed.connect(self.index_thread.quit)
        self.index_worker.cancelled.connect(self.index_thread.quit)
        self.index_thread.finished.connect(self.cleanup_index_thread)

        self.index_thread.start()

    def cancel_scan(self):
        if self.index_worker is not None:
            self.summary.setText("Cancelling index operation...")
            self.cancel_index_button.setEnabled(False)
            self.index_worker.cancel()

    def on_index_progress(self, message):
        self.summary.setText(message)

    def on_index_finished(
        self,
        rows,
        video_count,
        toml_count,
        matched_count,
        indexed_at,
    ):
        self.rows = rows
        self.populate()
        timestamp = datetime.fromtimestamp(indexed_at).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        approved_count = sum(row.get("qc_status") == "APPROVED" for row in rows)
        rerun_count = sum(row.get("qc_status") == "NEEDS RERUN" for row in rows)
        scored_count = sum(row.get("qc_status") not in (None, "", "UNSCORED") for row in rows)
        self.summary.setText(
            f"Index dated {timestamp}: {video_count:,} videos, "
            f"{toml_count:,} TOMLs, {matched_count:,} exact matches; "
            f"{scored_count:,} scored ({approved_count:,} approved, {rerun_count:,} need rerun)."
        )
        self.finish_index_ui()

    def on_index_failed(self, message):
        self.summary.setText("Index operation failed.")
        self.finish_index_ui()
        QMessageBox.critical(self, "Index operation failed", message)

    def on_index_cancelled(self):
        self.summary.setText("Index operation cancelled.")
        self.finish_index_ui()

    def finish_index_ui(self):
        self.search_index_button.setEnabled(True)
        self.rebuild_index_button.setEnabled(True)
        self.cancel_index_button.setEnabled(False)
        self.index_progress.setVisible(False)

    def cleanup_index_thread(self):
        if self.index_worker is not None:
            self.index_worker.deleteLater()
        if self.index_thread is not None:
            self.index_thread.deleteLater()
        self.index_worker = None
        self.index_thread = None

    def populate(self):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.rows))
        for i, row in enumerate(self.rows):
            use = QTableWidgetItem()
            use.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            use.setCheckState(Qt.Unchecked)
            use.setData(Qt.UserRole, row)
            self.table.setItem(i, 0, use)
            for c, key in enumerate(("video", "cell", "toml"), 1):
                self.table.setItem(i, c, QTableWidgetItem(str(row[key])))
            combo = QComboBox()
            combo.addItems(["ba", "fight"])
            combo.setCurrentText(row["analysis"])
            self.table.setCellWidget(i, 4, combo)
            qc_status = row.get("qc_status", "UNSCORED") or "UNSCORED"
            qc_text = qc_status
            if row.get("qc_date"):
                qc_text += f" | {row['qc_date']}"
            if row.get("qc_record_id"):
                qc_text += f" | {row['qc_record_id']}"
            if row.get("qc_notes"):
                qc_text += f" | {row['qc_notes']}"
            self.table.setItem(i, 8, QTableWidgetItem(qc_text))
            for c, key in ((5, "area_min"), (6, "area_max"), (7, "background_min"), (9, "status"), (10, "reason")):
                self.table.setItem(i, c, QTableWidgetItem(str(row[key])))
        self.table.setSortingEnabled(True)
        self.apply_scan_filter()

    def apply_scan_filter(self):
        if not hasattr(self, "table") or not hasattr(self, "approved_filter"):
            return
        mode = self.approved_filter.currentText()
        needle = (self.toml_filter.text() if hasattr(self, "toml_filter") else "").strip().lower()
        for table_row in range(self.table.rowCount()):
            item = self.table.item(table_row, 0)
            data = item.data(Qt.UserRole) if item else {}
            qc_status = (data.get("qc_status", "UNSCORED") if isinstance(data, dict) else "UNSCORED") or "UNSCORED"
            qc_status = normalize_qc_status(qc_status)

            values = []
            for column in range(self.table.columnCount()):
                widget = self.table.cellWidget(table_row, column)
                if isinstance(widget, QComboBox):
                    values.append(widget.currentText())
                table_item = self.table.item(table_row, column)
                if table_item is not None:
                    values.append(table_item.text())
            haystack = " ".join(values).lower()

            if mode == "All QC statuses":
                status_visible = True
            elif mode == "Unscored only":
                status_visible = qc_status == "UNSCORED"
            elif mode == "Scored only":
                status_visible = qc_status != "UNSCORED"
            else:
                status_visible = qc_status == mode.upper()
            search_visible = not needle or needle in haystack
            self.table.setRowHidden(table_row, not (status_visible and search_visible))

    def postprocess_args(self, analysis):
        p=self.param
        if analysis=='fight':
            vals={'analysis-stop-frame':p['analysis_stop_frame'].value(),'window-frames':p['window_frames'].value(),'contact-px':p['contact_px'].value(),'fight-px':p['fight_px'].value(),'min-fight-frames':p['min_fight_frames'].value(),'roi-wall-buffer-px':p['roi_wall_buffer_px'].value()}
        else:
            vals={'analysis-stop-frame':p['ba_analysis_stop_frame'].value(),'move-threshold-px':p['move_threshold_px'].value(),'movement-onset-consecutive-frames':p['movement_onset_consecutive_frames'].value(),'roi-wall-buffer-px':p['ba_roi_wall_buffer_px'].value(),'turtling-window-frames':p['turtling_window_frames'].value(),'turtling-min-duration-frames':p['turtling_min_duration_frames'].value()}
        return ' '.join(f'--{k} {v}' for k,v in vals.items())

    def submit(self):
        if getattr(self, "submit_thread", None) is not None:
            QMessageBox.information(self, "Submission in progress", "A submission is already running.")
            return

        selected = []
        for i in range(self.table.rowCount()):
            use_item = self.table.item(i, 0)
            if use_item is None or use_item.checkState() != Qt.Checked:
                continue
            row = use_item.data(Qt.UserRole)
            if not isinstance(row, dict):
                continue
            try:
                selected.append({
                    "row": dict(row),
                    "analysis": self.table.cellWidget(i, 4).currentText(),
                    "area_min": float(self.table.item(i, 5).text()),
                    "area_max": float(self.table.item(i, 6).text()),
                    "background_min": float(self.table.item(i, 7).text()),
                })
            except Exception as exc:
                QMessageBox.critical(self, "Invalid row parameters", f"Could not read parameters for row {i + 1}: {exc}")
                return

        if not selected:
            self.result.setText("No rows selected.")
            return

        parameters = {key: widget.value() for key, widget in self.param.items()}
        run_mode = "postprocess" if self.run_post.isChecked() else "full"
        self.submit_button.setEnabled(False)
        self.result.setText(f"Starting submission of {len(selected)} selected run(s)...")
        QApplication.processEvents()

        self.submit_thread = QThread(self)
        self.submit_worker = SubmitWorker(
            self.host.text().strip(),
            self.key.text().strip(),
            self.project_root.text().strip(),
            self.repo_root.text().strip(),
            selected,
            run_mode,
            parameters,
        )
        self.submit_worker.moveToThread(self.submit_thread)
        self.submit_thread.started.connect(self.submit_worker.run)
        self.submit_worker.progress.connect(self.result.setText)
        self.submit_worker.job_submitted.connect(self._submission_job_added)
        self.submit_worker.finished.connect(self._submission_finished)
        self.submit_worker.failed.connect(self._submission_failed)
        self.submit_worker.finished.connect(self.submit_thread.quit)
        self.submit_worker.failed.connect(self.submit_thread.quit)
        self.submit_thread.finished.connect(self._cleanup_submit_thread)
        self.submit_thread.start()

    def _submission_job_added(self, job):
        self.jobs.append(job)
        self.populate_jobs_table()

    def _submission_finished(self, messages):
        self.result.setText("\n".join(messages) if messages else "Submission completed with no output.")
        self.submit_button.setEnabled(True)

    def _submission_failed(self, message):
        self.result.setText(f"Submission failed: {message}")
        self.submit_button.setEnabled(True)
        QMessageBox.critical(self, "Submission failed", message)

    def _cleanup_submit_thread(self):
        if getattr(self, "submit_worker", None) is not None:
            self.submit_worker.deleteLater()
        if getattr(self, "submit_thread", None) is not None:
            self.submit_thread.deleteLater()
        self.submit_worker = None
        self.submit_thread = None



    def populate_jobs_table(self):
        if not hasattr(self, "jobs_table"):
            return
        self.jobs_table.setRowCount(len(self.jobs))
        for row_number, job in enumerate(self.jobs):
            values = [
                f"{int(job.get('attempt_index', job.get('run_index', 1))):05d}",
                job["timestamp"],
                job["label"],
                job.get("idtracker_job", ""),
                job.get("postprocess_job", ""),
                job.get("collector_job", ""),
                job.get("status", "Submitted"),
                job["run_dir"],
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.jobs_table.setItem(row_number, column, item)


    def recover_runs(self):
        if self.recovery_thread is not None:
            return
        if hasattr(self, 'diagnostics_output'):
            self.diagnostics_output.setPlainText('Recovering prior runs from Firebird and repairing any completed-but-uncollected outputs...')
        self.recovery_thread = QThread(self)
        self.recovery_worker = RecoveryWorker(
            self.host.text().strip(), self.key.text().strip(),
            self.project_root.text().strip(), self.repo_root.text().strip()
        )
        self.recovery_worker.moveToThread(self.recovery_thread)
        self.recovery_thread.started.connect(self.recovery_worker.run)
        self.recovery_worker.finished.connect(self._recovery_finished)
        self.recovery_worker.failed.connect(self._recovery_failed)
        self.recovery_worker.finished.connect(self.recovery_thread.quit)
        self.recovery_worker.failed.connect(self.recovery_thread.quit)
        self.recovery_thread.finished.connect(self._cleanup_recovery_thread)
        self.recovery_thread.start()

    def _recovery_finished(self, recovered):
        dedup = {j['run_dir']: j for j in self.jobs}
        for job in recovered:
            dedup[job['run_dir']] = job
        self.jobs = sorted(dedup.values(), key=lambda j: j.get('timestamp',''), reverse=True)
        self.populate_jobs_table()
        repaired = sum(j.get('status') == 'Recovery collector submitted' for j in recovered)
        self.diagnostics_output.setPlainText(
            f"Recovered {len(recovered)} run(s) from Firebird. "
            f"Submitted {repaired} missing collector job(s). Refresh QC after those collector jobs finish."
        )
        self.refresh_qc()

    def _recovery_failed(self, message):
        if hasattr(self, 'diagnostics_output'):
            self.diagnostics_output.setPlainText(
                f"Recovery could not connect to Firebird:\n{message}"
            )

    def _cleanup_recovery_thread(self):
        if self.recovery_worker is not None: self.recovery_worker.deleteLater()
        if self.recovery_thread is not None: self.recovery_thread.deleteLater()
        self.recovery_worker = None; self.recovery_thread = None

    def selected_job(self):
        if not hasattr(self, "jobs_table"):
            return None
        rows = self.jobs_table.selectionModel().selectedRows()
        if not rows:
            return None
        index = rows[0].row()
        if not 0 <= index < len(self.jobs):
            return None
        return self.jobs[index]

    def retrieve_selected_diagnostics(self):
        job = self.selected_job()
        if job is None:
            return
        try:
            script = (
                self.repo_root.text().rstrip("/")
                + "/scripts/firebird/diagnose_pipeline_run.sh"
            )
            command = (
                f"bash {shlex.quote(script)} "
                f"{shlex.quote(job['run_dir'])}"
            )
            output = self.ssh().run(command, timeout=300)
            self.diagnostics_output.setPlainText(output)
        except Exception as exc:
            self.diagnostics_output.setPlainText(
                f"Could not retrieve diagnostics:\n{exc}"
            )

    def fetch_selected_logs(self):
        job = self.selected_job()
        if job is None:
            QMessageBox.warning(
                self, "Select a run", "Select a run in the table first."
            )
            return
        try:
            run_dir = job["run_dir"]
            command = (
                f"if [[ -d {shlex.quote(run_dir + '/logs')} ]]; then "
                f"for f in {shlex.quote(run_dir + '/logs')}/*.out "
                f"{shlex.quote(run_dir + '/logs')}/*.err; do "
                f"[[ -f \"$f\" ]] || continue; "
                f"echo; echo \"===== $f =====\"; tail -n 300 \"$f\"; done; "
                "else echo 'No logs directory exists.'; fi"
            )
            self.diagnostics_output.setPlainText(
                self.ssh().run(command, timeout=300)
            )
        except Exception as exc:
            QMessageBox.critical(self, "Log retrieval failed", str(exc))

    def check_selected_session(self):
        job = self.selected_job()
        if job is None:
            QMessageBox.warning(
                self, "Select a run", "Select a run in the table first."
            )
            return
        try:
            run_dir = job["run_dir"]
            command = (
                f"echo 'Run: {shlex.quote(run_dir)}'; "
                f"if [[ -f {shlex.quote(run_dir + '/session_path.txt')} ]]; then "
                f"echo 'Recorded session:'; "
                f"cat {shlex.quote(run_dir + '/session_path.txt')}; "
                f"session=$(cat {shlex.quote(run_dir + '/session_path.txt')}); "
                f"echo; echo 'Session files:'; "
                f"find \"$session\" -maxdepth 4 -type f "
                r"\( -name 'trajectories*.npy' -o -name 'trajectories*.h5' "
                r"-o -name 'session.json' -o -name 'attributes.json' \) "
                r"-printf '%TY-%Tm-%Td %TH:%TM:%TS\t%p\n' 2>/dev/null | sort; "
                f"else echo 'session_path.txt is missing'; fi; "
                f"echo; echo 'All session_* directories under run:'; "
                f"find {shlex.quote(run_dir)} -type d -name 'session_*' "
                r"-printf '%TY-%Tm-%Td %TH:%TM:%TS\t%p\n' 2>/dev/null "
                "| sort -r | head -30"
            )
            self.diagnostics_output.setPlainText(
                self.ssh().run(command, timeout=300)
            )
        except Exception as exc:
            QMessageBox.critical(
                self, "Session check failed", str(exc)
            )

    def refresh_all_job_states(self):
        if not self.jobs:
            QMessageBox.information(
                self, "No jobs", "No runs have been submitted in this session."
            )
            return

        for job in self.jobs:
            ids = [
                job.get("idtracker_job", ""),
                job.get("postprocess_job", ""),
                job.get("collector_job", ""),
            ]
            ids = [job_id for job_id in ids if job_id]
            if not ids:
                job["status"] = "No jobs"
                continue

            joined = ",".join(ids)
            command = (
                f"squeue -h -j {shlex.quote(joined)} -o '%i|%T|%R'; "
                f"sacct -n -X -P -j {shlex.quote(joined)} "
                "--format=JobIDRaw,State,ExitCode,Elapsed"
            )
            try:
                output = self.ssh().run(command)
                states = []
                for line in output.splitlines():
                    if "|" not in line:
                        continue
                    fields = line.split("|")
                    if len(fields) >= 2 and fields[0] in ids:
                        states.append(
                            f"{fields[0]}:{fields[1]}"
                        )
                job["status"] = ", ".join(dict.fromkeys(states)) or "Unknown"
            except Exception as exc:
                job["status"] = f"Error: {exc}"

        self.populate_jobs_table()

    def cancel_selected_blocked_jobs(self):
        job = self.selected_job()
        if job is None:
            QMessageBox.warning(
                self, "Select a run", "Select a run in the table first."
            )
            return

        candidates = [
            job.get("postprocess_job", ""),
            job.get("collector_job", ""),
        ]
        candidates = [job_id for job_id in candidates if job_id]
        if not candidates:
            QMessageBox.information(
                self, "No dependent jobs", "No dependent job IDs are recorded."
            )
            return

        joined = " ".join(shlex.quote(job_id) for job_id in candidates)
        try:
            states = self.ssh().run(
                f"squeue -h -j {shlex.quote(','.join(candidates))} "
                "-o '%i|%T|%R'"
            )
            blocked = []
            for line in states.splitlines():
                fields = line.split("|", 2)
                if len(fields) != 3:
                    continue
                job_id, state, reason = fields
                if state == "PENDING" and (
                    "Dependency" in reason
                    or "DependencyNeverSatisfied" in reason
                ):
                    blocked.append(job_id)

            if not blocked:
                QMessageBox.information(
                    self,
                    "No blocked jobs",
                    "No pending dependency-blocked jobs were found.",
                )
                return

            answer = QMessageBox.question(
                self,
                "Cancel blocked jobs",
                "Cancel these blocked dependent jobs?\n\n"
                + "\n".join(blocked),
            )
            if answer == QMessageBox.Yes:
                self.ssh().run(
                    "scancel "
                    + " ".join(shlex.quote(x) for x in blocked)
                )
                QMessageBox.information(
                    self,
                    "Cancelled",
                    "Cancelled: " + ", ".join(blocked),
                )
                self.refresh_all_job_states()
        except Exception as exc:
            QMessageBox.critical(
                self, "Cancellation failed", str(exc)
            )




    def closeEvent(self, event):
        if self.index_worker is not None:
            self.index_worker.cancel()
        if self.submit_thread is not None:
            self.submit_thread.quit()
            self.submit_thread.wait(3000)
        if self.index_thread is not None:
            self.index_thread.quit()
            self.index_thread.wait(3000)
        event.accept()


def validate_window_class():
    required = {
        "connection_page",
        "scan_page",
        "submit_page",
        "diagnostics_page",
        "ssh",
        "choose_key",
        "test_save",
        "scan",
        "populate",
        "submit",
        "populate_jobs_table",
        "selected_job",
        "retrieve_selected_diagnostics",
        "fetch_selected_logs",
        "check_selected_session",
        "refresh_all_job_states",
        "cancel_selected_blocked_jobs",
        "closeEvent",
        "cleanup_index_thread",
        "on_index_finished",
        "cancel_scan",
        "start_index_operation",
        "search_existing_index",
    }
    missing = sorted(name for name in required if not hasattr(Window, name))
    if missing:
        raise RuntimeError(
            "Mac GUI is incomplete. Missing Window methods: "
            + ", ".join(missing)
        )


def main():
    validate_window_class()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = Window()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
