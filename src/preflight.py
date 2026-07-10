from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from commands import Runner


@dataclass(frozen=True)
class CheckResult:
    name: str
    required: bool
    installed: bool
    path: Optional[str]
    version: str
    minimum_version: Optional[str]
    status: str


class PreflightManager:
    TOOL_SPECS = [
        ("codex", True, "0.142.5", ["codex", "--version"]),
        ("cc-switch", True, "5.8.7", ["cc-switch", "--version"]),
        ("node", False, None, ["node", "--version"]),
        ("npm", False, None, ["npm", "--version"]),
        ("python3", True, "3.9", ["python3", "--version"]),
        ("systemd", True, None, None),
        ("curl", True, None, None),
        ("tar", True, None, None),
        ("ss", False, None, None),
    ]

    COMMAND_NAMES = {
        "systemd": "systemctl",
    }

    def __init__(self, runner: Runner) -> None:
        self.runner = runner

    def check_all(self) -> List[CheckResult]:
        return [
            self.check(name, required, minimum, version_argv)
            for name, required, minimum, version_argv in self.TOOL_SPECS
        ]

    def check(
        self,
        name: str,
        required: bool,
        minimum_version: Optional[str],
        version_argv: Optional[List[str]],
    ) -> CheckResult:
        command_name = self.COMMAND_NAMES.get(name, name)
        path = self.runner.which(command_name)
        if not path:
            return CheckResult(
                name=name,
                required=required,
                installed=False,
                path=None,
                version="missing",
                minimum_version=minimum_version,
                status="missing",
            )

        version = "available"
        if version_argv:
            result = self.runner.run(version_argv)
            version = (result.stdout or result.stderr).strip() or "available"

        return CheckResult(
            name=name,
            required=required,
            installed=True,
            path=path,
            version=version,
            minimum_version=minimum_version,
            status="ok",
        )

    def install_blockers(self, component: str) -> List[str]:
        if component == "codex":
            if not self.runner.which("npm"):
                return ["npm is required to install Codex CLI"]
            return []
        if component == "cc-switch":
            blockers = []
            if not self.runner.which("curl"):
                blockers.append("curl is required to install cc-switch CLI")
            if not self.runner.which("tar"):
                blockers.append("tar is required to install cc-switch CLI")
            return blockers
        return [f"unknown component: {component}"]

    def by_name(self) -> Dict[str, CheckResult]:
        return {item.name: item for item in self.check_all()}
