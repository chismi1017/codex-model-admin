from pathlib import Path
import os
import shutil
import subprocess
import unittest
import tempfile


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate-from-server.sh"
BASH = shutil.which("bash")


@unittest.skipUnless(BASH, "bash is required")
class MigrationScriptTests(unittest.TestCase):
    def test_script_has_valid_bash_syntax(self):
        result = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_help_does_not_require_root_or_source(self):
        result = subprocess.run([BASH, str(SCRIPT), "--help"], capture_output=True, text=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--source", result.stdout)
        self.assertIn("--install-dir", result.stdout)

    def test_missing_source_prints_usage(self):
        result = subprocess.run([BASH, str(SCRIPT)], capture_output=True, text=True)

        self.assertEqual(result.returncode, 2)
        self.assertIn("用法", result.stderr)

    def test_script_automatically_quiesces_source_processes(self):
        text = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("kill -STOP", text)
        self.assertIn("kill -CONT", text)
        self.assertIn("watchdog_seconds", text)
        self.assertIn("cc-switch daemon stop", text)
        self.assertIn('kill -TERM "${pids[@]}"', text)
        self.assertIn('kill -KILL "${remaining[@]}"', text)
        self.assertNotIn("请退出所有 Codex 会话后重试", text)
        self.assertNotIn("请关闭手动启动的 cc-switch 后重试", text)
        self.assertNotIn("新服务器仍有 Codex CLI 进程", text)

    @unittest.skipUnless(Path("/bin/sh").exists(), "Linux shell is required")
    def test_target_process_helper_succeeds_when_no_processes_match(self):
        helper = self._target_process_helper()
        with tempfile.TemporaryDirectory() as tmp:
            fake_bin = Path(tmp)
            self._write_executable(fake_bin / "ps", "#!/usr/bin/env bash\nexit 0\n")
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"

            result = subprocess.run(
                [BASH, "-c", f"set -Eeuo pipefail\n{helper}\nterminate_target_processes"],
                capture_output=True,
                text=True,
                env=env,
            )

        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(Path("/bin/sleep").exists(), "Linux process controls are required")
    def test_source_archive_helper_pauses_and_resumes_processes(self):
        helper = self._source_archive_helper()

        sleepers = [subprocess.Popen(["/bin/sleep", "30"]) for _ in range(2)]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                fake_bin = Path(tmp)
                self._write_executable(
                    fake_bin / "systemctl",
                    "#!/usr/bin/env bash\nexit 0\n",
                )
                self._write_executable(
                    fake_bin / "ps",
                    """#!/usr/bin/env bash
if [[ "$*" == "-eo pid=,comm=,args=" ]]; then
  printf '%s codex codex app-server\\n' "$TEST_CODEX_PID"
  printf '%s cc-switch cc-switch daemon start --detach\\n' "$TEST_SWITCH_PID"
else
  exec /bin/ps "$@"
fi
""",
                )
                self._write_executable(
                    fake_bin / "tar",
                    "#!/usr/bin/env bash\nprintf 'test-archive'\n",
                )
                env = os.environ.copy()
                env["PATH"] = f"{fake_bin}:{env['PATH']}"
                env["TEST_CODEX_PID"] = str(sleepers[0].pid)
                env["TEST_SWITCH_PID"] = str(sleepers[1].pid)

                result = subprocess.run(
                    [BASH, "-s", "--", "cc-switch-codex-proxy", "30"],
                    input=helper,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=10,
                )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "test-archive")
            self.assertIn("已临时暂停 2 个源服务器进程", result.stderr)
            for sleeper in sleepers:
                state = subprocess.run(
                    ["/bin/ps", "-o", "stat=", "-p", str(sleeper.pid)],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
                self.assertNotIn("T", state)
                self.assertIsNone(sleeper.poll())
        finally:
            for sleeper in sleepers:
                if sleeper.poll() is None:
                    sleeper.terminate()
            for sleeper in sleepers:
                try:
                    sleeper.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    sleeper.kill()
                    sleeper.wait(timeout=2)

    @unittest.skipUnless(Path("/bin/sleep").exists(), "Linux process controls are required")
    def test_watchdog_resumes_process_after_archive_shell_is_killed(self):
        helper = self._source_archive_helper()
        sleeper = subprocess.Popen(["/bin/sleep", "30"])
        try:
            with tempfile.TemporaryDirectory() as tmp:
                fake_bin = Path(tmp)
                self._write_executable(fake_bin / "systemctl", "#!/usr/bin/env bash\nexit 0\n")
                self._write_executable(
                    fake_bin / "ps",
                    """#!/usr/bin/env bash
if [[ "$*" == "-eo pid=,comm=,args=" ]]; then
  printf '%s codex codex app-server\\n' "$TEST_CODEX_PID"
else
  exec /bin/ps "$@"
fi
""",
                )
                self._write_executable(
                    fake_bin / "tar",
                    "#!/usr/bin/env bash\nkill -KILL \"$PPID\"\nexit 1\n",
                )
                env = os.environ.copy()
                env["PATH"] = f"{fake_bin}:{env['PATH']}"
                env["TEST_CODEX_PID"] = str(sleeper.pid)

                result = subprocess.run(
                    [BASH, "-s", "--", "cc-switch-codex-proxy", "10"],
                    input=helper,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=10,
                )

            self.assertNotEqual(result.returncode, 0)
            for _ in range(30):
                state = subprocess.run(
                    ["/bin/ps", "-o", "stat=", "-p", str(sleeper.pid)],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
                if "T" not in state:
                    break
                subprocess.run(["/bin/sleep", "0.1"], check=True)
            self.assertNotIn("T", state)
            self.assertIsNone(sleeper.poll())
        finally:
            if sleeper.poll() is None:
                sleeper.terminate()
                try:
                    sleeper.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    sleeper.kill()
                    sleeper.wait(timeout=2)

    @unittest.skipUnless(Path("/bin/sleep").exists(), "Linux process controls are required")
    def test_watchdog_timeout_invalidates_archive_and_resumes_process(self):
        helper = self._source_archive_helper()
        sleeper = subprocess.Popen(["/bin/sleep", "30"])
        try:
            with tempfile.TemporaryDirectory() as tmp:
                fake_bin = Path(tmp)
                self._write_executable(fake_bin / "systemctl", "#!/usr/bin/env bash\nexit 0\n")
                self._write_executable(
                    fake_bin / "ps",
                    """#!/usr/bin/env bash
if [[ "$*" == "-eo pid=,comm=,args=" ]]; then
  printf '%s codex codex app-server\\n' "$TEST_CODEX_PID"
else
  exec /bin/ps "$@"
fi
""",
                )
                self._write_executable(
                    fake_bin / "tar",
                    "#!/usr/bin/env bash\n/bin/sleep 3\nprintf 'late-archive'\n",
                )
                env = os.environ.copy()
                env["PATH"] = f"{fake_bin}:{env['PATH']}"
                env["TEST_CODEX_PID"] = str(sleeper.pid)

                result = subprocess.run(
                    [BASH, "-s", "--", "cc-switch-codex-proxy", "1"],
                    input=helper,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=10,
                )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("watchdog 已超时，本次归档作废", result.stderr)
            state = subprocess.run(
                ["/bin/ps", "-o", "stat=", "-p", str(sleeper.pid)],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertNotIn("T", state)
            self.assertIsNone(sleeper.poll())
        finally:
            if sleeper.poll() is None:
                sleeper.terminate()
                try:
                    sleeper.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    sleeper.kill()
                    sleeper.wait(timeout=2)

    @staticmethod
    def _source_archive_helper() -> str:
        text = SCRIPT.read_text(encoding="utf-8")
        marker = 'if ! ssh_source bash -s -- "$SERVICE" "$SOURCE_WATCHDOG_SECONDS" >"$SOURCE_ARCHIVE" <<\'REMOTE\'\n'
        start = text.index(marker) + len(marker)
        return text[start : text.index("\nREMOTE\n  then", start)]

    @staticmethod
    def _target_process_helper() -> str:
        text = SCRIPT.read_text(encoding="utf-8")
        start = text.index("matching_target_pids() {")
        return text[start : text.index("\nstop_target_environment() {", start)]

    @staticmethod
    def _write_executable(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
