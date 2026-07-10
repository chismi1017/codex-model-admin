from __future__ import annotations

from dataclasses import dataclass
import shutil
import subprocess
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class Runner:
    def which(self, name: str) -> Optional[str]:
        return shutil.which(name)

    def run(self, argv: Sequence[str], timeout: int = 30) -> CommandResult:
        try:
            completed = subprocess.run(
                list(argv),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
        except OSError as exc:
            return CommandResult(returncode=126, stdout="", stderr=str(exc))
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


class FakeRunner(Runner):
    def __init__(
        self,
        which_map: Optional[Mapping[str, str]] = None,
        run_map: Optional[Mapping[Tuple[str, ...], CommandResult]] = None,
    ) -> None:
        self.which_map: Dict[str, str] = dict(which_map or {})
        self.run_map: Dict[Tuple[str, ...], CommandResult] = dict(run_map or {})
        self.calls: List[List[str]] = []

    def which(self, name: str) -> Optional[str]:
        return self.which_map.get(name)

    def run(self, argv: Sequence[str], timeout: int = 30) -> CommandResult:
        del timeout
        call = list(argv)
        self.calls.append(call)
        return self.run_map.get(
            tuple(call),
            CommandResult(returncode=127, stdout="", stderr="command not configured"),
        )
