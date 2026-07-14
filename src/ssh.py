from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    def check(self) -> "CommandResult":
        if self.returncode != 0:
            raise RuntimeError(self.stderr.strip() or self.stdout.strip())
        return self


class SSHClient:
    def __init__(self, host: str):
        self.host = host

    def run(self, remote_command: str, timeout: int = 120) -> CommandResult:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", self.host, remote_command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return CommandResult(proc.returncode, proc.stdout, proc.stderr)

    def run_bash(self, script: str, timeout: int = 120) -> CommandResult:
        quoted = shlex.quote(script)
        return self.run(f"bash -lc {quoted}", timeout=timeout)

    def copy_to_remote(self, local_path: Path, remote_path: str, recursive: bool = False) -> CommandResult:
        args = ["rsync", "-a"]
        if recursive:
            args.append("--recursive")
        args += [str(local_path), f"{self.host}:{remote_path}"]
        proc = subprocess.run(args, capture_output=True, text=True)
        return CommandResult(proc.returncode, proc.stdout, proc.stderr)

    def copy_from_remote(self, remote_path: str, local_path: Path) -> CommandResult:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["rsync", "-a", f"{self.host}:{remote_path}", str(local_path)],
            capture_output=True,
            text=True,
        )
        return CommandResult(proc.returncode, proc.stdout, proc.stderr)

    def ensure_remote_dir(self, remote_dir: str) -> None:
        self.run(f"mkdir -p {shlex.quote(remote_dir)}").check()

    def submit_sbatch(self, remote_script_path: str) -> str:
        result = self.run(
            f"sbatch --parsable {shlex.quote(remote_script_path)}"
        ).check()
        job_id = result.stdout.strip().split(";")[0]
        if not job_id:
            raise RuntimeError(f"Could not parse sbatch response: {result.stdout}")
        return job_id

    def cancel_job(self, job_id: str) -> None:
        self.run(f"scancel {shlex.quote(job_id)}").check()

    def job_status(self, job_id: str) -> dict:
        sq = self.run(
            f"squeue -h -j {shlex.quote(job_id)} -o '%T|%M|%R'"
        )
        if sq.returncode == 0 and sq.stdout.strip():
            line = sq.stdout.strip().splitlines()[0]
            state, elapsed, reason = (line.split("|", 2) + ["", ""])[:3]
            return {
                "state": state.lower(),
                "source": "squeue",
                "raw": sq.stdout.strip(),
                "elapsed": elapsed,
                "reason": reason,
            }
        sa = self.run(
            "sacct -n -P -X "
            f"-j {shlex.quote(job_id)} "
            "--format=JobIDRaw,State,Elapsed,ExitCode"
        )
        raw = sa.stdout.strip()
        if raw:
            first = raw.splitlines()[0].split("|")
            return {
                "state": first[1].split()[0].lower() if len(first) > 1 else "unknown",
                "source": "sacct",
                "raw": raw,
                "elapsed": first[2] if len(first) > 2 else "",
                "reason": first[3] if len(first) > 3 else "",
            }
        return {"state": "unknown", "source": "none", "raw": sq.stderr + sa.stderr}
