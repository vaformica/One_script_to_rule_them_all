# Job Diagnostics

## Automatic log placement

SLURM output and error paths are supplied on the `sbatch` command line. This is
more reliable than relative `#SBATCH --output` directives because every file is
placed in the run's `logs` folder regardless of the shell working directory.

## Mac GUI diagnostic queries

The Jobs and Diagnostics tab performs the following remote checks:

```bash
squeue -j <job IDs>
sacct -j <job IDs> -X -P
scontrol show job <active job ID>
find <run directory>
cat <run directory>/run_metadata.json
cat <run directory>/job_ids.env
cat <run directory>/session_path.txt
tail <run directory>/logs/*.out
tail <run directory>/logs/*.err
grep likely error terms across logs
```

`scontrol` may report an invalid job ID after a completed job has been purged
from the active controller. The diagnostic panel therefore also uses `sacct`,
which retains completed-job accounting information.

## Firebird command-line diagnostic

```bash
bash scripts/firebird/diagnose_pipeline_run.sh \
  /path/to/run_directory
```

An optional job ID may be supplied as a second argument.
