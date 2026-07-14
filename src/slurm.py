from __future__ import annotations

import getpass
import json
from pathlib import Path
from typing import Any

from .config import AppConfig
from .database import Database
from .ssh import SSHClient


IDTRACKER_TEMPLATE = """#!/usr/bin/env bash
#SBATCH --job-name={job_name}
#SBATCH --partition={partition}
#SBATCH --time={time}
#SBATCH --cpus-per-task={cpus}
#SBATCH --mem={memory}
#SBATCH --gres={gres}
#SBATCH --array=1-{unit_count}%{array_limit}
#SBATCH --output={remote_run}/logs/idtracker_%A_%a.out
#SBATCH --error={remote_run}/logs/idtracker_%A_%a.err

set -euo pipefail

{activate_command}

MANIFEST={remote_run}/run_manifest.json

readarray -t VALUES < <(
python3 - "$MANIFEST" "$SLURM_ARRAY_TASK_ID" <<'PY'
import json, sys
manifest_path = sys.argv[1]
ordinal = int(sys.argv[2])
with open(manifest_path) as fh:
    manifest = json.load(fh)
unit = next(x for x in manifest["units"] if int(x["ordinal"]) == ordinal)
print(unit["analysis_unit_id"])
print(unit["video_path"])
print(unit["run_toml_path"])
PY
)

ANALYSIS_UNIT_ID="${{VALUES[0]}}"
VIDEO_PATH="${{VALUES[1]}}"
TOML_PATH="${{VALUES[2]}}"

UNIT_ROOT="{remote_run}/idtracker/${{ANALYSIS_UNIT_ID}}"
mkdir -p "$UNIT_ROOT"
cd "$UNIT_ROOT"

echo "Run ID: {run_id}"
echo "Analysis unit: $ANALYSIS_UNIT_ID"
echo "Video: $VIDEO_PATH"
echo "TOML: $TOML_PATH"
echo "Started: $(date --iso-8601=seconds)"

{command}

echo "Completed: $(date --iso-8601=seconds)"
"""


POSTPROCESS_TEMPLATE = """#!/usr/bin/env bash
#SBATCH --job-name={job_name}
#SBATCH --partition={partition}
#SBATCH --time={time}
#SBATCH --cpus-per-task={cpus}
#SBATCH --mem={memory}
#SBATCH --array=1-{unit_count}%{array_limit}
#SBATCH --output={remote_run}/logs/postprocess_%A_%a.out
#SBATCH --error={remote_run}/logs/postprocess_%A_%a.err

set -euo pipefail

{activate_command}

MANIFEST={remote_run}/run_manifest.json

readarray -t VALUES < <(
python3 - "$MANIFEST" "$SLURM_ARRAY_TASK_ID" <<'PY'
import json, sys
manifest_path = sys.argv[1]
ordinal = int(sys.argv[2])
with open(manifest_path) as fh:
    manifest = json.load(fh)
unit = next(x for x in manifest["units"] if int(x["ordinal"]) == ordinal)
print(unit["analysis_unit_id"])
PY
)

ANALYSIS_UNIT_ID="${{VALUES[0]}}"
SESSION_PATH="{remote_run}/idtracker/${{ANALYSIS_UNIT_ID}}"
POSTPROCESS_OUTPUT="{remote_run}/postprocessing/${{ANALYSIS_UNIT_ID}}"
mkdir -p "$POSTPROCESS_OUTPUT"

echo "Run ID: {run_id}"
echo "Analysis unit: $ANALYSIS_UNIT_ID"
echo "Session path: $SESSION_PATH"
echo "Started: $(date --iso-8601=seconds)"

{command}

echo "Completed: $(date --iso-8601=seconds)"
"""


def generate_scripts(
    config: AppConfig,
    db: Database,
    run_id: str,
) -> tuple[Path, Path]:
    runs = {r["run_id"]: r for r in db.list_runs()}
    run = runs[run_id]
    units = db.get_run_units(run_id)
    local_run = Path(run["local_run_dir"])
    remote_run = run["remote_run_dir"]

    id_cfg = config.raw["slurm"]["idtracker"]
    pp_cfg = config.raw["slurm"]["postprocess"]
    commands = config.raw["commands"]

    id_script = IDTRACKER_TEMPLATE.format(
        job_name=f"idt_{run_id[-18:]}",
        partition=id_cfg["partition"],
        time=id_cfg["time"],
        cpus=id_cfg["cpus"],
        memory=id_cfg["memory"],
        gres=id_cfg["gres"],
        unit_count=len(units),
        array_limit=id_cfg.get("array_limit", 20),
        remote_run=remote_run,
        activate_command=commands["activate_idtracker"],
        command=commands["idtracker_command"].format(toml_path='"$TOML_PATH"'),
        run_id=run_id,
    )

    pp_command = commands["postprocess_command"].format(
        session_path='"$SESSION_PATH"',
        postprocess_output='"$POSTPROCESS_OUTPUT"',
    )
    pp_script = POSTPROCESS_TEMPLATE.format(
        job_name=f"pp_{run_id[-18:]}",
        partition=pp_cfg["partition"],
        time=pp_cfg["time"],
        cpus=pp_cfg["cpus"],
        memory=pp_cfg["memory"],
        unit_count=len(units),
        array_limit=pp_cfg.get("array_limit", 20),
        remote_run=remote_run,
        activate_command=commands["activate_postprocess"],
        command=pp_command,
        run_id=run_id,
    )

    id_path = local_run / "generated" / "submit_idtracker.slurm"
    pp_path = local_run / "generated" / "submit_postprocess.slurm"
    id_path.write_text(id_script, encoding="utf-8")
    pp_path.write_text(pp_script, encoding="utf-8")
    return id_path, pp_path


def upload_scripts(
    config: AppConfig,
    db: Database,
    ssh: SSHClient,
    run_id: str,
) -> tuple[str, str]:
    runs = {r["run_id"]: r for r in db.list_runs()}
    run = runs[run_id]
    local_id, local_pp = generate_scripts(config, db, run_id)
    remote_id = f"{run['remote_run_dir']}/generated/submit_idtracker.slurm"
    remote_pp = f"{run['remote_run_dir']}/generated/submit_postprocess.slurm"
    ssh.copy_to_remote(local_id, remote_id).check()
    ssh.copy_to_remote(local_pp, remote_pp).check()
    return remote_id, remote_pp


def submit_stage(
    config: AppConfig,
    db: Database,
    ssh: SSHClient,
    run_id: str,
    stage: str,
) -> str:
    remote_id, remote_pp = upload_scripts(config, db, ssh, run_id)
    script = remote_id if stage == "idtracker" else remote_pp
    job_id = ssh.submit_sbatch(script)
    db.record_job(job_id, run_id, stage, getpass.getuser())
    if stage == "idtracker":
        db.update_run(run_id, idtracker_job_id=job_id, status="idtracker_submitted")
        for unit in db.get_run_units(run_id):
            db.update_run_unit(
                run_id, unit["analysis_unit_id"],
                idtracker_status="submitted",
            )
    else:
        db.update_run(run_id, postprocess_job_id=job_id, status="postprocess_submitted")
        for unit in db.get_run_units(run_id):
            db.update_run_unit(
                run_id, unit["analysis_unit_id"],
                postprocess_status="submitted",
            )
    return job_id
