import math
import sys
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QPushButton, QLineEdit, QLabel, QTabWidget, QTableWidget, QTableWidgetItem,
    QMessageBox, QHeaderView, QComboBox, QCheckBox, QTextEdit,
    QAbstractItemView, QFileDialog
)

from src.config import Config
from src.ssh_backend import SSHBackend
from src.remote_scan import scan
from src.matcher import match_all
from src.run_manager import prepare_and_submit
from src.job_monitor import read_status


BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config/config.json"


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = Config.load(CONFIG_PATH)
        self.backend = SSHBackend(
            self.config.ssh_host,
            self.config.identity_file,
        )
        self.matches = []
        self.jobs = []
        self.setWindowTitle("IDtracker Firebird Controller")
        self.resize(1550, 900)
        self.setFont(QFont("Arial", 11))
        self.build()

    def build(self):
        tabs = QTabWidget()
        tabs.addTab(self.connection_tab(), "1. Connection")
        tabs.addTab(self.files_tab(), "2. Find and Match")
        tabs.addTab(self.submit_tab(), "3. Submit")
        tabs.addTab(self.jobs_tab(), "4. Jobs")
        self.setCentralWidget(tabs)

    def connection_tab(self):
        page = QWidget()
        form = QFormLayout(page)

        self.host = QLineEdit(self.config.ssh_host)
        self.key = QLineEdit(self.config.identity_file)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key)
        choose = QPushButton("Choose Key…")
        choose.clicked.connect(self.choose_key)
        key_row.addWidget(choose)

        self.search_root = QLineEdit(self.config.remote_search_root)
        self.run_root = QLineEdit(self.config.remote_project_root)
        self.id_script = QLineEdit(self.config.idtracker_script)
        self.ba_script = QLineEdit(self.config.ba_script)
        self.fight_script = QLineEdit(self.config.fight_script)

        form.addRow("SSH host or alias", self.host)
        form.addRow("SSH private key", key_row)
        form.addRow("Recursive Firebird search root", self.search_root)
        form.addRow("Remote run root", self.run_root)
        form.addRow("Known-working IDtracker SLURM script", self.id_script)
        form.addRow("Known-working BA post-process script", self.ba_script)
        form.addRow("Known-working fight post-process script", self.fight_script)

        buttons = QHBoxLayout()
        for text, callback in (
            ("Test SSH", self.test_ssh),
            ("Verify Scripts", self.verify_scripts),
            ("Save Settings", self.save_settings),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        form.addRow(buttons)
        return page

    def files_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel(
            "Firebird is searched recursively for MP4, AVI, TOML, and session_* "
            "folders. Ambiguous matches are never selected automatically."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        scan_button = QPushButton("Recursively Scan Firebird")
        scan_button.clicked.connect(self.scan_remote)
        layout.addWidget(scan_button)

        self.summary = QLabel("No scan run.")
        layout.addWidget(self.summary)

        self.table = QTableWidget(0, 13)
        self.table.setHorizontalHeaderLabels([
            "Use", "Status", "Video", "Cell", "TOML", "Session",
            "Animals", "ROIs", "Analysis", "Blob min", "Blob max",
            "Background", "Reason"
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.itemChanged.connect(self.item_changed)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(12, QHeaderView.Stretch)
        layout.addWidget(self.table)
        return page

    def submit_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.run_id = QCheckBox(
            "Submit IDtracker through the configured SLURM script"
        )
        self.run_id.setChecked(True)
        self.run_pp = QCheckBox(
            "Submit assay-specific post-processing after IDtracker succeeds"
        )
        layout.addWidget(self.run_id)
        layout.addWidget(self.run_pp)

        self.command_preview = QTextEdit()
        self.command_preview.setReadOnly(True)
        self.command_preview.setPlaceholderText(
            "Exact remote sbatch commands will appear here."
        )
        layout.addWidget(self.command_preview)

        submit = QPushButton("Create Remote Runs and Submit Selected Rows")
        submit.clicked.connect(self.submit_rows)
        layout.addWidget(submit)
        return page

    def jobs_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.jobs_table = QTableWidget(0, 8)
        self.jobs_table.setHorizontalHeaderLabels([
            "Run", "Date/time", "Video/cell", "IDtracker job",
            "Post-process job", "ID state", "Post state", "Remote run folder"
        ])
        self.jobs_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        layout.addWidget(self.jobs_table)
        refresh = QPushButton("Refresh Job Status")
        refresh.clicked.connect(self.refresh_jobs)
        layout.addWidget(refresh)
        return page

    def choose_key(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose SSH private key",
            str(Path.home() / ".ssh"),
        )
        if path:
            self.key.setText(path)

    def rebuild_backend(self):
        self.backend = SSHBackend(
            self.host.text().strip(),
            self.key.text().strip(),
        )

    def save_settings(self):
        self.config.ssh_host = self.host.text().strip()
        self.config.identity_file = self.key.text().strip()
        self.config.remote_search_root = self.search_root.text().strip()
        self.config.remote_project_root = self.run_root.text().strip()
        self.config.idtracker_script = self.id_script.text().strip()
        self.config.ba_script = self.ba_script.text().strip()
        self.config.fight_script = self.fight_script.text().strip()
        self.config.save(CONFIG_PATH)
        self.rebuild_backend()
        QMessageBox.information(self, "Saved", "Settings saved.")

    def test_ssh(self):
        self.rebuild_backend()
        try:
            QMessageBox.information(
                self,
                "SSH works",
                self.backend.test(),
            )
        except Exception as exc:
            QMessageBox.critical(self, "SSH failed", str(exc))

    def verify_scripts(self):
        self.rebuild_backend()
        lines = []
        for label, path in (
            ("IDtracker", self.id_script.text().strip()),
            ("BA", self.ba_script.text().strip()),
            ("Fight", self.fight_script.text().strip()),
        ):
            if not path:
                lines.append(f"{label}: not configured")
                continue
            result = self.backend.run(f"test -r {path!r}")
            lines.append(
                f"{label}: {'OK' if result.returncode == 0 else 'MISSING'} — {path}"
            )
        QMessageBox.information(
            self,
            "Script verification",
            "\n".join(lines),
        )

    def scan_remote(self):
        self.save_settings()
        try:
            videos, tomls, sessions = scan(
                self.backend,
                self.config.remote_search_root,
            )
            self.matches = match_all(videos, tomls, sessions)
            self.populate_matches()
            matched = sum(m.status == "Matched" for m in self.matches)
            ambiguous = sum(m.status == "Ambiguous" for m in self.matches)
            unmatched = sum(m.status == "Unmatched" for m in self.matches)
            errors = sum(m.status == "TOML error" for m in self.matches)
            self.summary.setText(
                f"Videos: {len(videos)}; TOMLs: {len(tomls)}; "
                f"Sessions: {len(sessions)}; Matched: {matched}; "
                f"Ambiguous: {ambiguous}; Unmatched: {unmatched}; "
                f"TOML errors: {errors}"
            )
        except Exception as exc:
            QMessageBox.critical(self, "Scan failed", str(exc))

    def populate_matches(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.matches))
        for row_number, match in enumerate(self.matches):
            use = QTableWidgetItem()
            use.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            use.setCheckState(
                Qt.Checked if match.selected else Qt.Unchecked
            )
            self.table.setItem(row_number, 0, use)

            values = [
                match.status,
                match.video_filename or "",
                match.cell_label or "",
                match.toml_filename,
                match.session_path or "",
                "" if match.number_of_animals is None else str(match.number_of_animals),
                "" if match.roi_count is None else str(match.roi_count),
            ]
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row_number, column, item)

            combo = QComboBox()
            combo.addItems(["Auto", "Behavioral assay", "Fight"])
            combo.setCurrentText(match.assay_type)
            combo.currentTextChanged.connect(
                lambda value, row=row_number: self.set_assay(row, value)
            )
            self.table.setCellWidget(row_number, 8, combo)

            for column, value in (
                (9, match.area_min),
                (10, match.area_max),
                (11, match.background_difference),
            ):
                if value is None:
                    text = ""
                elif math.isinf(value):
                    text = "inf"
                else:
                    text = str(value)
                self.table.setItem(
                    row_number,
                    column,
                    QTableWidgetItem(text),
                )

            reason = QTableWidgetItem(match.reason)
            reason.setFlags(reason.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row_number, 12, reason)

        self.table.blockSignals(False)

    def item_changed(self, item):
        if not 0 <= item.row() < len(self.matches):
            return
        match = self.matches[item.row()]
        if item.column() == 0:
            match.selected = item.checkState() == Qt.Checked
        elif item.column() in (9, 10, 11):
            try:
                value = float(item.text())
            except ValueError:
                value = None
            if item.column() == 9:
                match.area_min = value
            elif item.column() == 10:
                match.area_max = value
            else:
                match.background_difference = value

    def set_assay(self, row, value):
        self.matches[row].assay_type = value

    def submit_rows(self):
        self.save_settings()
        selected = [row for row in self.matches if row.selected]
        if not selected:
            QMessageBox.warning(
                self,
                "Nothing selected",
                "Select at least one matched row.",
            )
            return

        submitted = 0
        errors = []
        previews = []

        for row in selected:
            try:
                result = prepare_and_submit(
                    self.backend,
                    self.config,
                    row,
                    self.run_id.isChecked(),
                    self.run_pp.isChecked(),
                )
                self.jobs.append({
                    "run": result["run_index"],
                    "time": result["timestamp"],
                    "label": f"{row.video_filename} / {row.cell_label or ''}",
                    "id": result["jobs"]["idtracker"],
                    "pp": result["jobs"]["postprocess"],
                    "id_state": "Submitted" if result["jobs"]["idtracker"] else "Not submitted",
                    "pp_state": "Submitted" if result["jobs"]["postprocess"] else "Not submitted",
                    "dir": result["run_dir"],
                })
                previews.extend(result["commands"].values())
                submitted += 1
            except Exception as exc:
                errors.append(f"{row.toml_filename}: {exc}")

        self.command_preview.setPlainText("\n\n".join(previews))
        self.populate_jobs()

        message = f"Submitted {submitted} run(s)."
        if errors:
            message += "\n\nProblems:\n" + "\n".join(errors)
        QMessageBox.information(
            self,
            "Submission result",
            message,
        )

    def populate_jobs(self):
        self.jobs_table.setRowCount(len(self.jobs))
        for row_number, job in enumerate(self.jobs):
            values = [
                f"{job['run']:05d}",
                job["time"],
                job["label"],
                job["id"],
                job["pp"],
                job["id_state"],
                job["pp_state"],
                job["dir"],
            ]
            for column, value in enumerate(values):
                self.jobs_table.setItem(
                    row_number,
                    column,
                    QTableWidgetItem(str(value)),
                )

    def refresh_jobs(self):
        for job in self.jobs:
            try:
                job["id_state"] = read_status(
                    self.backend,
                    job["id"],
                )
                job["pp_state"] = read_status(
                    self.backend,
                    job["pp"],
                )
            except Exception as exc:
                job["id_state"] = f"Error: {exc}"
        self.populate_jobs()


def main():
    application = QApplication(sys.argv)
    application.setStyle("Fusion")
    window = Window()
    window.show()
    sys.exit(application.exec_())


if __name__ == "__main__":
    main()
