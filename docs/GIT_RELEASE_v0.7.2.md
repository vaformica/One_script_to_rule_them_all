# Git-Ready Release Summary — v0.7.2

## Diagnosis

The v0.7.1 TOML loaded successfully. The job then logged:

```text
No terminal arguments detected
```

and entered `run_segmentation_GUI(session)`. On a headless SLURM compute node,
Qt could not connect to an X display and aborted.

The prior command was:

```bash
idtrackerai --load "$PIPELINE_TOML"
```

For IDtracker.ai 6.0.10, loading parameters does not imply direct tracking.
The separate `--track` flag is required to bypass the segmentation GUI.

## Fix

The tracking job now runs:

```bash
idtrackerai --track --load "$PIPELINE_TOML"
```

## Quick Firebird installation

Routine code updates:

```bash
bash scripts/firebird/quick_install.sh
```

or:

```bash
bash scripts/firebird/install.sh
```

Both reuse existing environments. The first command guarantees that Conda is
not changed.

Only update dependencies when `environment-firebird.yml` changes:

```bash
bash scripts/firebird/install.sh --update-env
```

## Failed jobs from the reported run

The dependent jobs can be cancelled:

```bash
scancel 836442 836443
```

Submit a new run after installing v0.7.2. Do not reuse the failed immutable run
directory.
