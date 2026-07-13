# Firebird IDtracker.ai TOML Folder Pipeline

This package runs a controlled IDtracker.ai workflow on Firebird for one original video and a folder of arena-specific TOML files. It was built for the Formica Lab workflow where students manually create TOMLs in the idtracker.ai Segmentation app, usually one TOML per arena/cell in a larger video, and then use this launcher to run tracking and post-processing in an organized, metadata-safe way.

The current stable architecture is the three-stage pipeline that first worked reliably in v8 and has been retained here:

1. **IDtracker.ai tracking** runs as a GPU SLURM array, one TOML per array task.
2. **Session collection** runs as a CPU SLURM job after tracking and moves the complete IDtracker.ai session folders out of the original video directory into the TOML-folder project structure.
3. **Post-processing** runs as a CPU SLURM job after collection and writes BA or fight outputs with full source-video and TOML provenance in every output row.

## Quick start on Firebird

Activate the idtracker.ai environment, install the package, and launch the GUI:

```bash
conda activate idtrackerai
python -m zipfile -e IDTracker_Firebird_TOML_Folder_Package_v12_final.zip .
cd IDTracker_Firebird_TOML_Folder_Package
bash install_on_firebird.sh
cd ~/formicalab/IDTracker_Firebird_TOML_Folder
bash launch_gui.sh
```

The GUI uses PyQt/PySide. If the GUI does not launch because no Qt binding is installed, install PyQt inside the `idtrackerai` conda environment:

```bash
conda activate idtrackerai
conda install -c conda-forge pyqt -y
```


### Install the one-line `process_tomls` command

From the package directory on Firebird, run:

```bash
conda activate idtrackerai
bash install_process_tomls_command.sh
```

After that, while the `idtrackerai` environment is active, launch the GUI from any directory with:

```bash
process_tomls
```

The command is installed inside that conda environment, not globally.

## Student workflow

### 1. Make TOMLs in idtracker.ai

Students still use the idtracker.ai Segmentation app by hand. This is intentional because ROI boundaries, blob size, background subtraction, and tracking interval require judgment.

For one original video, make one TOML per arena/cell. Name TOMLs with the arena/cell label at the end when possible, such as:

```text
Camera_1_40169154_20260630_1342_FIGHT_ACT1_A1.toml
Camera_1_40169154_20260630_1342_FIGHT_ACT1_A2.toml
Camera_1_40169154_20260630_1342_FIGHT_ACT1_B1.toml
```

Put all TOMLs for that one video in the same TOML folder.

### 2. Import TOMLs in the GUI

Open the GUI and select:

- the original `.mp4`/`.avi` video used to make the TOMLs,
- the folder containing those TOMLs,
- the pipeline type: **Fight** for two animals or **BA** for one animal,
- a metadata tag. If left blank, the package uses the original video stem.

Choose either **Browse TOML folder** to import the whole folder or **Browse TOML file(s)** to import only one or several TOMLs from the same folder. Then click **Import / validate TOML folder**. The GUI creates a grid, one row per imported TOML.

To include or exclude many rows at once, use **Shift-click** for a continuous range or **Ctrl-click / Command-click** for separate rows, then click **Remove selected TOML from run** or **Restore selected TOML to run**.

The importer immediately checks whether each TOML appears to point to the selected video. If a hard mismatch is detected, it blocks the run and shows a clear mismatch dialog. Students should choose the correct video or TOML folder and import again.

### 3. Edit arena labels if needed

The GUI infers `cell_label` from the end of the TOML filename, such as `A1`, `B3`, or `C2`. Students can edit the label in the grid and click **Save edited labels**.

### 4. Remove TOMLs from a run without deleting files

Use **Remove selected TOML from run** to exclude a TOML from the next run. This does not delete the TOML file and does not delete existing outputs. It only sets `run_this_toml = NO` in the import grid.

Use **Restore selected TOML to run** to include it again.

This is useful when most cells have already run successfully and only one TOML needs to be rerun.

### 5. Run

On the Run tab, choose analysis settings and click **Run SLURM pipeline**. The defaults are intended for current Formica Lab Firebird use:

- Analysis window length: 7500 frames
- ROI/wall buffer: 30 pixels
- Any GPU for IDtracker.ai by default
- CPU partition for session collection and post-processing by default

If outputs already exist, the GUI asks whether to:

- rerun IDtracker and overwrite managed outputs,
- rerun Python post-processing only,
- or do nothing.

Python-only reruns are submitted to a CPU SLURM job by default, not to GPU.

## Output structure

For a TOML folder like:

```text
Camera_1_40169154_20260630_1342_FIGHT_ACT1/
```

the output structure is:

```text
Camera_1_40169154_20260630_1342_FIGHT_ACT1/
├── *.toml
├── project_metadata/
│   ├── run_config.json
│   ├── toml_import_grid.csv
│   ├── toml_video_manifest.csv
│   ├── session_collection_report.csv
│   └── pipeline_status_summary.json/txt
├── logs/
├── gui_slurm/
├── idtracker_sessions/
│   └── <video_stem>/
│       ├── <video_stem>__A1/
│       ├── <video_stem>__A2/
│       └── ...
└── postprocessing/
    └── fight_postprocessing/ or ba_postprocessing/
        ├── postprocessing_manifest.csv
        ├── postprocessing_status_by_cell.csv
        ├── *_summary_all.csv
        ├── <metadata_tag>_*_summary_all.csv
        ├── all_track_maps/
        └── session_outputs/
```

Every output row carries provenance columns such as `metadata_tag`, `original_video_name`, `original_video_path`, `cell_label`, `toml_name`, `toml_path`, and `output_stem`. This is critical because output CSVs will later be concatenated across videos and cells.

## Status interpretation

A successful complete run says:

- Overall status: COMPLETE
- Collected sessions with trajectories: N/N
- Post-processed cells OK: N/N
- Warnings: none
- Errors: none

Completion marker files are written only when all expected cells succeed:

```text
project_metadata/_SESSION_COLLECTION_COMPLETE_ALL_CELLS.txt
postprocessing/<pipeline>_postprocessing/_POSTPROCESS_COMPLETE_ALL_CELLS.txt
```

If the status panel is too large, use the **Show pipeline summary** button in the Status tab. It opens a larger scrollable window.

## Standalone post-processing without the GUI

These scripts rerun only Python post-processing using the metadata sheets and collected session folders. They do not run IDtracker.ai and do not move sessions.

Fight:

```bash
conda activate idtrackerai
cd ~/formicalab/IDTracker_Firebird_TOML_Folder
python standalone/run_fight_postprocessing_standalone.py \
  --toml-folder /path/to/TOML_FOLDER
```

BA:

```bash
conda activate idtrackerai
cd ~/formicalab/IDTracker_Firebird_TOML_Folder
python standalone/run_ba_postprocessing_standalone.py \
  --toml-folder /path/to/TOML_FOLDER
```

To submit the standalone post-processing rerun to a CPU SLURM partition:

```bash
python standalone/run_fight_postprocessing_standalone.py \
  --toml-folder /path/to/TOML_FOLDER \
  --submit-cpu \
  --cpu-partition ""
```

If Firebird uses a different CPU partition name, change `--cpu-partition` to that name or pass an empty string to omit the partition flag.

## Troubleshooting commands

Check jobs:

```bash
squeue -u vformic1-swat
```

Cancel old stuck jobs:

```bash
scancel <JOBID>
```

Show timestamps for maps:

```bash
find postprocessing/fight_postprocessing/all_track_maps -type f -name "*track_map*.png" -printf "%TY-%Tm-%Td %TH:%TM:%TS  %p\n" | sort
```

Check the collection report:

```bash
cat project_metadata/session_collection_report.csv
```

Check per-cell post-processing status:

```bash
cat postprocessing/fight_postprocessing/postprocessing_status_by_cell.csv
```

### Rerun analysis without rerunning IDtracker.ai

On the GUI's **Run** tab, use **Run post-processing only (CPU)** when the selected TOMLs already have completed sessions in `idtracker_sessions` and only the analysis needs to be regenerated. The GUI rebuilds the manifest from the TOMLs currently included in the table, submits one CPU SLURM job, overwrites the managed post-processing outputs, and skips both IDtracker.ai and session collection. Use the **Post-processing partition** field only when Firebird requires a named CPU partition; otherwise leave it blank to use the cluster default.
