# Changelog

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
