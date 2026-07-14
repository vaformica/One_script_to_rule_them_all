# IDtracker Pipeline Controller

A Mac-hosted Streamlit controller for managing IDtracker.ai analysis on Firebird through SSH and SLURM.

## Core design

- Videos and master TOMLs remain on Firebird.
- The application scans configured Firebird folders over SSH.
- Repeated scans update a stable catalog rather than creating duplicate rows.
- Every analysis submission creates an immutable run record and a new remote run directory.
- Master TOMLs are never edited.
- Run-specific TOML snapshots can be modified for:
  - minimum blob area
  - maximum blob area
  - background-difference threshold
- IDtracker and post-processing are tracked as separate pipeline stages.
- Track PNGs and compact summary CSVs can be retrieved to the Mac.
- Every track can receive a formal QC decision:
  - accepted
  - rejected
  - rerun needed
  - review later
- Only accepted run-unit outputs should be exported into the final scientific dataset.

## Current release

This is an MVP. It provides:

1. Three-tab Streamlit interface:
   - Files & Runs
   - Settings
   - Status & QC
2. Remote video/TOML scanning through SSH.
3. Stable deduplication using analysis-unit IDs.
4. Local SQLite catalog.
5. Immutable run creation and settings snapshots.
6. TOML threshold editing on copied TOMLs.
7. SLURM script generation and submission hooks.
8. Job monitoring with `squeue` and `sacct`.
9. Quick-result retrieval with `rsync`.
10. PNG-based QC records.
11. CSV exports for catalog, run history, and accepted results.

## Important limitations

- The exact Firebird IDtracker command and post-processing command must be configured for your environment.
- The generated SLURM templates are conservative examples and may need partition, account, module, and Conda changes.
- Full end-to-end GPU execution has not been tested from this environment.
- The application currently assumes one primary user and is designed to leave room for later multi-user support.

## Quick installation on macOS

```bash
cd idtracker-pipeline-controller
bash scripts/install_mac.sh
```

Then edit:

```text
config/config.toml
```

Launch:

```bash
bash scripts/launch_app.sh
```

The app normally opens at:

```text
http://localhost:8501
```

## SSH setup

The cleanest setup is a host alias in `~/.ssh/config`:

```sshconfig
Host firebird
    HostName firebird.cluster
    User vformic1-swat
```

Test it:

```bash
ssh firebird 'hostname'
```

The app never stores your password or SSH private key.

## Project files created by the app

By default, local project state is stored under:

```text
~/IDtrackerPipelineController/
```

This includes:

```text
catalog/
runs/
retrieved_results/
exports/
logs/
```

The authoritative large data and remote sessions remain on Firebird.

## Versioning

Use semantic versioning:

- `0.x`: development releases
- `1.0.0`: first dependable production release
- patch releases: bug fixes
- minor releases: backward-compatible features
- major releases: workflow or schema changes

See `CHANGELOG.md`.
