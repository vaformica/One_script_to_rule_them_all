from dataclasses import dataclass
from pathlib import Path
import shlex
import subprocess


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str

    def check(self):
        if self.returncode != 0:
            raise RuntimeError(self.stderr.strip() or self.stdout.strip())
        return self


class SSHBackend:
    def __init__(self, host: str, identity_file: str = ""):
        self.host = host
        self.identity_file = str(Path(identity_file).expanduser()) if identity_file else ""

    def _base(self):
        command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
        if self.identity_file:
            command += ["-o", "IdentitiesOnly=yes", "-i", self.identity_file]
        command.append(self.host)
        return command

    def run(self, command: str, timeout: int = 300):
        process = subprocess.run(
            self._base() + ["bash", "-lc", shlex.quote(command)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return Result(process.returncode, process.stdout, process.stderr)

    def read_text(self, remote_path: str):
        return self.run(f"cat -- {shlex.quote(remote_path)}").check().stdout

    def write_text(self, remote_path: str, text: str):
        parent = str(Path(remote_path).parent)
        remote_command = (
            f"mkdir -p -- {shlex.quote(parent)} && "
            f"cat > {shlex.quote(remote_path)}"
        )
        process = subprocess.run(
            self._base() + ["bash", "-lc", shlex.quote(remote_command)],
            input=text,
            capture_output=True,
            text=True,
            timeout=300,
        )
        Result(process.returncode, process.stdout, process.stderr).check()

    def test(self):
        return self.run("hostname && whoami && pwd").check().stdout
