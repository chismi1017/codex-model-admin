import unittest
from unittest import mock

from commands import CommandResult, FakeRunner, Runner
from preflight import PreflightManager
from rendering import render_doctor


class CommandBoundaryTests(unittest.TestCase):
    def test_fake_runner_returns_configured_which_result(self):
        runner = FakeRunner(which_map={"codex": "/usr/bin/codex"})

        self.assertEqual(runner.which("codex"), "/usr/bin/codex")
        self.assertIsNone(runner.which("missing"))

    def test_fake_runner_records_run_calls(self):
        runner = FakeRunner(
            run_map={
                ("codex", "--version"): CommandResult(
                    returncode=0,
                    stdout="codex-cli 0.142.5\n",
                    stderr="",
                )
            }
        )

        result = runner.run(["codex", "--version"])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "codex-cli 0.142.5\n")
        self.assertEqual(runner.calls, [["codex", "--version"]])

    @mock.patch("commands.subprocess.run", side_effect=PermissionError("denied"))
    def test_runner_returns_failure_when_process_cannot_start(self, mocked_run):
        result = Runner().run(["codex", "--version"])

        self.assertEqual(result.returncode, 126)
        self.assertEqual(result.stdout, "")
        self.assertIn("denied", result.stderr)
        mocked_run.assert_called_once()


class PreflightManagerTests(unittest.TestCase):
    def test_detects_installed_codex_and_cc_switch(self):
        runner = FakeRunner(
            which_map={
                "codex": "/usr/bin/codex",
                "cc-switch": "/usr/local/bin/cc-switch",
                "node": "/usr/bin/node",
                "npm": "/usr/bin/npm",
                "python3": "/usr/bin/python3",
                "systemctl": "/usr/bin/systemctl",
                "curl": "/usr/bin/curl",
                "tar": "/usr/bin/tar",
                "ss": "/usr/sbin/ss",
            },
            run_map={
                ("codex", "--version"): CommandResult(0, "codex-cli 0.142.5\n", ""),
                ("cc-switch", "--version"): CommandResult(0, "cc-switch 5.8.7\n", ""),
                ("node", "--version"): CommandResult(0, "v20.20.2\n", ""),
                ("npm", "--version"): CommandResult(0, "11.18.0\n", ""),
                ("python3", "--version"): CommandResult(0, "Python 3.9.18\n", ""),
            },
        )

        results = PreflightManager(runner).check_all()
        by_name = {item.name: item for item in results}

        self.assertEqual(by_name["codex"].status, "ok")
        self.assertEqual(by_name["codex"].path, "/usr/bin/codex")
        self.assertEqual(by_name["cc-switch"].version, "cc-switch 5.8.7")
        self.assertEqual(by_name["systemd"].version, "available")

    def test_marks_missing_codex_as_missing(self):
        runner = FakeRunner(which_map={"npm": "/usr/bin/npm"})

        results = PreflightManager(runner).check_all()
        by_name = {item.name: item for item in results}

        self.assertEqual(by_name["codex"].status, "missing")
        self.assertFalse(by_name["codex"].installed)

    def test_codex_install_blocked_when_npm_missing(self):
        runner = FakeRunner(which_map={})

        blockers = PreflightManager(runner).install_blockers("codex")

        self.assertEqual(blockers, ["npm is required to install Codex CLI"])

    def test_cc_switch_install_blocked_when_curl_or_tar_missing(self):
        runner = FakeRunner(which_map={"curl": "/usr/bin/curl"})

        blockers = PreflightManager(runner).install_blockers("cc-switch")

        self.assertEqual(blockers, ["tar is required to install cc-switch CLI"])


class DoctorRenderingTests(unittest.TestCase):
    def test_render_doctor_uses_chinese_labels_and_status_icons(self):
        runner = FakeRunner(
            which_map={"codex": "/usr/bin/codex"},
            run_map={
                ("codex", "--version"): CommandResult(0, "codex-cli 0.142.5\n", ""),
            },
        )
        results = PreflightManager(runner).check_all()

        text = render_doctor(results)

        self.assertIn("环境检查 / 安装", text)
        self.assertIn("✅  codex", text)
        self.assertIn("❌  cc-switch", text)
        self.assertIn("路径", text)
        self.assertIn("版本", text)


if __name__ == "__main__":
    unittest.main()
