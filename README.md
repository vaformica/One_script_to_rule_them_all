# Beetle IDtracker Unified Pipeline

This repository now contains the actual uploaded BA and fight post-processing
source and a new consolidated analyzer.

## Primary analysis files

```text
analysis/analyze_idtracker_unified.py
analysis/run_idtracker_unified_batch.py
```

These are the only post-processing analysis scripts used by the pipeline.

The exact uploaded originals are retained under:

```text
analysis/legacy_uploaded_20260715/
```

## One shared computational stream

The unified analyzer automatically supports:

- one-animal behavioral assays
- two-animal fights

Shared operations are performed once:

- trajectory loading and normalization
- ROI loading
- artifact filtering and interpolation
- movement onset
- movement and distance metrics
- ROI and wall-buffer metrics
- turtling detection
- standardized track maps
- per-frame kinematics
- summary writing

Fight mode adds pairwise contact and possible-fight calculations after both
animals pass through the same individual-analysis stream.

## One shared track-map implementation

BA and fight maps now come from the same `plot_tracks()` function, with the
same:

- ROI-local coordinate system
- thin normal track lines
- start and end symbols
- sustained movement-onset stars
- gray interpolated markers
- black turtling markers
- ROI outlines
- output resolution and layout

## Pipeline

```text
Mac GUI
  → SSH
  → IDtracker SLURM
  → afterok: unified post-processing
  → metadata enrichment
  → afterok: collector
  → review/all_tracks
  → review/all_summaries
  → review/all_manifests
```

## Install on Firebird

```bash
cd /data/labs/vformic1-swat-lab/Beetle_IDtracker_Pipeline
bash scripts/firebird/install.sh
bash scripts/firebird/verify_analysis_sources.sh
```

No GitHub import is required. The analysis files are included directly.

## Install on the Mac

```bash
cd Beetle_IDtracker_Pipeline
bash scripts/mac/install.sh
bash scripts/mac/launch.sh
```

## Direct post-processing example

Automatic BA/fight detection:

```bash
python analysis/run_idtracker_unified_batch.py \
  --search-root /path/to/session_or_parent \
  --output-root /path/to/output \
  --analysis-type auto \
  --window-frames 7200 \
  --overwrite
```

Force behavioral assay:

```bash
python analysis/run_idtracker_unified_batch.py \
  --search-root /path/to/session \
  --output-root /path/to/output \
  --analysis-type ba \
  --overwrite
```

Force fight:

```bash
python analysis/run_idtracker_unified_batch.py \
  --search-root /path/to/session \
  --output-root /path/to/output \
  --analysis-type fight \
  --overwrite
```

## Outputs

BA:

```text
*_ba_individual_summary.csv
*_animal0_per_frame_kinematics.csv
*_turtling_events_animal0.csv
*_track_map.png
*_tracks.pdf
```

Fight:

```text
*_combat_pair_summary.csv
*_combat_individual_summary.csv
*_per_frame_pairwise.csv
*_contact_events.csv
*_possible_fight_events.csv
*_track_map.png
*_track_map_animal0.png
*_track_map_animal1.png
*_tracks.pdf
```

Merged batch outputs:

```text
ba_individual_summary_all.csv
combat_pair_summary_all.csv
combat_individual_summary_all.csv
postprocessing_manifest.csv
batch_summary.json
all_track_maps/
```

## Critical validation

The uploaded source was consolidated structurally and all Python files compile.
Before large-scale use, compare one known BA result and one known fight result
against the archived uploaded scripts. See:

```text
docs/UNIFIED_ANALYSIS_DESIGN.md
```


## Run-specific logs

Every stage now writes directly into its run folder:

```text
<run_dir>/logs/idtracker_<jobid>.out
<run_dir>/logs/idtracker_<jobid>.err
<run_dir>/logs/postprocess_<jobid>.out
<run_dir>/logs/postprocess_<jobid>.err
<run_dir>/logs/collector_<jobid>.out
<run_dir>/logs/collector_<jobid>.err
```

The submission helper also writes:

```text
<run_dir>/job_ids.env
```

with the three SLURM job IDs.

## Built-in GUI diagnostics

The Mac application includes a **Jobs and Diagnostics** tab. For each submitted
run it can retrieve:

- current `squeue` state and pending reason
- `sacct` history, state, elapsed time, and exit code
- active dependency information
- run metadata
- recorded job IDs
- session-folder discovery
- trajectory and JSON files found in the session
- the last 200–300 lines of all run logs
- likely error messages collected from all logs

It also provides a guarded button for cancelling post-processing and collector
jobs that are permanently blocked by failed dependencies.

## v0.8.0 workflow modes

The Parameters tab now provides **Run IDtracker + Postprocess** and **Postprocess Existing Session**. The latter resolves the canonical `session_<name>` beside the source video and never submits a GPU tracking job. Session archives are optional; archive-copy failure is recorded as a warning and does not block postprocessing.

The project-level `QC/` folder contains `run_status.csv`, consolidated BA/fight summary files, renamed track maps, and `QC_Report.html`. Each immutable run contains `session_link.txt`, which always points to the canonical IDtracker session.
