from __future__ import annotations
import json
import math
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QPushButton, QLabel, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QFileDialog, QComboBox, QCheckBox, QTextEdit,
    QAbstractItemView
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


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.rows = []
        self.jobs = []
        self.setWindowTitle("Beetle IDtracker Unified Pipeline")
        self.resize(1500, 850)
        tabs = QTabWidget()
        tabs.addTab(self.connection_page(), "1. Connection")
        tabs.addTab(self.scan_page(), "2. Scan and Match")
        tabs.addTab(self.submit_page(), "3. Submit")
        tabs.addTab(self.diagnostics_page(), "4. Jobs and Diagnostics")
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
        button = QPushButton("Recursively Find Videos and TOMLs")
        button.clicked.connect(self.scan)
        layout.addWidget(button)
        self.summary = QLabel("No scan yet.")
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels([
            "Use", "Video", "Cell", "TOML", "Analysis", "Blob min",
            "Blob max", "Background", "Status", "Reason"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(9, QHeaderView.Stretch)
        layout.addWidget(self.table)
        return page

    def submit_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        text = QLabel(
            "Each selected row creates a run-specific TOML and metadata file, then "
            "submits IDtracker → post-processing → collector with afterok dependencies."
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
        try:
            root = self.search_root.text().strip()
            output = self.ssh().run(
                f"find {shlex.quote(root)} -type f "
                r"\( -iname '*.mp4' -o -iname '*.avi' -o -iname '*.toml' \) -print"
            )
            paths = [x for x in output.splitlines() if x.strip()]
            videos = {Path(p).name.lower(): p for p in paths if Path(p).suffix.lower() in (".mp4", ".avi")}
            tomls = [p for p in paths if Path(p).suffix.lower() == ".toml"]
            rows = []
            for toml_path in tomls:
                name = Path(toml_path).name
                stem = Path(toml_path).stem
                try:
                    text = self.ssh().read(toml_path)
                    doc = tomlkit.parse(text)
                    embedded = None
                    for value in find_values(doc, {
                        "video_path", "video_paths", "video", "videos", "video_file"
                    }):
                        candidates = [value] if isinstance(value, str) else value if isinstance(value, list) else []
                        for candidate in candidates:
                            if isinstance(candidate, str) and candidate.lower().endswith((".mp4", ".avi")):
                                embedded = Path(candidate).name
                                break
                        if embedded:
                            break
                    video = videos.get((embedded or "").lower())
                    cell = stem.rsplit("_", 1)[-1]
                    area = next((x for x in find_values(doc, {"area_ths", "area_thresholds"}) if isinstance(x, list) and len(x) >= 2), [None, None])
                    intensity = next((x for x in find_values(doc, {"intensity_ths", "intensity_thresholds"}) if isinstance(x, list) and len(x) >= 2), [None, None])
                    animals = next(iter(find_values(doc, {"number_of_animals", "n_animals"})), None)
                    analysis = "ba" if animals == 1 else "fight" if animals == 2 else "ba"
                    rows.append({
                        "use": bool(video), "video": video or "", "cell": cell,
                        "toml": toml_path, "analysis": analysis,
                        "area_min": area[0], "area_max": area[1],
                        "background": intensity[1], "status": "Matched" if video else "Unmatched",
                        "reason": "Embedded video filename" if video else "No exact embedded video match",
                    })
                except Exception as exc:
                    rows.append({
                        "use": False, "video": "", "cell": "", "toml": toml_path,
                        "analysis": "ba", "area_min": None, "area_max": None,
                        "background": None, "status": "TOML error", "reason": str(exc)
                    })
            self.rows = rows
            self.populate()
            self.summary.setText(
                f"Found {len(videos)} videos and {len(tomls)} TOMLs; "
                f"{sum(r['status']=='Matched' for r in rows)} exact matches."
            )
        except Exception as exc:
            QMessageBox.critical(self, "Scan failed", str(exc))

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
            for c, key in ((5, "area_min"), (6, "area_max"), (7, "background"), (8, "status"), (9, "reason")):
                self.table.setItem(i, c, QTableWidgetItem(str(row[key])))

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
                background = float(self.table.item(i, 7).text())
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
                def edit(obj):
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if str(k).lower() in {"area_ths", "area_thresholds"} and isinstance(v, list) and len(v)>=2:
                                v[0], v[1] = area_min, area_max
                            elif str(k).lower() in {"intensity_ths", "intensity_thresholds"} and isinstance(v, list) and len(v)>=2:
                                v[1] = background
                            edit(v)
                    elif isinstance(obj, list):
                        for v in obj: edit(v)
                edit(doc)
                copied_toml = input_dir + "/" + Path(row["toml"]).name
                ssh.write(copied_toml, tomlkit.dumps(doc))
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
                        "background_difference": background,
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
                }
                exports = ",".join(f"{k}={str(v).replace(',', '_')}" for k, v in env.items())
                command = (
                    f"export {exports.replace(',', ' ')}; "
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
                    "status": "Submitted",
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


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = Window()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
