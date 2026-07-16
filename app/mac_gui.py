from __future__ import annotations
import json
import math
import sqlite3
import time
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, QObject, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QPushButton, QLabel, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QFileDialog, QComboBox, QCheckBox, QTextEdit,
    QAbstractItemView, QProgressBar, QGroupBox, QSpinBox, QDoubleSpinBox,
    QRadioButton, QButtonGroup
)
import tomlkit


BASE = Path(__file__).resolve().parents[1]
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

    def __init__(self, host, key, search_root, db_path, rebuild):
        super().__init__()
        self.host = host
        self.key = key
        self.search_root = search_root
        self.db_path = db_path
        self.rebuild = rebuild
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        index = None
        try:
            index = LocalIndex(self.db_path)

            if not self.rebuild:
                rows, summary = index.load(self.search_root)
                if summary is None:
                    raise RuntimeError(
                        "No saved index exists for this Firebird search root. "
                        "Use Rebuild Firebird Index first."
                    )
                video_count, toml_count, matched_count, indexed_at = summary
                self.finished.emit(
                    rows,
                    int(video_count),
                    int(toml_count),
                    int(matched_count),
                    float(indexed_at),
                )
                return

            ssh = SSH(self.host, self.key)
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



class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.rows = []
        self.jobs = []
        self.index_thread = None
        self.index_worker = None
        self.index_db_path = str(
            Path.home() / '.beetle_idtracker' / 'firebird_index.sqlite3'
        )
        self.setWindowTitle("Beetle IDtracker Unified Pipeline")
        self.resize(1500, 850)
        tabs = QTabWidget()
        tabs.addTab(self.connection_page(), "1. Connection")
        tabs.addTab(self.scan_page(), "2. Scan and Match")
        tabs.addTab(self.parameters_page(), "3. Parameters")
        tabs.addTab(self.submit_page(), "4. Submit")
        tabs.addTab(self.diagnostics_page(), "5. Jobs and Diagnostics")
        self.setCentralWidget(tabs)

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
            "Search Existing Index reuses that local index without rescanning."
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

        selection = QHBoxLayout()
        for label, action in [("Check All", self.check_all), ("Uncheck All", self.uncheck_all), ("Invert Selection", self.invert_selection)]:
            b = QPushButton(label); b.clicked.connect(action); selection.addWidget(b)
        selection.addStretch(); layout.addLayout(selection)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels([
            "Use",
            "Video",
            "Cell",
            "TOML",
            "Analysis",
            "Blob min",
            "Blob max",
            "Background threshold min",
            "Status",
            "Reason",
        ])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            9,
            QHeaderView.Stretch,
        )
        layout.addWidget(self.table)
        return page

    def _set_checks(self, mode):
        for i in range(self.table.rowCount()):
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
          'Fight':[('analysis_stop_frame','Analysis stop frame',0,0,100000000,1),('contact_px','Contact distance (px)',60,0,10000,.1),('fight_px','Fight distance (px)',35,0,10000,.1),('min_fight_frames','Minimum fight duration (frames)',6,1,100000,1),('roi_wall_buffer_px','Wall buffer (px)',30,0,10000,.1),('window_frames','Window frames',7200,1,100000000,1)],
          'BA':[('ba_analysis_stop_frame','Analysis stop frame',0,0,100000000,1),('ba_roi_wall_buffer_px','Wall buffer (px)',30,0,10000,.1),('move_threshold_px','Movement threshold (px)',30,0,10000,.1),('movement_onset_consecutive_frames','Movement onset duration (frames)',30,1,100000,1),('turtling_window_frames','Turtle window (frames)',300,1,100000,1),('turtling_min_duration_frames','Turtle minimum duration (frames)',300,1,100000,1)]}
        tips={'analysis_stop_frame':'0 means use Window frames','ba_analysis_stop_frame':'0 means use all configured frames','contact_px':'Distance defining contact','fight_px':'Stricter fight-distance threshold','roi_wall_buffer_px':'Inward ROI border width','ba_roi_wall_buffer_px':'Inward ROI border width'}
        for title,rows in specs.items():
            box=QGroupBox(title); form=QFormLayout(box)
            for key,label,default,lo,hi,step in rows:
                w=QSpinBox() if step==1 else QDoubleSpinBox(); w.setRange(lo,hi); w.setValue(default); w.setSingleStep(step); w.setToolTip(tips.get(key,label)); self.param[key]=w; form.addRow(label,w)
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
            "Run", "Date/time", "Video/cell", "IDtracker job",
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
        layout.addWidget(self.jobs_table)

        buttons = QHBoxLayout()

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

        cancel_blocked = QPushButton("Cancel Blocked Dependents")
        cancel_blocked.clicked.connect(self.cancel_selected_blocked_jobs)
        buttons.addWidget(cancel_blocked)

        layout.addLayout(buttons)

        self.diagnostics_output = QTextEdit()
        self.diagnostics_output.setReadOnly(True)
        self.diagnostics_output.setPlaceholderText(
            "Diagnostics for the selected run will appear here."
        )
        layout.addWidget(self.diagnostics_output, 1)
        return page

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
        self.summary.setText(
            f"Index dated {timestamp}: {video_count:,} videos, "
            f"{toml_count:,} TOMLs, {matched_count:,} exact matches."
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
        self.table.setRowCount(len(self.rows))
        for i, row in enumerate(self.rows):
            use = QTableWidgetItem()
            use.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            use.setCheckState(Qt.Checked if row["use"] else Qt.Unchecked)
            self.table.setItem(i, 0, use)
            for c, key in enumerate(("video", "cell", "toml"), 1):
                self.table.setItem(i, c, QTableWidgetItem(str(row[key])))
            combo = QComboBox()
            combo.addItems(["ba", "fight"])
            combo.setCurrentText(row["analysis"])
            self.table.setCellWidget(i, 4, combo)
            for c, key in ((5, "area_min"), (6, "area_max"), (7, "background_min"), (8, "status"), (9, "reason")):
                self.table.setItem(i, c, QTableWidgetItem(str(row[key])))

    def postprocess_args(self, analysis):
        p=self.param
        if analysis=='fight':
            vals={'analysis-stop-frame':p['analysis_stop_frame'].value(),'window-frames':p['window_frames'].value(),'contact-px':p['contact_px'].value(),'fight-px':p['fight_px'].value(),'min-fight-frames':p['min_fight_frames'].value(),'roi-wall-buffer-px':p['roi_wall_buffer_px'].value()}
        else:
            vals={'analysis-stop-frame':p['ba_analysis_stop_frame'].value(),'move-threshold-px':p['move_threshold_px'].value(),'movement-onset-consecutive-frames':p['movement_onset_consecutive_frames'].value(),'roi-wall-buffer-px':p['ba_roi_wall_buffer_px'].value(),'turtling-window-frames':p['turtling_window_frames'].value(),'turtling-min-duration-frames':p['turtling_min_duration_frames'].value()}
        return ' '.join(f'--{k} {v}' for k,v in vals.items())

    def submit(self):
        ssh = self.ssh()
        messages = []
        for i, row in enumerate(self.rows):
            if self.table.item(i, 0).checkState() != Qt.Checked:
                continue
            try:
                analysis = self.table.cellWidget(i, 4).currentText()
                area_min = float(self.table.item(i, 5).text())
                area_max = float(self.table.item(i, 6).text())
                background_min = float(self.table.item(i, 7).text())
                now = datetime.now()
                stamp = now.strftime("%Y%m%d_%H%M%S")
                run_index = int(ssh.run(
                    f"mkdir -p {shlex.quote(self.project_root.text())}; "
                    f"if [[ -f {shlex.quote(self.project_root.text() + '/run_index.tsv')} ]]; then "
                    f"awk -F'\\t' 'NR>1 && $1+0>m{{m=$1+0}} END{{print m+1}}' "
                    f"{shlex.quote(self.project_root.text() + '/run_index.tsv')}; else echo 1; fi"
                ).strip())
                video_stem = Path(row["video"]).stem
                run_dir = (
                    f"{self.project_root.text().rstrip('/')}/runs/{video_stem}/"
                    f"{row['cell']}/run_{run_index:05d}_{stamp}"
                )
                input_dir = run_dir + "/input"
                session_out = run_dir + "/idtracker"
                metadata_path = run_dir + "/run_metadata.json"
                ssh.run(f"mkdir -p {shlex.quote(input_dir)} {shlex.quote(session_out)}")
                source = ssh.read(row["toml"])
                doc = tomlkit.parse(source)
                updates = {"area": 0, "intensity": 0}

                def edit_thresholds(obj):
                    if isinstance(obj, dict):
                        for key, value in obj.items():
                            key_lower = str(key).lower()
                            if key_lower in {"area_ths", "area_thresholds"}:
                                updates["area"] += _update_threshold_pairs(
                                    value,
                                    minimum=area_min,
                                    maximum=area_max,
                                )
                            elif key_lower in {
                                "intensity_ths",
                                "intensity_thresholds",
                            }:
                                # GUI edits the MINIMUM background intensity
                                # threshold. The maximum is preserved.
                                updates["intensity"] += _update_threshold_pairs(
                                    value,
                                    minimum=background_min,
                                    maximum=None,
                                )
                            edit_thresholds(value)
                    elif isinstance(obj, list):
                        for value in obj:
                            edit_thresholds(value)

                edit_thresholds(doc)

                if updates["area"] == 0:
                    raise ValueError(
                        "No blob-area threshold pair was found in the TOML."
                    )
                if updates["intensity"] == 0:
                    raise ValueError(
                        "No intensity threshold pair was found in the TOML."
                    )

                _validate_thresholds(doc)
                copied_toml = input_dir + "/" + Path(row["toml"]).name
                rendered_toml = tomlkit.dumps(doc)
                reparsed = tomlkit.parse(rendered_toml)
                _validate_thresholds(reparsed)
                ssh.write(copied_toml, rendered_toml)
                metadata = {
                    "run_index": run_index,
                    "run_timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "analysis_type": analysis,
                    "video_path": row["video"],
                    "video_filename": Path(row["video"]).name,
                    "toml_source_path": row["toml"],
                    "toml_run_copy_path": copied_toml,
                    "cell_label": row["cell"],
                    "remote_run_dir": run_dir,
                    "session_path": "",
                    "parameters": {
                        "area_min": area_min,
                        "area_max": None if math.isinf(area_max) else area_max,
                        "area_max_is_infinite": math.isinf(area_max),
                        "background_intensity_min": background_min,
                        "run_mode": "postprocess" if self.run_post.isChecked() else "full",
                        **{k: w.value() for k,w in self.param.items()},
                    },
                }
                ssh.write(metadata_path, json.dumps(metadata, indent=2, allow_nan=False))
                env = {
                    "PIPELINE_REPO_ROOT": self.repo_root.text().strip(),
                    "PIPELINE_PROJECT_ROOT": self.project_root.text().strip(),
                    "PIPELINE_RUN_DIR": run_dir,
                    "PIPELINE_TOML": copied_toml,
                    "PIPELINE_VIDEO": row["video"],
                    "PIPELINE_ANALYSIS_TYPE": analysis,
                    "PIPELINE_METADATA_JSON": metadata_path,
                    "PIPELINE_SESSION_OUTPUT_DIR": session_out,
                    "PIPELINE_SESSION": "",
                    "PIPELINE_RUN_MODE": "postprocess" if self.run_post.isChecked() else "full",
                    "PIPELINE_ARCHIVE_SESSION": "0",
                    "PIPELINE_POSTPROCESS_EXTRA_ARGS": self.postprocess_args(analysis),
                }
                exports = " ".join(
                    f"{k}={shlex.quote(str(v))}" for k, v in env.items()
                )
                command = (
                    f"export {exports}; "
                    f"bash {shlex.quote(self.repo_root.text().rstrip('/') + '/scripts/firebird/submit_pipeline_run.sh')}"
                )
                result = ssh.run(command)
                job_ids = {
                    "IDTRACKER_JOB": "",
                    "POSTPROCESS_JOB": "",
                    "COLLECTOR_JOB": "",
                }
                for output_line in result.splitlines():
                    if "=" not in output_line:
                        continue
                    key, value = output_line.split("=", 1)
                    if key in job_ids:
                        job_ids[key] = value.strip()

                self.jobs.append({
                    "run_index": run_index,
                    "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "label": f"{Path(row['video']).name} / {row['cell']}",
                    "idtracker_job": job_ids["IDTRACKER_JOB"],
                    "postprocess_job": job_ids["POSTPROCESS_JOB"],
                    "collector_job": job_ids["COLLECTOR_JOB"],
                    "status": "Postprocess submitted" if self.run_post.isChecked() else "Tracking submitted",
                    "run_dir": run_dir,
                })
                self.populate_jobs_table()
                messages.append(f"Run {run_index:05d}: {result.strip()}")
            except Exception as exc:
                messages.append(f"{row['toml']}: ERROR {exc}")
        self.result.setText("\n".join(messages) if messages else "No rows selected.")



    def populate_jobs_table(self):
        if not hasattr(self, "jobs_table"):
            return
        self.jobs_table.setRowCount(len(self.jobs))
        for row_number, job in enumerate(self.jobs):
            values = [
                f"{int(job['run_index']):05d}",
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
