from __future__ import annotations

import getpass
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
import shlex

from .config import AppConfig
from .database import Database, utcnow
from .ssh import SSHClient
from .toml_editor import edit_thresholds


def slugify(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return re.sub(r"_+", "_", value).strip("_") or "run"


def make_run_id(label: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"RUN_{stamp}_{slugify(label)}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def create_run(
    config: AppConfig,
    db: Database,
    ssh: SSHClient,
    selected_units: list[dict[str, Any]],
    settings: dict[str, Any],
) -> str:
    if not selected_units:
        raise ValueError("No analysis units selected.")

    run_id = make_run_id(settings.get("run_label", "analysis"))
    local_run = config.local_project_root / "runs" / run_id
    remote_run = f"{config.remote_project_root.rstrip('/')}/runs/{run_id}"

    for sub in (
        "input_snapshot/tomls",
        "generated",
        "quick_results/tracks",
        "quick_results/individual_summaries",
        "logs",
    ):
        (local_run / sub).mkdir(parents=True, exist_ok=True)

    ssh.ensure_remote_dir(remote_run)
    ssh.ensure_remote_dir(f"{remote_run}/input_snapshot/tomls")
    ssh.ensure_remote_dir(f"{remote_run}/generated")
    ssh.ensure_remote_dir(f"{remote_run}/logs")
    ssh.ensure_remote_dir(f"{remote_run}/idtracker")
    ssh.ensure_remote_dir(f"{remote_run}/postprocessing")

    unit_records = []
    manifest_units = []

    for ordinal, unit in enumerate(selected_units, start=1):
        remote_source = unit["toml_path"]
        read_cmd = f"cat {shlex.quote(remote_source)}"
        source_text = ssh.run(read_cmd, timeout=60).check().stdout

        edited_text = edit_thresholds(
            source_text,
            area_min=settings.get("area_min"),
            area_max=settings.get("area_max"),
            background_difference_threshold=settings.get("background_difference_threshold"),
        )

        output_name = f"{ordinal:04d}_{slugify(unit['analysis_unit_id'])}.toml"
        local_toml = local_run / "input_snapshot" / "tomls" / output_name
        local_toml.write_text(edited_text, encoding="utf-8")
        remote_toml = f"{remote_run}/input_snapshot/tomls/{output_name}"
        ssh.copy_to_remote(local_toml, remote_toml).check()

        unit_records.append({
            "analysis_unit_id": unit["analysis_unit_id"],
            "ordinal": ordinal,
            "run_toml_local_path": str(local_toml),
            "run_toml_remote_path": remote_toml,
            "idtracker_status": "not_submitted",
            "postprocess_status": "blocked" if settings["mode"] != "postprocess_only" else "not_submitted",
        })

        manifest_units.append({
            "ordinal": ordinal,
            "analysis_unit_id": unit["analysis_unit_id"],
            "video_path": unit["video_path"],
            "master_toml_path": unit["toml_path"],
            "run_toml_path": remote_toml,
            "run_toml_sha256": sha256_text(edited_text),
            "cell_label": unit["cell_label"],
            "assay_type": unit["assay_type"],
            "animal_count": unit.get("animal_count"),
            "roi_count": unit.get("roi_count"),
        })

    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": utcnow(),
        "created_by": getpass.getuser(),
        "mode": settings["mode"],
        "run_label": settings.get("run_label", ""),
        "notes": settings.get("notes", ""),
        "settings": settings,
        "units": manifest_units,
    }

    manifest_path = local_run / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    ssh.copy_to_remote(
        manifest_path,
        f"{remote_run}/run_manifest.json",
    ).check()

    settings_path = local_run / "run_settings.json"
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    ssh.copy_to_remote(
        settings_path,
        f"{remote_run}/run_settings.json",
    ).check()

    run_record = {
        "run_id": run_id,
        "created_at": manifest["created_at"],
        "created_by": manifest["created_by"],
        "run_label": settings.get("run_label", ""),
        "mode": settings["mode"],
        "assay_profile": settings.get("assay_profile", "auto"),
        "status": "created",
        "local_run_dir": str(local_run),
        "remote_run_dir": remote_run,
        "settings_json": json.dumps(settings, sort_keys=True),
        "notes": settings.get("notes", ""),
    }
    db.create_run(run_record, unit_records)
    return run_id
