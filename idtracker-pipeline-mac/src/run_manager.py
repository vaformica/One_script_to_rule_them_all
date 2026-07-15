import json
import math
import shlex
from datetime import datetime
from pathlib import Path

from .toml_editor import edit_thresholds


def _safe(value):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in value)


def _next_index(backend, root):
    index_path = f"{root.rstrip('/')}/run_index.tsv"
    command = (
        f"mkdir -p {shlex.quote(root)}; "
        f"if [[ -f {shlex.quote(index_path)} ]]; then "
        f"awk -F'\\t' 'NR>1 && $1+0>m{{m=$1+0}} END{{print m+1}}' "
        f"{shlex.quote(index_path)}; else echo 1; fi"
    )
    return int(backend.run(command).check().stdout.strip() or "1")


def _verify_script(backend, path, label):
    if not path:
        raise ValueError(f"{label} script path is not configured")
    result = backend.run(f"test -r {shlex.quote(path)}")
    if result.returncode != 0:
        raise ValueError(f"{label} script cannot be read: {path}")


def _submission_command(script, env, working_dir, dependency=""):
    exports = ",".join(
        f"{key}={str(value).replace(',', '_')}" for key, value in env.items()
    )
    dependency_arg = f" --dependency=afterok:{dependency}" if dependency else ""
    return (
        f"cd {shlex.quote(working_dir)} && "
        f"sbatch --parsable{dependency_arg} "
        f"--export=ALL,{exports} {shlex.quote(script)}"
    )


def prepare_and_submit(backend, config, row, run_idtracker, run_postprocess):
    if row.status != "Matched" or not row.video_path:
        raise ValueError("Only matched rows may be submitted")
    if None in (row.area_min, row.area_max, row.background_difference):
        raise ValueError("All threshold values are required")

    run_index = _next_index(backend, config.remote_project_root)
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    stamp = now.strftime("%Y%m%d_%H%M%S")
    video_stem = _safe(Path(row.video_filename).stem)
    cell = _safe(row.cell_label or "cell")

    run_dir = (
        f"{config.remote_project_root.rstrip('/')}/runs/"
        f"{video_stem}/{cell}/run_{run_index:05d}_{stamp}"
    )
    input_dir = f"{run_dir}/input"
    output_dir = f"{run_dir}/outputs"
    log_dir = f"{run_dir}/logs"
    backend.run(
        "mkdir -p "
        + " ".join(shlex.quote(x) for x in (input_dir, output_dir, log_dir))
    ).check()

    edited_toml = edit_thresholds(
        backend.read_text(row.toml_path),
        row.area_min,
        row.area_max,
        row.background_difference,
    )
    copied_toml = f"{input_dir}/{Path(row.toml_path).name}"
    backend.write_text(copied_toml, edited_toml)

    manifest = {
        "run_index": run_index,
        "run_timestamp": timestamp,
        "video_path": row.video_path,
        "toml_source": row.toml_path,
        "toml_copy": copied_toml,
        "cell_label": row.cell_label,
        "assay_type": row.assay_type,
        "existing_session": row.session_path,
        "thresholds": {
            "area_min": row.area_min,
            "area_max": None if math.isinf(row.area_max) else row.area_max,
            "area_max_is_infinite": bool(math.isinf(row.area_max)),
            "background_difference": row.background_difference,
        },
    }
    backend.write_text(
        f"{run_dir}/run_manifest.json",
        json.dumps(manifest, indent=2, allow_nan=False),
    )

    env = {
        "PIPELINE_RUN_INDEX": f"{run_index:05d}",
        "PIPELINE_RUN_TIMESTAMP": stamp,
        "PIPELINE_RUN_DIR": run_dir,
        "PIPELINE_INPUT_DIR": input_dir,
        "PIPELINE_OUTPUT_DIR": output_dir,
        "PIPELINE_LOG_DIR": log_dir,
        "PIPELINE_TOML": copied_toml,
        "PIPELINE_VIDEO": row.video_path,
        "PIPELINE_CELL": row.cell_label or "",
        "PIPELINE_ASSAY_TYPE": row.assay_type,
        "PIPELINE_SESSION": row.session_path or "",
    }

    jobs = {"idtracker": "", "postprocess": ""}
    commands = {}

    if run_idtracker:
        _verify_script(backend, config.idtracker_script, "IDtracker")
        command = _submission_command(
            config.idtracker_script, env, input_dir
        )
        commands["idtracker"] = command
        jobs["idtracker"] = (
            backend.run(command).check().stdout.strip().split(";")[0]
        )

    if run_postprocess:
        if row.assay_type == "Behavioral assay":
            script = config.ba_script
            label = "Behavioral-assay post-processing"
        elif row.assay_type == "Fight":
            script = config.fight_script
            label = "Fight post-processing"
        else:
            raise ValueError("Choose Behavioral assay or Fight")
        _verify_script(backend, script, label)
        command = _submission_command(
            script,
            env,
            input_dir,
            dependency=jobs["idtracker"] if run_idtracker else "",
        )
        commands["postprocess"] = command
        jobs["postprocess"] = (
            backend.run(command).check().stdout.strip().split(";")[0]
        )

    index_path = f"{config.remote_project_root.rstrip('/')}/run_index.tsv"
    header = (
        "run_index\trun_timestamp\tvideo_filename\tcell_label\tassay_type\t"
        "run_dir\tidtracker_job\tpostprocess_job\n"
    )
    line = (
        f"{run_index}\t{timestamp}\t{row.video_filename}\t{row.cell_label or ''}\t"
        f"{row.assay_type}\t{run_dir}\t{jobs['idtracker']}\t{jobs['postprocess']}\n"
    )
    command = (
        f"if [[ ! -f {shlex.quote(index_path)} ]]; then "
        f"printf %s {shlex.quote(header)} > {shlex.quote(index_path)}; fi; "
        f"printf %s {shlex.quote(line)} >> {shlex.quote(index_path)}"
    )
    backend.run(command).check()

    return {
        "run_index": run_index,
        "timestamp": timestamp,
        "run_dir": run_dir,
        "jobs": jobs,
        "commands": commands,
    }
