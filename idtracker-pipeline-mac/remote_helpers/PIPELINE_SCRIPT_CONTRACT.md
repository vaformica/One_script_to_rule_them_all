# SLURM Script Contract

The Mac controller submits existing Firebird scripts using `sbatch --export`.

The following variables are exported:

- `PIPELINE_RUN_INDEX`
- `PIPELINE_RUN_TIMESTAMP`
- `PIPELINE_RUN_DIR`
- `PIPELINE_INPUT_DIR`
- `PIPELINE_OUTPUT_DIR`
- `PIPELINE_LOG_DIR`
- `PIPELINE_TOML`
- `PIPELINE_VIDEO`
- `PIPELINE_CELL`
- `PIPELINE_ASSAY_TYPE`
- `PIPELINE_SESSION`

A compatible SLURM script should use these values. At minimum:

```bash
TOML_FILE="${PIPELINE_TOML:?PIPELINE_TOML is required}"
VIDEO_FILE="${PIPELINE_VIDEO:?PIPELINE_VIDEO is required}"
RUN_DIR="${PIPELINE_RUN_DIR:?PIPELINE_RUN_DIR is required}"
```

The controller changes into `PIPELINE_INPUT_DIR` before calling `sbatch`.
Therefore, an existing script that recursively finds TOMLs beneath its current
working directory may work without modification.

Scripts that hard-code unrelated input and output folders need a small adapter.
Test with one TOML before submitting a batch.
