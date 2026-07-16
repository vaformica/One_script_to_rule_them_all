# v0.9.4 — QC lifecycle and filtering

- Adds APPROVED, NEEDS RERUN, RERUNNING, SUPERSEDED, and PENDING workflow states.
- Approving a newer matching run automatically marks older NEEDS RERUN/RERUNNING runs as SUPERSEDED.
- Adds `replaces` and `replaced_by` provenance columns.
- Adds free-text QC filtering across record ID, date, camera/video, cell, analysis, status, and run path.
- Adds a status dropdown filter and clear-filter button.
- Only APPROVED (and legacy DONE) runs enter master spreadsheets.
