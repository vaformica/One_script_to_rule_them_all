from dataclasses import dataclass, asdict
from pathlib import Path
import json


@dataclass
class Config:
    ssh_host: str
    identity_file: str
    remote_search_root: str
    remote_project_root: str
    idtracker_script: str
    ba_script: str
    fight_script: str
    max_concurrent: int

    @classmethod
    def load(cls, path):
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path):
        Path(path).write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
