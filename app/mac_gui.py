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
    QHeaderView, QMessageBox, QFileDialog, QComboBox, QCheckBox
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
        self.setWindowTitle("Beetle IDtracker Unified Pipeline")
        self.resize(1500, 850)
        tabs = QTabWidget()
        tabs.addTab(self.connection_page(), "1. Connection")
        tabs.addTab(self.scan_page(), "2. Scan and Match")
        tabs.addTab(self.submit_page(), "3. Submit")
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
                messages.append(f"Run {run_index:05d}: {result.strip()}")
            except Exception as exc:
                messages.append(f"{row['toml']}: ERROR {exc}")
        self.result.setText("\n".join(messages) if messages else "No rows selected.")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = Window()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
