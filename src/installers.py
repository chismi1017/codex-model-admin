from __future__ import annotations

from commands import Runner
from preflight import PreflightManager


CC_SWITCH_URL = "https://github.com/saladday/cc-switch-cli/releases/latest/download/cc-switch-cli-linux-x64-musl.tar.gz"


class InstallerManager:
    def __init__(self, runner: Runner) -> None:
        self.runner = runner
        self.preflight = PreflightManager(runner)

    def install_codex(self, confirm: bool) -> str:
        if self.runner.which("codex"):
            return "already-installed"
        blockers = self.preflight.install_blockers("codex")
        if blockers:
            return "blocked: " + "; ".join(blockers)
        if not confirm:
            return "confirmation-required"

        result = self.runner.run(["npm", "install", "-g", "@openai/codex"], timeout=300)
        if result.returncode != 0:
            return "failed: " + (result.stderr or result.stdout).strip()
        verify = self.runner.run(["codex", "--version"])
        if verify.returncode != 0:
            return "failed: codex --version did not succeed"
        return "installed"

    def install_cc_switch(self, confirm: bool) -> str:
        if self.runner.which("cc-switch"):
            return "already-installed"
        blockers = self.preflight.install_blockers("cc-switch")
        if blockers:
            return "blocked: " + "; ".join(blockers)
        if not confirm:
            return "confirmation-required"

        steps = [
            [
                "curl",
                "-L",
                "-o",
                "/tmp/cc-switch-cli.tar.gz",
                CC_SWITCH_URL,
            ],
            ["tar", "-xzf", "/tmp/cc-switch-cli.tar.gz", "-C", "/tmp"],
            ["install", "-m", "0755", "/tmp/cc-switch", "/usr/local/bin/cc-switch"],
        ]
        for step in steps:
            result = self.runner.run(step, timeout=300)
            if result.returncode != 0:
                return "failed: " + (result.stderr or result.stdout).strip()

        verify = self.runner.run(["cc-switch", "--version"])
        if verify.returncode != 0:
            return "failed: cc-switch --version did not succeed"
        return "installed"
