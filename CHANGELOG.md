# Changelog

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
