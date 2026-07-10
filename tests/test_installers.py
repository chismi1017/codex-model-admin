import unittest

from commands import CommandResult, FakeRunner
from installers import InstallerManager


class InstallerManagerTests(unittest.TestCase):
    def test_does_not_install_existing_codex(self):
        runner = FakeRunner(which_map={"codex": "/usr/bin/codex", "npm": "/usr/bin/npm"})

        result = InstallerManager(runner).install_codex(confirm=True)

        self.assertEqual(result, "already-installed")
        self.assertEqual(runner.calls, [])

    def test_refuses_codex_install_without_confirmation(self):
        runner = FakeRunner(which_map={"npm": "/usr/bin/npm"})

        result = InstallerManager(runner).install_codex(confirm=False)

        self.assertEqual(result, "confirmation-required")
        self.assertEqual(runner.calls, [])

    def test_runs_codex_install_when_missing_and_confirmed(self):
        runner = FakeRunner(
            which_map={"npm": "/usr/bin/npm"},
            run_map={
                ("npm", "install", "-g", "@openai/codex"): CommandResult(0, "installed\n", ""),
                ("codex", "--version"): CommandResult(0, "codex-cli 0.142.5\n", ""),
            },
        )

        result = InstallerManager(runner).install_codex(confirm=True)

        self.assertEqual(result, "installed")
        self.assertEqual(runner.calls[0], ["npm", "install", "-g", "@openai/codex"])

    def test_refuses_cc_switch_when_tar_missing(self):
        runner = FakeRunner(which_map={"curl": "/usr/bin/curl"})

        result = InstallerManager(runner).install_cc_switch(confirm=True)

        self.assertEqual(result, "blocked: tar is required to install cc-switch CLI")

    def test_runs_cc_switch_download_extract_install(self):
        runner = FakeRunner(
            which_map={"curl": "/usr/bin/curl", "tar": "/usr/bin/tar"},
            run_map={
                (
                    "curl",
                    "-L",
                    "-o",
                    "/tmp/cc-switch-cli.tar.gz",
                    "https://github.com/saladday/cc-switch-cli/releases/latest/download/cc-switch-cli-linux-x64-musl.tar.gz",
                ): CommandResult(0, "", ""),
                ("tar", "-xzf", "/tmp/cc-switch-cli.tar.gz", "-C", "/tmp"): CommandResult(0, "", ""),
                ("install", "-m", "0755", "/tmp/cc-switch", "/usr/local/bin/cc-switch"): CommandResult(0, "", ""),
                ("cc-switch", "--version"): CommandResult(0, "cc-switch 5.8.7\n", ""),
            },
        )

        result = InstallerManager(runner).install_cc_switch(confirm=True)

        self.assertEqual(result, "installed")
        self.assertEqual(len(runner.calls), 4)


if __name__ == "__main__":
    unittest.main()
