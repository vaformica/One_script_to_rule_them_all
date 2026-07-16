# Git-Ready Release Summary — v0.7.0

## Semantic version

`0.7.0`

## Git commit message

```text
Add persistent indexing and safe IDtracker TOML threshold handling
```

## GitHub release title

Beetle IDtracker Unified Pipeline v0.7.0 — Indexed Search and TOML Safety

## New features

- Persistent SQLite index on the Mac:
  `~/.beetle_idtracker/firebird_index.sqlite3`
- **Rebuild Firebird Index** for full remote discovery and parsing.
- **Search Existing Index** for fast repeat searches.
- Responsive Qt worker thread, progress status, and cancellation.
- Firebird preflight validator before any SLURM submission.

## Bug fixes

- Preserves nested per-ROI threshold arrays.
- Rejects mixed scalar/nested arrays before IDtracker sees them.
- Parses the generated TOML again before upload.
- Uses exact video names embedded in TOMLs.
- Handles ambiguous duplicate video filenames as unmatched.

## Background threshold behavior

The Background column now represents the **minimum intensity threshold**.
The maximum intensity threshold remains unchanged from the source TOML.

## Performance improvements

Repeated searches use the local index instead of recursively scanning Firebird.
Generated run, session, log, output, archive, cache, and Git folders are pruned.

## Upgrade notes

This release changes both Mac and Firebird files.

Mac:

```bash
bash scripts/mac/install.sh
bash scripts/mac/launch.sh
```

Firebird:

```bash
bash scripts/firebird/install.sh
```

Use **Rebuild Firebird Index** once after upgrading.

## High-level changed files

- `app/mac_gui.py`
- `scripts/firebird/submit_pipeline_run.sh`
- `scripts/firebird/validate_run_toml.py`
- `CHANGELOG.md`
- `VERSION`
- `docs/GIT_RELEASE_v0.7.0.md`

## Known issues

- Index refresh is manual.
- Cancellation may wait for an active SSH command to return.
- Rebuilds still read every relevant TOML once.

## Recommended tests

1. Rebuild on a small Firebird directory.
2. Search Existing Index and confirm fast local loading.
3. Test flat threshold arrays.
4. Test nested threshold arrays.
5. Confirm background minima change while maxima remain unchanged.
6. Submit a valid TOML and confirm preflight succeeds.
7. Submit a deliberately malformed TOML and confirm no SLURM job is created.
