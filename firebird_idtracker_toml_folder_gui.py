#!/usr/bin/env python3
"""
Firebird IDtracker.ai TOML-folder launcher.

Simplified student workflow
---------------------------
1. Student manually creates TOML files in idtracker.ai's Segmentation app.
2. Student opens this GUI.
3. Student selects the original source video and the folder containing all TOMLs
   made from that video.
4. GUI imports and validates the TOMLs into a grid.
5. Student edits arena/cell labels if needed.
6. GUI submits SLURM jobs for tracking and post-processing.

This version deliberately does not launch the Segmentation app.  That avoids
Qt/conda fragility on noVNC and keeps the human judgment step separate.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Dict, List, Optional

# Set these before importing Qt.  On Firebird/noVNC, /run/user/<uid> may not be
# writable, and SESSION_MANAGER can point at an authentication mechanism Qt
# cannot use.  These warnings are usually harmless, but setting clean values
# prevents noisy startup messages and occasional Qt confusion.
_runtime = Path(f"/tmp/runtime-{os.environ.get('USER', 'user')}")
try:
    _runtime.mkdir(parents=True, exist_ok=True)
    _runtime.chmod(0o700)
    os.environ.setdefault("XDG_RUNTIME_DIR", str(_runtime))
except Exception:
    pass
os.environ.pop("SESSION_MANAGER", None)

QT_BINDING = ""
try:  # PyQt6
    from PyQt6.QtCore import QTimer, Qt
    from PyQt6.QtWidgets import (
        QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGridLayout,
        QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
        QMessageBox, QPushButton, QSpinBox, QDoubleSpinBox, QTabWidget, QTableWidget,
        QTableWidgetItem, QTextEdit, QToolButton, QVBoxLayout, QWidget
    )
    QT_BINDING = "PyQt6"
except Exception:
    try:  # PySide6
        from PySide6.QtCore import QTimer, Qt
        from PySide6.QtWidgets import (
            QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGridLayout,
            QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
            QMessageBox, QPushButton, QSpinBox, QDoubleSpinBox, QTabWidget, QTableWidget,
            QTableWidgetItem, QTextEdit, QToolButton, QVBoxLayout, QWidget
        )
        QT_BINDING = "PySide6"
    except Exception:  # PyQt5 fallback
        from PyQt5.QtCore import QTimer, Qt  # type: ignore
        from PyQt5.QtWidgets import (  # type: ignore
            QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGridLayout,
            QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
            QMessageBox, QPushButton, QSpinBox, QDoubleSpinBox, QTabWidget, QTableWidget,
            QTableWidgetItem, QTextEdit, QToolButton, QVBoxLayout, QWidget
        )
        QT_BINDING = "PyQt5"

PACKAGE_DIR = Path(__file__).resolve().parent
MANAGER = PACKAGE_DIR / "tools" / "toml_folder_manager.py"
SLURM_SCRIPT = PACKAGE_DIR / "slurm" / "firebird_idtracker_toml_folder.slurm"
STATUS_TOOL = PACKAGE_DIR / "tools" / "pipeline_status.py"

GRID_COLUMNS = [
    "validation_status", "run_this_toml", "toml_name", "cell_label", "output_stem", "pipeline", "expected_animals",
    "toml_expected_animals_found", "toml_tracking_interval_start_frame", "toml_tracking_interval_end_frame",
    "toml_analysis_start_frame", "toml_video_matches_registered_video", "toml_roi_or_polygon_found",
    "validation_message", "cell_notes", "toml_path", "toml_video_path",
]
EDITABLE_GRID_COLUMNS = {"cell_label", "cell_notes"}

PARAM_HELP = {
    "Analysis window length, frames": """
    <b>Analysis window length, frames</b><br>
    Number of frames analyzed after the TOML tracking-interval start frame. For example, if the TOML says
    <code>tracking_intervals = [[65, 8419]]</code> and this value is 7500, post-processing analyzes frames 65 through 7564.
    The default is 7500 frames.
    """,
    "FPS": """
    <b>FPS</b><br>
    Frames per second used to convert frame counts into seconds. Set this to the recording frame rate of the source video.
    """,
    "Movement threshold, pixels": """
    <b>Movement threshold, pixels</b><br>
    Minimum frame-to-frame displacement used to classify an animal as moving. Larger values are more conservative.
    """,
    "Artifact max step, pixels": """
    <b>Artifact max step, pixels</b><br>
    Frame-to-frame jumps larger than this are treated as likely tracking artifacts and are filtered/interpolated rather than interpreted as real movement.
    """,
    "Sustained movement onset, frames": """
    <b>Sustained movement onset, frames</b><br>
    Number of consecutive frames above the movement threshold required before the script calls the animal's movement onset.
    """,
    "ROI/wall buffer, pixels": """
    <b>ROI/wall buffer, pixels</b><br>
    Distance from the arena boundary used to flag wall/edge-associated positions. This is useful for separating center use from wall following.
    """,
    "Turtling window, frames": """
    <b>Turtling window, frames</b><br>
    Sliding window length used to identify turtle-like behavior: low net displacement despite path length and turning.
    """,
    "Minimum turtling duration, frames": """
    <b>Minimum turtling duration, frames</b><br>
    Minimum duration for a turtle-like segment to be retained as an event.
    """,
    "Contact distance, pixels": """
    <b>Contact distance, pixels</b><br>
    Fight-only setting. If the two beetles are within this distance, the frame is marked as contact-like.
    """,
    "Minimum contact duration, seconds": """
    <b>Minimum contact duration, seconds</b><br>
    Fight-only setting. Contact-like frames must last at least this long to be summarized as a contact event.
    """,
    "Possible fight distance, pixels": """
    <b>Possible fight distance, pixels</b><br>
    Fight-only setting. A stricter distance threshold for possible fight-like interaction frames.
    """,
    "Minimum fight-like duration, frames": """
    <b>Minimum fight-like duration, frames</b><br>
    Fight-only setting. Minimum contiguous number of fight-like frames required before an event is written.
    """,
    "Conda environment": """
    <b>Conda environment</b><br>
    Environment activated inside SLURM before running <code>idtrackerai</code> and post-processing. This should usually be <code>idtrackerai</code>.
    """,
    "Conda init file": """
    <b>Conda init file</b><br>
    Path to conda's shell initialization script. The SLURM job sources this file, then runs <code>conda activate idtrackerai</code>.
    """,
    "Maximum simultaneous array tasks": """
    <b>Maximum simultaneous array tasks</b><br>
    Maximum number of TOMLs submitted to run at the same time in the SLURM array. This prevents one student run from flooding the GPU queue.
    """,
    "IDtracker GPU request": """
    <b>IDtracker GPU request</b><br>
    SLURM GPU resource request for the IDtracker.ai array jobs. The default accepts any GPU using <code>--partition=gpu --gres=gpu:1</code>, which usually gives the most simultaneous test jobs. Use L40S only when you prefer fewer, faster GPU slots.
    """,
    "Custom GPU sbatch flags": """
    <b>Custom GPU sbatch flags</b><br>
    Used only when the GPU request menu is set to Custom. Example: <code>--partition=gpu --gres=gpu:l40s:1</code> or <code>--partition=gpu --constraint=l40s --gres=gpu:1</code>.
    """,
    "Post-processing partition": """
    <b>Post-processing partition</b><br>
    Optional CPU partition for the collector and post-processing jobs. Leave blank to use the cluster default. Post-processing does not request a GPU.
    """,
}


def qt_checked_yes():
    return QMessageBox.StandardButton.Yes if QT_BINDING != "PyQt5" else QMessageBox.Yes


def item_editable_off(item: QTableWidgetItem) -> None:
    try:
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    except AttributeError:
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)  # type: ignore[attr-defined]


def header_interactive():
    return QHeaderView.ResizeMode.Interactive if QT_BINDING != "PyQt5" else QHeaderView.Interactive


def run_cmd(cmd: List[str], cwd: Optional[Path] = None, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, capture_output=True, check=check)


def quote_export(value: str) -> str:
    # sbatch --export uses comma-separated KEY=value pairs.  Replace commas to
    # avoid breaking the export list.  Paths used here should not contain commas.
    return str(value).replace(",", "_")


def parse_job_id(stdout: str) -> str:
    for tok in stdout.split():
        if tok.isdigit():
            return tok
    return ""

def sbatch_resource_args(mode: str, custom_text: str = "") -> List[str]:
    """Return sbatch resource arguments selected in the GUI.

    The SLURM script itself intentionally does not hard-code a GPU type.
    IDtracker.ai tasks get GPU options here; the dependent post-processing job
    should not request a GPU because it only reads trajectory files and writes
    CSVs/plots.
    """
    mode = (mode or "").strip()
    if mode == "any_gpu":
        return ["--partition=gpu", "--gres=gpu:1"]
    if mode == "l40s_gres":
        return ["--partition=gpu", "--gres=gpu:l40s:1"]
    if mode == "l40s_constraint":
        return ["--partition=gpu", "--constraint=l40s", "--gres=gpu:1"]
    if mode == "custom":
        return shlex.split(custom_text or "")
    return ["--partition=gpu", "--gres=gpu:1"]


def postprocess_sbatch_args(partition: str) -> List[str]:
    """Return sbatch arguments for CPU post-processing.

    Blank means use the cluster's default partition. This avoids holding a GPU
    for post-processing, which should be CPU/I/O work.
    """
    partition = (partition or "").strip()
    return ["--partition", partition] if partition else []


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Firebird IDtracker.ai TOML Folder Launcher")
        self.resize(1050, 720)
        self.last_rows: List[Dict[str, str]] = []
        self.active_job_ids: List[str] = []
        self.active_toml_folder: Optional[Path] = None
        self.selected_toml_files: List[Path] = []
        self.notified_completion = False
        self.lockable_widgets: List[QWidget] = []

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self._build_import_tab()
        self._build_run_tab()
        self._build_status_tab()

        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.refresh_squeue)
        self.status_timer.start(10000)

    def _help_button(self, title: str) -> QToolButton:
        html = PARAM_HELP.get(title, f"<b>{title}</b><br>No additional description available.")
        btn = QToolButton()
        btn.setText("?")
        btn.setToolTip(html)
        btn.clicked.connect(lambda _=False, t=title, h=html: QMessageBox.information(self, t, h))
        return btn

    def _label_help_widget(self, title: str) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        lab = QLabel(title)
        lab.setToolTip(PARAM_HELP.get(title, ""))
        h.addWidget(lab)
        h.addWidget(self._help_button(title))
        h.addStretch(1)
        return w

    def _add_grid_param(self, grid: QGridLayout, row: int, col: int, title: str, widget: QWidget) -> None:
        grid.addWidget(self._label_help_widget(title), row, col)
        grid.addWidget(widget, row, col + 1)
        self.lockable_widgets.append(widget)

    def _build_import_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        info = QLabel(
            "Select one source video, then either choose a whole TOML folder or choose one or several TOML files. "
            "Each TOML is treated as one arena/cell. The arena/cell label is inferred from the end of the TOML filename, such as A1 or B3, and can be edited in the grid. "
            "In the table, use Shift-click or Ctrl/Command-click to select multiple rows for exclusion or restoration."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form_box = QGroupBox("Video + TOML folder")
        form = QFormLayout(form_box)

        self.video_edit = QLineEdit()
        self.video_edit.setPlaceholderText("Select the original .avi or .mp4 video used when making these TOMLs")
        video_row = QHBoxLayout()
        video_row.addWidget(self.video_edit)
        self.browse_video_btn = QPushButton("Browse video")
        self.browse_video_btn.clicked.connect(self.browse_video)
        video_row.addWidget(self.browse_video_btn)
        form.addRow("Original video", video_row)

        self.toml_folder_edit = QLineEdit()
        self.toml_folder_edit.setPlaceholderText("Select the folder containing all TOML files for this video")
        toml_row = QHBoxLayout()
        toml_row.addWidget(self.toml_folder_edit)
        self.browse_tomls_btn = QPushButton("Browse TOML folder")
        self.browse_tomls_btn.clicked.connect(self.browse_toml_folder)
        toml_row.addWidget(self.browse_tomls_btn)
        self.browse_toml_files_btn = QPushButton("Browse TOML file(s)")
        self.browse_toml_files_btn.clicked.connect(self.browse_toml_files)
        toml_row.addWidget(self.browse_toml_files_btn)
        form.addRow("TOML source", toml_row)

        self.toml_selection_label = QLabel("Folder mode: all TOMLs in the selected folder will be imported.")
        self.toml_selection_label.setWordWrap(True)
        form.addRow("Selected TOMLs", self.toml_selection_label)

        self.pipeline_combo = QComboBox()
        self.pipeline_combo.addItem("Fight / combat: two beetles", "fight")
        self.pipeline_combo.addItem("BA / behavioral assay: one beetle", "ba")
        form.addRow("Pipeline for this video", self.pipeline_combo)

        self.metadata_tag_edit = QLineEdit()
        self.metadata_tag_edit.setPlaceholderText("Example: 2026_FFB_Round1_ACT1_Camera1")
        form.addRow("Metadata tag", self.metadata_tag_edit)

        self.recursive_check = QCheckBox("Search subfolders too")
        self.recursive_check.setChecked(False)
        form.addRow("TOML search", self.recursive_check)
        layout.addWidget(form_box)

        for w in [self.video_edit, self.browse_video_btn, self.toml_folder_edit, self.browse_tomls_btn, self.browse_toml_files_btn,
                  self.pipeline_combo, self.metadata_tag_edit, self.recursive_check]:
            self.lockable_widgets.append(w)

        btns = QHBoxLayout()
        self.import_btn = QPushButton("Import / validate TOML folder")
        self.import_btn.clicked.connect(self.import_tomls)
        btns.addWidget(self.import_btn)
        self.lockable_widgets.append(self.import_btn)

        self.save_labels_btn = QPushButton("Save edited labels")
        self.save_labels_btn.clicked.connect(self.save_grid_edits)
        btns.addWidget(self.save_labels_btn)
        self.lockable_widgets.append(self.save_labels_btn)

        self.remove_btn = QPushButton("Remove selected TOML from run")
        self.remove_btn.clicked.connect(lambda: self.set_selected_toml_run_status(False))
        btns.addWidget(self.remove_btn)
        self.lockable_widgets.append(self.remove_btn)

        self.restore_btn = QPushButton("Restore selected TOML to run")
        self.restore_btn.clicked.connect(lambda: self.set_selected_toml_run_status(True))
        btns.addWidget(self.restore_btn)
        self.lockable_widgets.append(self.restore_btn)

        self.open_folder_btn = QPushButton("Open TOML folder")
        self.open_folder_btn.clicked.connect(self.open_toml_folder)
        btns.addWidget(self.open_folder_btn)
        layout.addLayout(btns)
        self.lockable_widgets.append(self.open_folder_btn)

        self.table = QTableWidget()
        self.table.setColumnCount(len(GRID_COLUMNS))
        self.table.setHorizontalHeaderLabels(GRID_COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(header_interactive())
        self.table.setAlternatingRowColors(True)
        try:
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        except AttributeError:
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.table)
        self.lockable_widgets.append(self.table)

        self.import_summary = QLabel("No TOMLs imported yet.")
        self.import_summary.setWordWrap(True)
        layout.addWidget(self.import_summary)

        self.tabs.addTab(tab, "1. Import TOMLs")

    def _build_run_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        info = QLabel(
            "Run uses the validated manifest from the TOML folder. The analysis start frame is read from each TOML's tracking interval. "
            "IDtracker.ai runs first as a GPU array. Post-processing starts automatically after the array succeeds."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        params_box = QGroupBox("Shared analysis parameters")
        grid = QGridLayout(params_box)

        self.window_frames = QSpinBox(); self.window_frames.setRange(1, 100000000); self.window_frames.setValue(7500)
        self.fps = QDoubleSpinBox(); self.fps.setRange(0.001, 1000); self.fps.setDecimals(3); self.fps.setValue(30.0)
        self.move_threshold = QDoubleSpinBox(); self.move_threshold.setRange(0, 10000); self.move_threshold.setValue(30.0)
        self.max_step = QDoubleSpinBox(); self.max_step.setRange(0, 10000); self.max_step.setValue(50.0)
        self.movement_onset = QSpinBox(); self.movement_onset.setRange(1, 1000000); self.movement_onset.setValue(30)
        self.roi_wall_buffer = QDoubleSpinBox(); self.roi_wall_buffer.setRange(0, 10000); self.roi_wall_buffer.setValue(30.0)
        self.turtling_window = QSpinBox(); self.turtling_window.setRange(1, 1000000); self.turtling_window.setValue(300)
        self.turtling_min = QSpinBox(); self.turtling_min.setRange(1, 1000000); self.turtling_min.setValue(300)

        row = 0
        self._add_grid_param(grid, row, 0, "Analysis window length, frames", self.window_frames)
        self._add_grid_param(grid, row, 2, "FPS", self.fps)
        row += 1
        self._add_grid_param(grid, row, 0, "Movement threshold, pixels", self.move_threshold)
        self._add_grid_param(grid, row, 2, "Artifact max step, pixels", self.max_step)
        row += 1
        self._add_grid_param(grid, row, 0, "Sustained movement onset, frames", self.movement_onset)
        self._add_grid_param(grid, row, 2, "ROI/wall buffer, pixels", self.roi_wall_buffer)
        row += 1
        self._add_grid_param(grid, row, 0, "Turtling window, frames", self.turtling_window)
        self._add_grid_param(grid, row, 2, "Minimum turtling duration, frames", self.turtling_min)
        layout.addWidget(params_box)

        fight_box = QGroupBox("Fight-only parameters")
        fgrid = QGridLayout(fight_box)
        self.contact_px = QDoubleSpinBox(); self.contact_px.setRange(0, 10000); self.contact_px.setValue(60.0)
        self.min_contact_s = QDoubleSpinBox(); self.min_contact_s.setRange(0, 10000); self.min_contact_s.setDecimals(3); self.min_contact_s.setValue(0.2)
        self.fight_px = QDoubleSpinBox(); self.fight_px.setRange(0, 10000); self.fight_px.setValue(35.0)
        self.min_fight_frames = QSpinBox(); self.min_fight_frames.setRange(1, 1000000); self.min_fight_frames.setValue(6)
        self._add_grid_param(fgrid, 0, 0, "Contact distance, pixels", self.contact_px)
        self._add_grid_param(fgrid, 0, 2, "Minimum contact duration, seconds", self.min_contact_s)
        self._add_grid_param(fgrid, 1, 0, "Possible fight distance, pixels", self.fight_px)
        self._add_grid_param(fgrid, 1, 2, "Minimum fight-like duration, frames", self.min_fight_frames)
        layout.addWidget(fight_box)

        slurm_box = QGroupBox("SLURM / environment")
        sform = QFormLayout(slurm_box)
        self.conda_env = QLineEdit("idtrackerai")
        self.conda_init = QLineEdit("/home/vformic1-swat/miniconda3/etc/profile.d/conda.sh")
        self.array_limit = QSpinBox(); self.array_limit.setRange(1, 200); self.array_limit.setValue(20)

        self.gpu_mode = QComboBox()
        self.gpu_mode.addItem("Any GPU: --partition=gpu --gres=gpu:1", "any_gpu")
        self.gpu_mode.addItem("L40S only: --partition=gpu --gres=gpu:l40s:1", "l40s_gres")
        self.gpu_mode.addItem("L40S by constraint: --partition=gpu --constraint=l40s --gres=gpu:1", "l40s_constraint")
        self.gpu_mode.addItem("Custom sbatch resource flags", "custom")
        self.gpu_mode.setCurrentIndex(0)
        self.custom_gpu_flags = QLineEdit("--partition=gpu --gres=gpu:1")
        self.post_partition = QLineEdit("")
        self.post_partition.setPlaceholderText("Leave blank for Firebird default non-GPU partition; fill only if your cluster has a valid CPU partition name")

        self.strict_check = QCheckBox("Strict mode: do not run warning rows")
        self.strict_check.setChecked(False)

        for title, widget in [("Conda environment", self.conda_env), ("Conda init file", self.conda_init),
                              ("Maximum simultaneous array tasks", self.array_limit),
                              ("IDtracker GPU request", self.gpu_mode),
                              ("Custom GPU sbatch flags", self.custom_gpu_flags),
                              ("Post-processing partition", self.post_partition)]:
            roww = QWidget(); hh = QHBoxLayout(roww); hh.setContentsMargins(0,0,0,0); hh.addWidget(widget); hh.addWidget(self._help_button(title))
            sform.addRow(title, roww)
            self.lockable_widgets.append(widget)
        sform.addRow("Validation", self.strict_check)
        self.lockable_widgets.append(self.strict_check)
        layout.addWidget(slurm_box)

        run_row = QHBoxLayout()
        self.run_btn = QPushButton("Run full pipeline (IDtracker + post-processing)")
        self.run_btn.setMinimumHeight(48)
        self.run_btn.setToolTip("Runs IDtracker.ai on the included TOMLs, collects the resulting sessions, and then runs post-processing.")
        self.run_btn.clicked.connect(self.submit_slurm)
        run_row.addWidget(self.run_btn)
        self.lockable_widgets.append(self.run_btn)

        self.postprocess_only_btn = QPushButton("Run post-processing only (CPU)")
        self.postprocess_only_btn.setMinimumHeight(48)
        self.postprocess_only_btn.setToolTip(
            "Skips IDtracker.ai and reruns only the Python post-processing analysis on existing "
            "idtracker_sessions. The job requests no GPU."
        )
        self.postprocess_only_btn.clicked.connect(self.submit_postprocessing_only)
        run_row.addWidget(self.postprocess_only_btn)
        self.lockable_widgets.append(self.postprocess_only_btn)
        self.running_label = QLabel("Not running.")
        run_row.addWidget(self.running_label)
        layout.addLayout(run_row)

        self.run_log = QTextEdit()
        self.run_log.setReadOnly(True)
        self.run_log.setMinimumHeight(180)
        layout.addWidget(self.run_log)

        self.tabs.addTab(tab, "2. Run")

    def _build_status_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        top = QHBoxLayout()
        self.status_btn = QPushButton("Refresh status")
        self.status_btn.clicked.connect(self.refresh_squeue)
        top.addWidget(self.status_btn)
        self.pipeline_summary_btn = QPushButton("Show pipeline summary")
        self.pipeline_summary_btn.clicked.connect(self.show_pipeline_summary)
        self.pipeline_summary_btn.setToolTip("Reads project_metadata/session_collection_report.csv and postprocessing status files, then summarizes which stages worked and which failed.")
        top.addWidget(self.pipeline_summary_btn)
        self.clear_run_lock_btn = QPushButton("Unlock settings manually")
        self.clear_run_lock_btn.clicked.connect(lambda: self.set_running_state(False))
        self.clear_run_lock_btn.setToolTip("Use this only if the GUI thinks jobs are running after you have cancelled or cleared them from SLURM.")
        top.addWidget(self.clear_run_lock_btn)
        top.addStretch(1)
        layout.addLayout(top)
        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        layout.addWidget(self.status_text)
        self.tabs.addTab(tab, "3. Status")

    def log(self, text: str) -> None:
        if not text:
            return
        self.run_log.append(text)
        self.status_text.append(text)

    def current_toml_folder(self) -> Optional[Path]:
        p = self.toml_folder_edit.text().strip()
        return Path(p).expanduser().resolve() if p else None

    def grid_path(self) -> Optional[Path]:
        folder = self.current_toml_folder()
        return folder / "project_metadata" / "toml_import_grid.csv" if folder else None

    def manifest_path(self) -> Optional[Path]:
        folder = self.current_toml_folder()
        return folder / "project_metadata" / "toml_video_manifest.csv" if folder else None

    def run_state_path(self) -> Optional[Path]:
        folder = self.current_toml_folder() or self.active_toml_folder
        return folder / "project_metadata" / "slurm_run_state.json" if folder else None

    def save_run_state(self, id_job_id: str, post_job_id: str, n_tomls: int, collect_job_id: str = "") -> None:
        path = self.run_state_path()
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "idtracker_array_job_id": id_job_id,
            "session_collection_job_id": collect_job_id,
            "postprocess_job_id": post_job_id,
            "n_tomls": n_tomls,
            "status": "submitted",
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def set_running_state(self, running: bool) -> None:
        for w in self.lockable_widgets:
            try:
                w.setEnabled(not running)
            except Exception:
                pass
        self.running_label.setText("SLURM jobs running. Settings are locked." if running else "Not running.")
        if not running:
            self.active_job_ids = []
            self.notified_completion = False

    def browse_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select original video", "", "Video files (*.avi *.mp4 *.mov *.mkv *.m4v);;All files (*)")
        if path:
            self.video_edit.setText(path)
            if not self.metadata_tag_edit.text().strip():
                self.metadata_tag_edit.setText(Path(path).stem)

    def browse_toml_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select TOML folder")
        if path:
            self.selected_toml_files = []
            self.toml_folder_edit.setText(path)
            self.toml_selection_label.setText("Folder mode: all TOMLs in the selected folder will be imported.")
            if not self.metadata_tag_edit.text().strip():
                self.metadata_tag_edit.setText(Path(path).name)

    def browse_toml_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select one or more TOML files", "", "TOML files (*.toml);;All files (*)"
        )
        if not paths:
            return
        selected = [Path(x).expanduser().resolve() for x in paths]
        parents = {p.parent for p in selected}
        if len(parents) != 1:
            QMessageBox.warning(
                self, "TOMLs must share a folder",
                "For a single run, the selected TOML files must be in the same folder. "
                "Move/copy them into one folder or select each folder as a separate run."
            )
            return
        self.selected_toml_files = selected
        folder = selected[0].parent
        self.toml_folder_edit.setText(str(folder))
        names = ", ".join(p.name for p in selected[:6])
        if len(selected) > 6:
            names += f", plus {len(selected) - 6} more"
        self.toml_selection_label.setText(f"File mode: {len(selected)} selected TOML file(s): {names}")
        if not self.metadata_tag_edit.text().strip():
            self.metadata_tag_edit.setText(folder.name)

    def _has_trajectory(self, folder: Path) -> bool:
        if not folder.exists():
            return False
        names = {"trajectories.npy", "trajectories_wo_gaps.npy", "trajectories_without_gaps.npy", "trajectories.h5", "trajectories.csv"}
        try:
            return any(p.is_file() and p.name in names for p in folder.rglob("*"))
        except Exception:
            return False

    def _scan_existing_outputs(self, rows: Optional[List[Dict[str, str]]] = None) -> Dict[str, object]:
        rows = rows if rows is not None else self.last_rows
        out = {"expected": len(rows), "sessions": 0, "postprocess": 0, "complete_marker": "", "details": []}
        if not rows:
            return out
        folder = self.current_toml_folder()
        if not folder:
            return out
        pipeline = rows[0].get("pipeline", "")
        post_root = folder / "postprocessing" / f"{pipeline}_postprocessing"
        markers = list(post_root.glob("_POSTPROCESS_COMPLETE_ALL_CELLS.txt"))
        if markers:
            out["complete_marker"] = str(markers[-1])
        for row in rows:
            video_stem = row.get("original_video_stem", "")
            output_stem = row.get("output_stem", "")
            sess = folder / "idtracker_sessions" / video_stem / output_stem
            pp = post_root / "session_outputs" / output_stem
            sess_ok = self._has_trajectory(sess)
            pp_ok = pp.exists() and any(p.is_file() for p in pp.glob("*.csv"))
            out["sessions"] = int(out["sessions"]) + (1 if sess_ok else 0)
            out["postprocess"] = int(out["postprocess"]) + (1 if pp_ok else 0)
            out["details"].append({"cell_label": row.get("cell_label", ""), "session_ok": sess_ok, "postprocess_ok": pp_ok})
        return out

    def _ask_existing_output_action(self, scan: Dict[str, object]) -> None:
        expected = int(scan.get("expected") or 0)
        sessions = int(scan.get("sessions") or 0)
        postprocess = int(scan.get("postprocess") or 0)
        complete_marker = str(scan.get("complete_marker") or "")
        if expected == 0 or (sessions == 0 and postprocess == 0 and not complete_marker):
            self.existing_output_action = "full_rerun"
            return
        msg = QMessageBox(self)
        msg.setWindowTitle("Existing outputs detected")
        text = (
            f"This TOML folder already has managed outputs for this video.\n\n"
            f"Collected sessions with trajectories: {sessions}/{expected}\n"
            f"Per-cell post-processing folders: {postprocess}/{expected}\n"
            f"All-cells completion marker: {'YES' if complete_marker else 'NO'}\n\n"
            "Choose what Run should do next."
        )
        msg.setText(text)
        roles = getattr(QMessageBox, "ButtonRole", QMessageBox)
        rerun_all = msg.addButton("Rerun IDtracker and overwrite managed outputs", roles.AcceptRole)
        rerun_post = msg.addButton("Rerun Python post-processing only", roles.ActionRole)
        do_nothing = msg.addButton("Do nothing for now", roles.RejectRole)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked == rerun_post:
            self.existing_output_action = "postprocess_only"
            self.import_summary.setText(self.import_summary.text() + " Existing outputs detected. Run action: rerun Python post-processing only.")
        elif clicked == do_nothing:
            self.existing_output_action = "do_nothing"
            self.import_summary.setText(self.import_summary.text() + " Existing outputs detected. Run action: do nothing.")
        else:
            self.existing_output_action = "full_rerun"
            self.import_summary.setText(self.import_summary.text() + " Existing outputs detected. Run action: rerun IDtracker and overwrite managed outputs.")

    def _cleanup_managed_outputs_for_manifest(self, manifest: Path) -> None:
        if not manifest.exists():
            return
        with manifest.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            return
        folder = self.current_toml_folder()
        if not folder:
            return
        pipeline = rows[0].get("pipeline", "")
        post_root = folder / "postprocessing" / f"{pipeline}_postprocessing"
        for row in rows:
            sess = folder / "idtracker_sessions" / row.get("original_video_stem", "") / row.get("output_stem", "")
            pp = post_root / "session_outputs" / row.get("output_stem", "")
            for path in [sess, pp]:
                if path.exists() and path.is_dir():
                    shutil.rmtree(path)
        # Remove combined status/summary products so rerun results are unambiguous.
        for pattern in ["*_summary_all.csv", "postprocessing_manifest.csv", "postprocessing_status_by_cell.csv", "_POSTPROCESS_*.txt"]:
            for p in post_root.glob(pattern):
                if p.is_file():
                    p.unlink()

    def load_grid(self) -> None:
        grid = self.grid_path()
        rows: List[Dict[str, str]] = []
        if grid and grid.exists():
            with grid.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        self.last_rows = rows
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, col in enumerate(GRID_COLUMNS):
                item = QTableWidgetItem(str(row.get(col, "")))
                status = row.get("validation_status", "")
                if col == "validation_status" and status == "error":
                    item.setText("ERROR")
                elif col == "validation_status" and status == "warning":
                    item.setText("WARNING")
                elif col == "validation_status" and status == "ready":
                    item.setText("READY")
                elif col == "validation_status" and status == "excluded":
                    item.setText("EXCLUDED")
                if col not in EDITABLE_GRID_COLUMNS:
                    item_editable_off(item)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        counts: Dict[str, int] = {}
        for row in rows:
            counts[row.get("validation_status", "unknown")] = counts.get(row.get("validation_status", "unknown"), 0) + 1
        self.import_summary.setText(f"Imported {len(rows)} TOML file(s). Counts: {counts}. Grid file: {grid}")

    def save_grid_edits(self) -> None:
        grid = self.grid_path()
        if not grid or not grid.exists():
            QMessageBox.warning(self, "No grid", "Import TOMLs before editing labels.")
            return
        if not self.last_rows:
            return
        for r, row in enumerate(self.last_rows):
            for col in EDITABLE_GRID_COLUMNS:
                if col in GRID_COLUMNS:
                    c = GRID_COLUMNS.index(col)
                    item = self.table.item(r, c)
                    row[col] = item.text().strip() if item else ""
        with grid.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or list(self.last_rows[0].keys())
        with grid.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in self.last_rows:
                writer.writerow({k: row.get(k, "") for k in fieldnames})
        self.log("Saved edited arena/cell labels. Refreshing validation and output stems.")
        self.refresh_validation(show_message=False)

    def clear_current_import_selection(self, remove_metadata_files: bool = False) -> None:
        """Clear GUI selection after a serious import mismatch.

        This never deletes TOML files or videos.  If remove_metadata_files is True,
        only the package-created metadata CSV/JSON files for the selected TOML
        folder are removed so students cannot accidentally run a stale manifest.
        """
        folder = self.current_toml_folder()
        if remove_metadata_files and folder:
            for rel in [
                "project_metadata/toml_import_grid.csv",
                "project_metadata/toml_video_manifest.csv",
                "project_metadata/run_config.json",
            ]:
                p = folder / rel
                try:
                    if p.exists() and p.is_file():
                        p.unlink()
                except Exception as exc:
                    self.log(f"Could not remove package metadata file {p}: {exc}")
        self.video_edit.clear()
        self.toml_folder_edit.clear()
        self.selected_toml_files = []
        self.toml_selection_label.setText("Folder mode: all TOMLs in the selected folder will be imported.")
        self.table.setRowCount(0)
        self.last_rows = []
        self.import_summary.setText("Selection cleared. Choose the correct video and TOML folder, then import again.")
        self.active_toml_folder = None

    def mismatch_rows_from_grid(self) -> List[Dict[str, str]]:
        return [r for r in self.last_rows if r.get("toml_video_matches_registered_video") == "NO"]

    def handle_mismatch_after_import(self) -> bool:
        """Return True if import is safe to continue, False if a hard mismatch was detected."""
        bad = self.mismatch_rows_from_grid()
        if not bad:
            return True
        lines = [
            f"The selected video does not match {len(bad)} TOML file(s).",
            "",
            "This usually means the student selected the wrong source video or the wrong TOML folder.",
            "The package will not submit this run until the mismatch is fixed.",
            "",
        ]
        for row in bad[:12]:
            lines.append(f"TOML: {row.get('toml_name','')}")
            lines.append(f"  TOML video: {row.get('toml_video_path','')}")
            lines.append(f"  Selected video: {row.get('original_video_path','')}")
        if len(bad) > 12:
            lines.append(f"... plus {len(bad) - 12} more mismatch row(s).")

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Critical if QT_BINDING != "PyQt5" else QMessageBox.Critical)
        msg.setWindowTitle("Video/TOML mismatch")
        msg.setText("Video/TOML mismatch detected")
        msg.setDetailedText("\n".join(lines))
        msg.setInformativeText(
            "Do not run IDtracker or post-processing from this import. "
            "Clear the selection and choose the matching source video and TOML folder."
        )
        roles = getattr(QMessageBox, "ButtonRole", QMessageBox)
        clear_btn = msg.addButton("Clear selection and start over", roles.AcceptRole)
        keep_btn = msg.addButton("Keep grid for inspection", roles.RejectRole)
        msg.exec()
        if msg.clickedButton() == clear_btn:
            self.clear_current_import_selection(remove_metadata_files=True)
        else:
            self.import_summary.setText(
                "ERROR: Video/TOML mismatch detected. This import is blocked from running until corrected."
            )
        return False

    def import_tomls(self) -> None:
        video = self.video_edit.text().strip()
        folder = self.toml_folder_edit.text().strip()
        pipeline = self.pipeline_combo.currentData()
        tag = self.metadata_tag_edit.text().strip()
        if not video or not folder:
            QMessageBox.warning(self, "Missing input", "Select both an original video and a TOML folder.")
            return
        cmd = [sys.executable, str(MANAGER), "import", "--video", video, "--toml-folder", folder, "--pipeline", pipeline]
        if tag:
            cmd += ["--metadata-tag", tag]
        if self.selected_toml_files:
            for toml_file in self.selected_toml_files:
                cmd += ["--toml-file", str(toml_file)]
        elif self.recursive_check.isChecked():
            cmd += ["--recursive"]
        self.log("Running: " + " ".join(shlex.quote(x) for x in cmd))
        res = run_cmd(cmd)
        self.log(res.stdout.strip())
        if res.stderr.strip():
            self.log(res.stderr.strip())
        if res.returncode != 0:
            QMessageBox.critical(self, "Import failed", res.stderr or res.stdout)
            return
        self.load_grid()
        if not self.handle_mismatch_after_import():
            return
        self._ask_existing_output_action(self._scan_existing_outputs())

    def refresh_validation(self, show_message: bool = True) -> None:
        folder = self.current_toml_folder()
        if not folder:
            if show_message:
                QMessageBox.warning(self, "Missing TOML folder", "Select a TOML folder first.")
            return
        cmd = [sys.executable, str(MANAGER), "refresh", "--toml-folder", str(folder)]
        self.log("Running: " + " ".join(shlex.quote(x) for x in cmd))
        res = run_cmd(cmd)
        self.log(res.stdout.strip())
        if res.stderr.strip():
            self.log(res.stderr.strip())
        if res.returncode != 0:
            QMessageBox.critical(self, "Refresh failed", res.stderr or res.stdout)
            return
        self.load_grid()

    def selected_rows(self) -> List[Dict[str, str]]:
        selected_indexes = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        row_numbers = sorted({idx.row() for idx in selected_indexes})
        if not row_numbers:
            current = self.table.currentRow()
            if current >= 0:
                row_numbers = [current]
        return [self.last_rows[r] for r in row_numbers if 0 <= r < len(self.last_rows)]

    def set_selected_toml_run_status(self, include: bool) -> None:
        rows = self.selected_rows()
        if not rows:
            QMessageBox.warning(self, "No selection", "Select one or more TOML rows first.")
            return
        folder = self.current_toml_folder()
        if not folder:
            QMessageBox.warning(self, "Missing TOML folder", "Select/import a TOML folder first.")
            return
        paths = [row.get("toml_path", "") for row in rows if row.get("toml_path", "")]
        count = len(paths)
        preview = "\n".join(paths[:10])
        if count > 10:
            preview += f"\n... plus {count - 10} more"
        if include:
            title = f"Restore {count} TOML file(s) to run?"
            msg = f"Restore the selected TOML file(s) to the next SLURM/post-processing run?\n\n{preview}"
            include_value = "YES"
        else:
            title = f"Remove {count} TOML file(s) from run?"
            msg = (
                "Remove the selected TOML file(s) from consideration for the next run?\n\n"
                "The TOML files will NOT be deleted. Existing IDtracker sessions and post-processing outputs will also be left alone.\n\n"
                f"{preview}"
            )
            include_value = "NO"
        reply = QMessageBox.question(self, title, msg)
        if reply != qt_checked_yes():
            return
        cmd = [sys.executable, str(MANAGER), "set-run-status", "--toml-folder", str(folder), "--include", include_value]
        for toml_path in paths:
            cmd += ["--toml-path", toml_path]
        res = run_cmd(cmd)
        self.log(res.stdout.strip())
        if res.returncode != 0:
            QMessageBox.critical(self, "Run-status update failed", res.stderr or res.stdout)
            return
        self.refresh_validation(show_message=False)

    def open_toml_folder(self) -> None:
        folder = self.current_toml_folder()
        if not folder:
            return
        self.log(f"TOML folder: {folder}")
        subprocess.Popen(["xdg-open", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def submit_postprocessing_only(self) -> None:
        """Submit only the CPU post-processing stage for the included TOMLs.

        This intentionally skips both IDtracker.ai and session collection. It
        expects the matching completed session folders to already exist under
        <TOML folder>/idtracker_sessions.
        """
        folder = self.current_toml_folder()
        if not folder:
            QMessageBox.warning(self, "Missing TOML folder", "Select/import a TOML folder first.")
            return
        if self.last_rows:
            self.save_grid_edits()

        cmd = [sys.executable, str(MANAGER), "build-manifest", "--toml-folder", str(folder)]
        if self.strict_check.isChecked():
            cmd.append("--strict")
        res = run_cmd(cmd)
        self.log(res.stdout.strip())
        if res.stderr.strip():
            self.log(res.stderr.strip())
        if res.returncode != 0:
            QMessageBox.critical(self, "Manifest failed", res.stderr or res.stdout)
            return

        manifest = self.manifest_path()
        n = 0
        if manifest and manifest.exists():
            with manifest.open(newline="", encoding="utf-8") as f:
                n = sum(1 for _ in csv.DictReader(f))
        if n == 0:
            QMessageBox.warning(
                self,
                "No runnable TOMLs",
                "The manifest has zero runnable rows. Include at least one valid TOML or turn off strict mode.",
            )
            return

        session_root = folder / "idtracker_sessions"
        if not session_root.exists():
            QMessageBox.warning(
                self,
                "No collected sessions found",
                f"Post-processing-only mode requires existing IDtracker sessions in:\n{session_root}\n\n"
                "No IDtracker job was submitted.",
            )
            return

        export_common = {
            "TOML_ROOT": str(folder),
            "PROJECT_DIR": str(folder),
            "SESSION_OUTPUT_ROOT": str(session_root),
            "POSTPROCESS_OUTPUT_ROOT": str(folder / "postprocessing"),
            "PACKAGE_DIR": str(PACKAGE_DIR),
            "METADATA_MANIFEST": str(manifest),
            "CONDA_ENV": self.conda_env.text().strip() or "idtrackerai",
            "CONDA_INIT": self.conda_init.text().strip(),
            "WINDOW_FRAMES": str(self.window_frames.value()),
            "FPS": str(self.fps.value()),
            "MOVE_THRESHOLD_PX": str(self.move_threshold.value()),
            "MOVEMENT_ONSET_CONSECUTIVE_FRAMES": str(self.movement_onset.value()),
            "MAX_STEP_PX": str(self.max_step.value()),
            "ROI_WALL_BUFFER_PX": str(self.roi_wall_buffer.value()),
            "TURTLING_WINDOW_FRAMES": str(self.turtling_window.value()),
            "TURTLING_MIN_DURATION_FRAMES": str(self.turtling_min.value()),
            "CONTACT_PX": str(self.contact_px.value()),
            "MIN_CONTACT_S": str(self.min_contact_s.value()),
            "FIGHT_PX": str(self.fight_px.value()),
            "MIN_FIGHT_FRAMES": str(self.min_fight_frames.value()),
            "ANALYSIS_START_FRAME": "-1",
        }
        export_post = ",".join(
            ["ALL", "MODE=postprocess"]
            + [f"{k}={quote_export(v)}" for k, v in export_common.items()]
        )
        post_args = postprocess_sbatch_args(self.post_partition.text())
        post_cmd = [
            "sbatch",
            *post_args,
            "--job-name=idtracker_postprocess_cpu",
            "--chdir",
            str(folder),
            f"--export={export_post}",
            str(SLURM_SCRIPT),
        ]
        self.log("Submitting CPU post-processing-only job: " + " ".join(shlex.quote(x) for x in post_cmd))
        post_res = run_cmd(post_cmd, cwd=folder)
        self.log(post_res.stdout.strip())
        if post_res.stderr.strip():
            self.log(post_res.stderr.strip())
        if post_res.returncode != 0:
            QMessageBox.critical(self, "Post-processing submission failed", post_res.stderr or post_res.stdout)
            return

        post_job_id = parse_job_id(post_res.stdout)
        self.active_toml_folder = folder
        self.active_job_ids = [j for j in [post_job_id] if j]
        self.notified_completion = False
        self.save_run_state("", post_job_id, n, collect_job_id="")
        self.set_running_state(True)
        QMessageBox.information(
            self,
            "Post-processing submitted",
            f"Submitted CPU post-processing job {post_job_id or 'unknown'} for {n} included TOML(s).\n\n"
            "IDtracker.ai was not run and no GPU was requested.",
        )
        self.refresh_squeue()
        self.tabs.setCurrentIndex(2)

    def submit_slurm(self) -> None:
        folder = self.current_toml_folder()
        if not folder:
            QMessageBox.warning(self, "Missing TOML folder", "Select/import a TOML folder first.")
            return
        if self.last_rows:
            self.save_grid_edits()
        cmd = [sys.executable, str(MANAGER), "build-manifest", "--toml-folder", str(folder)]
        if self.strict_check.isChecked():
            cmd.append("--strict")
        res = run_cmd(cmd)
        self.log(res.stdout.strip())
        if res.stderr.strip():
            self.log(res.stderr.strip())
        if res.returncode != 0:
            QMessageBox.critical(self, "Manifest failed", res.stderr or res.stdout)
            return
        manifest = self.manifest_path()
        n = 0
        if manifest and manifest.exists():
            with manifest.open(newline="", encoding="utf-8") as f:
                n = sum(1 for _ in csv.DictReader(f))
        if n == 0:
            QMessageBox.warning(self, "No runnable TOMLs", "The manifest has zero runnable rows. Fix validation errors or turn off strict mode.")
            return

        if self.existing_output_action == "do_nothing":
            QMessageBox.information(self, "No action selected", "Existing outputs were detected and you chose Do nothing. Import again to choose a different rerun option, or change the TOML folder.")
            return

        export_common = {
            "TOML_ROOT": str(folder),
            "PROJECT_DIR": str(folder),
            "SESSION_OUTPUT_ROOT": str(folder / "idtracker_sessions"),
            "POSTPROCESS_OUTPUT_ROOT": str(folder / "postprocessing"),
            "PACKAGE_DIR": str(PACKAGE_DIR),
            "METADATA_MANIFEST": str(manifest),
            "CONDA_ENV": self.conda_env.text().strip() or "idtrackerai",
            "CONDA_INIT": self.conda_init.text().strip(),
            "WINDOW_FRAMES": str(self.window_frames.value()),
            "FPS": str(self.fps.value()),
            "MOVE_THRESHOLD_PX": str(self.move_threshold.value()),
            "MOVEMENT_ONSET_CONSECUTIVE_FRAMES": str(self.movement_onset.value()),
            "MAX_STEP_PX": str(self.max_step.value()),
            "ROI_WALL_BUFFER_PX": str(self.roi_wall_buffer.value()),
            "TURTLING_WINDOW_FRAMES": str(self.turtling_window.value()),
            "TURTLING_MIN_DURATION_FRAMES": str(self.turtling_min.value()),
            "CONTACT_PX": str(self.contact_px.value()),
            "MIN_CONTACT_S": str(self.min_contact_s.value()),
            "FIGHT_PX": str(self.fight_px.value()),
            "MIN_FIGHT_FRAMES": str(self.min_fight_frames.value()),
            "ANALYSIS_START_FRAME": "-1",
        }

        if self.existing_output_action == "postprocess_only":
            export_post = ",".join(["ALL", "MODE=postprocess"] + [f"{k}={quote_export(v)}" for k, v in export_common.items()])
            post_args = postprocess_sbatch_args(self.post_partition.text())
            post_cmd = ["sbatch"] + post_args + ["--chdir", str(folder), f"--export={export_post}", str(SLURM_SCRIPT)]
            self.log("Submitting post-processing-only job: " + " ".join(shlex.quote(x) for x in post_cmd))
            post_res = run_cmd(post_cmd, cwd=folder)
            self.log(post_res.stdout.strip())
            if post_res.stderr.strip():
                self.log(post_res.stderr.strip())
            if post_res.returncode != 0:
                QMessageBox.critical(self, "Post-processing submission failed", post_res.stderr or post_res.stdout)
                return
            post_job_id = parse_job_id(post_res.stdout)
            self.active_toml_folder = folder
            self.active_job_ids = [j for j in [post_job_id] if j]
            self.notified_completion = False
            self.save_run_state("", post_job_id, n, collect_job_id="")
            self.set_running_state(True)
            QMessageBox.information(self, "Submitted", f"Submitted post-processing-only job {post_job_id or 'unknown'}.")
            self.refresh_squeue()
            self.tabs.setCurrentIndex(2)
            return

        if self.existing_output_action == "full_rerun":
            self._cleanup_managed_outputs_for_manifest(manifest)

        export_id = ",".join(["ALL", "MODE=idtracker"] + [f"{k}={quote_export(v)}" for k, v in export_common.items()])
        array_limit = self.array_limit.value()
        gpu_args = sbatch_resource_args(self.gpu_mode.currentData(), self.custom_gpu_flags.text())
        id_cmd = ["sbatch"] + gpu_args + [f"--array=1-{n}%{array_limit}", "--chdir", str(folder), f"--export={export_id}", str(SLURM_SCRIPT)]
        self.log("Submitting IDtracker array: " + " ".join(shlex.quote(x) for x in id_cmd))
        id_res = run_cmd(id_cmd, cwd=folder)
        self.log(id_res.stdout.strip())
        if id_res.stderr.strip():
            self.log(id_res.stderr.strip())
        if id_res.returncode != 0:
            QMessageBox.critical(self, "SLURM submission failed", id_res.stderr or id_res.stdout)
            return
        job_id = parse_job_id(id_res.stdout)
        if not job_id:
            QMessageBox.warning(self, "Submitted but job ID unknown", id_res.stdout)
            return

        # Stage 2: collect/move completed IDtracker sessions after the full array finishes.
        # Use afterany rather than afterok so the collector can still rescue successfully
        # created sessions and write a clear per-cell failure report if one array task failed.
        export_collect = ",".join(["ALL", "MODE=collect"] + [f"{k}={quote_export(v)}" for k, v in export_common.items()])
        collect_args = postprocess_sbatch_args(self.post_partition.text())
        collect_cmd = ["sbatch"] + collect_args + [f"--dependency=afterany:{job_id}", "--chdir", str(folder), f"--export={export_collect}", str(SLURM_SCRIPT)]
        self.log("Submitting dependent session-collection job: " + " ".join(shlex.quote(x) for x in collect_cmd))
        collect_res = run_cmd(collect_cmd, cwd=folder)
        self.log(collect_res.stdout.strip())
        if collect_res.stderr.strip():
            self.log(collect_res.stderr.strip())
        if collect_res.returncode != 0:
            QMessageBox.critical(self, "Session-collection submission failed", collect_res.stderr or collect_res.stdout)
            return
        collect_job_id = parse_job_id(collect_res.stdout)
        if not collect_job_id:
            QMessageBox.warning(self, "Collector submitted but job ID unknown", collect_res.stdout)
            return

        # Stage 3: post-processing runs only if the collector verifies every expected cell.
        export_post = ",".join(["ALL", "MODE=postprocess"] + [f"{k}={quote_export(v)}" for k, v in export_common.items()])
        post_args = postprocess_sbatch_args(self.post_partition.text())
        post_cmd = ["sbatch"] + post_args + [f"--dependency=afterok:{collect_job_id}", "--chdir", str(folder), f"--export={export_post}", str(SLURM_SCRIPT)]
        self.log("Submitting dependent post-processing job: " + " ".join(shlex.quote(x) for x in post_cmd))
        post_res = run_cmd(post_cmd, cwd=folder)
        self.log(post_res.stdout.strip())
        if post_res.stderr.strip():
            self.log(post_res.stderr.strip())
        if post_res.returncode != 0:
            QMessageBox.critical(self, "Post-processing submission failed", post_res.stderr or post_res.stdout)
            return
        post_job_id = parse_job_id(post_res.stdout)

        self.active_toml_folder = folder
        self.active_job_ids = [j for j in [job_id, collect_job_id, post_job_id] if j]
        self.notified_completion = False
        self.save_run_state(job_id, post_job_id, n, collect_job_id=collect_job_id)
        self.set_running_state(True)
        QMessageBox.information(
            self,
            "Submitted",
            f"Submitted IDtracker array job {job_id}.\n"
            f"Session-collection job: {collect_job_id}.\n"
            f"Post-processing job: {post_job_id or 'unknown'}.\n"
            "Settings are locked until these jobs leave the SLURM queue."
        )
        self.refresh_squeue()
        self.tabs.setCurrentIndex(2)

    def get_pipeline_summary_text(self, folder: Optional[Path]) -> str:
        if not folder:
            return "No TOML folder selected."
        if not STATUS_TOOL.exists():
            return f"Status tool missing: {STATUS_TOOL}"
        res = run_cmd([sys.executable, str(STATUS_TOOL), "--toml-folder", str(folder)])
        text = res.stdout.strip() or "No status summary returned."
        if res.stderr.strip():
            text += "\n\nStatus-tool stderr:\n" + res.stderr.strip()
        return text

    def show_pipeline_summary(self) -> None:
        folder = self.active_toml_folder or self.current_toml_folder()
        text = self.get_pipeline_summary_text(folder)
        self.status_text.setPlainText(text)

        dlg = QDialog(self)
        dlg.setWindowTitle("Pipeline summary")
        dlg.resize(1050, 720)
        layout = QVBoxLayout(dlg)
        box = QTextEdit()
        box.setReadOnly(True)
        box.setPlainText(text)
        layout.addWidget(box)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn)
        dlg.exec()


    def refresh_squeue(self) -> None:
        user = os.environ.get("USER", "")
        cmd = ["squeue", "-u", user] if user else ["squeue"]
        res = run_cmd(cmd)
        text = "$ " + " ".join(cmd) + "\n" + (res.stdout or "")
        if res.stderr:
            text += "\n" + res.stderr

        folder_for_summary = self.active_toml_folder or self.current_toml_folder()
        if folder_for_summary:
            text += "\n\nPipeline stage summary:\n" + self.get_pipeline_summary_text(folder_for_summary)

        active_text = ""
        if self.active_job_ids:
            job_arg = ",".join(self.active_job_ids)
            active_cmd = ["squeue", "-h", "-j", job_arg, "-o", "%i %T %j %R"]
            active_res = run_cmd(active_cmd)
            active_text = active_res.stdout.strip()
            text += "\n\nTracked jobs:\n$ " + " ".join(active_cmd) + "\n" + (active_text or "No tracked jobs currently in squeue.")
            if active_res.stderr.strip():
                text += "\n" + active_res.stderr.strip()
            if not active_text and not self.notified_completion:
                self.notified_completion = True
                self.set_running_state(False)
                QApplication.beep()
                marker = ""
                folder = self.active_toml_folder or self.current_toml_folder()
                if folder:
                    complete_markers = list((folder / "postprocessing").glob("*/_POSTPROCESS_COMPLETE_ALL_CELLS.txt"))
                    incomplete_markers = list((folder / "postprocessing").glob("*/_POSTPROCESS_INCOMPLETE_OR_FAILED.txt"))
                    if complete_markers:
                        marker = f"\n\nAll-cells completion marker found: {complete_markers[-1]}"
                    elif incomplete_markers:
                        marker = f"\n\nIncomplete/failed marker found: {incomplete_markers[-1]}"
                    else:
                        marker = "\n\nNo post-processing completion marker found yet. Check logs if outputs are missing."
                summary = self.get_pipeline_summary_text(folder) if folder else ""
                QMessageBox.information(self, "SLURM jobs finished", "The tracked SLURM jobs are no longer in the queue." + marker + ("\n\n" + summary[:1500] if summary else ""))

        self.status_text.setPlainText(text)


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec() if QT_BINDING != "PyQt5" else app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
