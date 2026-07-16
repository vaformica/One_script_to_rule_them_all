# v0.9.1 QC index migration hotfix

This release preserves the working v0.9.0 postprocessing and adds backward-compatible migration for QC indexes created by earlier pipeline versions.

The collector now accepts legacy columns including `tracking`, `post`, `archive`, `status`, `qc`, and `track_map`, translates the useful values into the current schema, and atomically rewrites `QC/run_status.csv`.
