# SLURM templates

The application generates SLURM scripts inside each immutable run folder.

The generated scripts are based on configuration values in:

```text
config/config.toml
```

Before production use, verify:

- partition names
- account or allocation requirements
- GPU resource syntax
- Conda activation command
- IDtracker command
- post-processing command
- expected session output location
- maximum array concurrency
- runtime and memory requirements

Generated scripts are retained inside each run for reproducibility.
