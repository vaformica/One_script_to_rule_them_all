from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import os

import tomlkit


@dataclass(frozen=True)
class AppConfig:
    raw: dict[str, Any]
    config_path: Path

    @property
    def local_project_root(self) -> Path:
        value = self.raw["application"]["local_project_root"]
        return Path(os.path.expanduser(value)).resolve()

    @property
    def database_path(self) -> Path:
        filename = self.raw["application"].get("database_filename", "pipeline.sqlite3")
        return self.local_project_root / "catalog" / filename

    @property
    def ssh_host(self) -> str:
        return str(self.raw["firebird"]["ssh_host"])

    @property
    def remote_project_root(self) -> str:
        return str(self.raw["firebird"]["remote_project_root"])

    @property
    def remote_video_roots(self) -> list[str]:
        return list(self.raw["firebird"].get("remote_video_roots", []))

    @property
    def remote_toml_roots(self) -> list[str]:
        return list(self.raw["firebird"].get("remote_toml_roots", []))


def load_config(path: str | Path = "config/config.toml") -> AppConfig:
    path = Path(path)
    if not path.exists():
        example = path.with_name("config.example.toml")
        raise FileNotFoundError(
            f"Missing {path}. Copy {example} to {path} and edit it."
        )
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    raw = _to_builtin(doc)
    return AppConfig(raw=raw, config_path=path.resolve())


def _to_builtin(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _to_builtin(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_builtin(v) for v in value]
    return value


def ensure_local_directories(config: AppConfig) -> None:
    for name in ("catalog", "runs", "retrieved_results", "exports", "logs"):
        (config.local_project_root / name).mkdir(parents=True, exist_ok=True)
