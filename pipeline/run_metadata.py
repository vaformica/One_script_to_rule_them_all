from __future__ import annotations
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any
import json


@dataclass
class RunMetadata:
    run_index: int
    run_timestamp: str
    analysis_type: str
    video_path: str
    video_filename: str
    toml_source_path: str
    toml_run_copy_path: str
    cell_label: str
    remote_run_dir: str
    session_path: str = ""
    attempt_index: int = 0
    record_id: str = ""
    idtracker_job_id: str = ""
    postprocess_job_id: str = ""
    collector_job_id: str = ""
    idtracker_started_at: str = ""
    idtracker_completed_at: str = ""
    postprocess_started_at: str = ""
    postprocess_completed_at: str = ""
    collector_completed_at: str = ""
    code_version: str = ""
    git_commit: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, path: str | Path) -> "RunMetadata":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if "attempt_index" not in data:
            data["attempt_index"] = int(data.get("run_index", 1))
        if "run_index" not in data:
            data["run_index"] = int(data.get("attempt_index", 1))
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), indent=2, allow_nan=False),
            encoding="utf-8",
        )

    def identifier(self) -> str:
        if self.record_id:
            return self.record_id
        stamp = "".join(c for c in self.run_timestamp if c.isdigit())
        video = Path(self.video_filename).stem
        safe = lambda x: "".join(c if c.isalnum() or c in "-_" else "_" for c in str(x))
        return f"{safe(video)}_{safe(self.cell_label)}_A{(self.attempt_index or self.run_index):05d}_{stamp}"

    def csv_columns(self) -> dict[str, Any]:
        return {
            "pipeline_record_id": self.identifier(),
            "pipeline_attempt_index": self.attempt_index or self.run_index,
            "pipeline_run_index": self.run_index,
            "pipeline_run_timestamp": self.run_timestamp,
            "pipeline_analysis_type": self.analysis_type,
            "pipeline_video_filename": self.video_filename,
            "pipeline_video_path": self.video_path,
            "pipeline_toml_source_path": self.toml_source_path,
            "pipeline_toml_run_copy_path": self.toml_run_copy_path,
            "pipeline_cell_label": self.cell_label,
            "pipeline_remote_run_dir": self.remote_run_dir,
            "pipeline_session_path": self.session_path,
            "pipeline_idtracker_job_id": self.idtracker_job_id,
            "pipeline_postprocess_job_id": self.postprocess_job_id,
            "pipeline_collector_job_id": self.collector_job_id,
            "pipeline_code_version": self.code_version,
            "pipeline_git_commit": self.git_commit,
        }

    def camera_label(self) -> str:
        stem = Path(self.video_filename).stem
        parts = stem.split("_")
        if len(parts) >= 2 and parts[0].lower() == "camera":
            return f"Camera {parts[1]}"
        return "Camera unknown"

    def png_label_lines(self) -> list[str]:
        return [
            f"{self.camera_label()}  |  Cell {self.cell_label}  |  {self.analysis_type.upper()}",
            f"Attempt {(self.attempt_index or self.run_index):05d}  |  Date run: {self.run_timestamp}",
            f"Video: {self.video_filename}",
            f"Record ID: {self.identifier()}",
        ]

    def png_label(self) -> str:
        return " | ".join(self.png_label_lines())
