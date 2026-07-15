# Migration Plan to the Unified Repository

1. Upload the unified package to Firebird.
2. Run `bash scripts/firebird/install.sh`.
3. Confirm that all four Python files now exist under `analysis/`.
4. Inspect `analysis/SOURCE_VERSIONS.txt`.
5. Run one BA and one fight validation job.
6. Compare scientific summaries with the old workflows.
7. Copy the populated unified repository to the Mac.
8. Create the new GitHub repository and commit the included analysis files.
9. Mark the old BA and fight repositories as archived references.
10. Make all future edits in the unified repository only.

Do not delete old scripts or validated outputs until comparison tests pass.
