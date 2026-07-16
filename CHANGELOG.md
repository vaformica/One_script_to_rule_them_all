# Changelog

## 0.9.8
- Refined the fight-only 3-D space-time track page to use an idTracker-style corner view.
- Retained orange/blue animal identity colors along the complete 3-D trajectories.
- Added ROI outlines on the z=0 floor, colored start/end markers, and timed interaction stars.
- Broke 3-D paths across missing data and implausible jumps.
- Preserved v0.9.7 as the confirmed rollback/source baseline.

## v0.9.6

- Corrected fight left/right classification to represent each beetle's **starting side**.
- Starting X is the median valid X coordinate in the first 30 analyzed frames by default.
- Added `--starting-position-frames` for explicit control.
- Replaced mean-position output columns with clearly named starting-position columns.
- v0.9.5 remains the source baseline for this targeted correction.

## v0.9.5
- Added mean-X left/right identity columns to fight individual and pair summaries.
- Printed the run name directly beneath the legend on every track image and PDF page.
- Fight runs no longer create BA summary files; BA runs no longer create fight summary files.
- Preserved v0.9.4 as the confirmed source baseline.

## v0.9.3
- Track images now visibly contain camera, run, date, video, cell, and record ID.
- Fight PDF pages inherit the same visible labels.
- Added sortable QC Date run column.
- Preserved v0.9.2 as the confirmed source baseline.

# Changelog

## 0.8.2
- Leave canonical IDtracker sessions exactly where IDtracker creates them.
- Remove all session copying and moving from tracking jobs.
- Record only `session_path.txt` and `session_link.txt` in immutable run folders.
- Mark archive status as `DEFERRED`; session management can be performed later as a separate operation.
- Remove the session-archive checkbox from the GUI.


## 0.8.0
- Decoupled tracking, postprocessing, archiving, and QC.
- Added deterministic canonical session resolution from TOML video path and name.
- Added postprocessing-only submission mode.
- Added Check All, Uncheck All, and Invert Selection.
- Added GUI BA and fight parameter editor.
- Added optional nonfatal session archive and permanent session_link.txt.
- Added QC/run_status.csv, consolidated summaries, track maps, and QC_Report.html.

# Changelog

## 0.7.2

- Fixed headless SLURM execution by adding the required `--track` flag.
- Prevented IDtracker from launching the segmentation GUI on compute nodes.
- Made the Firebird installer quick by default and reusable across code updates.
- Added `scripts/firebird/quick_install.sh`, which never modifies Conda environments.
- Added explicit `--update-env` mode for dependency changes.


## 0.7.1

- Fixed IDtracker.ai 6.0.10 rejection of mixed integer/float threshold pairs.
- Threshold pairs are normalized to one TOML numeric type before writing.
- Mac and Firebird validators now reject `[25.0, 255]`-style arrays.
- Background continues to edit the minimum intensity threshold only.


## 0.7.0

- Added persistent Mac-side SQLite indexing for Firebird scans.
- Added background-thread index rebuilds and cancellation.
- Added fast Search Existing Index mode.
- Fixed nested threshold-array editing.
- Added Mac and Firebird TOML validation.
- Changed Background to the minimum intensity threshold; maximum is preserved.


## 0.6.1

- Fixed the Mac GUI class structure introduced in v0.6.0.
- Restored `choose_key`, SSH, scan, submission, and diagnostic methods as members of `Window`.
- Added startup validation for all required GUI methods.
- Added a safe Mac replacement helper that preserves user configuration.


## 0.6.0

- Routed every SLURM stdout/stderr file into the corresponding run folder.
- Added persistent `job_ids.env` to each run.
- Added Firebird run-diagnostic command.
- Added Mac Jobs and Diagnostics tab.
- Added queue, accounting, dependency, session, and log queries.
- Added guarded cancellation of permanently blocked dependent jobs.


## 0.5.0

- Embedded the exact uploaded BA and fight source files.
- Added one unified single-session analyzer.
- Added one unified recursive batch runner.
- Consolidated BA and fight trajectory cleaning, ROI metrics, turtling, and maps.
- Standardized both assays on one track-map function.
- Added automatic one-animal BA / two-animal fight selection.
- Preserved uploaded originals for regression comparison.
- Replaced separate BA/fight pipeline adapters with one unified processor.
- Removed runtime GitHub source import.

## 0.4.0

- Unified Mac GUI, Firebird execution, metadata, and collector.

## 0.8.1

- Removed the runtime dependency on `tomlkit` from session discovery.
- Validate tracking success from the canonical session contents rather than relying only on the `idtrackerai` process exit code.
- Preserve nonzero IDtracker exit codes as warnings when a complete session exists.
- Default postprocessing and collection to the `beetle_pipeline` Conda environment.
- Run the collector after any postprocessing outcome so failed analyses are recorded in QC.

## 0.8.3

- Session discovery now follows one deterministic rule: read the video path and session name from the TOML, then use `<video directory>/session_<name>`.
- The TOML file's directory and pipeline run directory are no longer used to infer session location.
- Multiline `video_paths` arrays are parsed without third-party TOML packages.
- Session errors now print the TOML, encoded video path, expected session path, and exact missing files.
- Added `INSTALL_UPDATE.command`, a one-step Mac-to-Firebird installer for the working repository.
- Existing Mac and Firebird Conda environments are reused and are not recreated during routine code updates.

## v0.8.4
- Fixed postprocessing and collector imports when SLURM starts jobs outside the repository.
- Added repository-root `PYTHONPATH` exports to both downstream SLURM scripts.
- Added direct-execution path protection inside the postprocessing and collector entry points.
- Added Firebird import smoke tests to the installer.
- Updated the requested default connection, search, output, and repository paths.
- Added a single `INSTALL_UPDATE.command` that reuses existing Conda environments, syncs to Firebird, installs, and validates the active code.

## 0.9.0
- Preserves v0.8.4 as the confirmed rollback/source baseline.
- Adds globally useful record IDs to metadata, CSVs, image labels, and collected filenames.
- Builds one multi-page track PDF per fight; BA tracks remain PNG files.
- Adds direct Downloads-folder result downloads from the Mac GUI.
- Adds a QC tab with DONE, NEEDS RERUN, and PENDING decisions.
- Rebuilds separate BA and fight master individual-summary CSVs from DONE runs only.
- Makes the TOML import table sortable without breaking row-to-TOML associations.

## v0.9.1 — QC index migration hotfix

- Automatically normalizes older `QC/run_status.csv` schemas.
- Preserves prior QC decisions when possible.
- Writes the QC index atomically to avoid partial files.
- Prevents legacy columns such as `tracking`, `post`, `archive`, `status`, `qc`, and `track_map` from crashing collection.

## 0.9.2
- Added flat `QC_review_bundle` folders containing all individual-summary CSVs and QC track files.
- Added **View Files** to the QC tab; it downloads and opens the selected bundle and fight PDF.
- Fight QC now uses one deduplicated multipage PDF per fight.
- BA QC continues to use PNG track maps.
- Changed the default window length from 7200 to 7500 frames across active and legacy entry points.
- Preserved v0.9.1 as the confirmed rollback/source baseline.

## 0.9.4
- Added QC lifecycle statuses, automatic superseding, replacement provenance, and text/status filtering.

## 0.9.7

- Fixed starting-side detection when early analyzed frames are NaN by using the first N valid tracked positions.
- Added fight-only interaction-location track maps with contact stars.
- Added an experimental fight-only 3D X/Y/time track page.
- Preserved the full analysis-generated fight PDF through QC collection and downloads.
