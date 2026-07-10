from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "uninstall-codex-model-admin.sh"
BASH = shutil.which("bash")
LINUX_ROOT = os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0 and Path("/proc").is_dir()


@unittest.skipUnless(BASH, "bash is required")
class UninstallScriptTests(unittest.TestCase):
    def test_script_has_valid_bash_syntax(self):
        result = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_help_documents_safety_options(self):
        result = subprocess.run([BASH, str(SCRIPT), "--help"], capture_output=True, text=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--dry-run", result.stdout)
        self.assertIn("--purge-npmrc", result.stdout)
        self.assertIn("--project-dir", result.stdout)
        self.assertIn("不会卸载 Node.js", result.stdout)

    def test_script_limits_destructive_paths_and_preserves_shared_dependencies(self):
        text = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("assert_removable_path", text)
        self.assertIn("/root/.codex", text)
        self.assertIn("/root/.cc-switch", text)
        self.assertIn("/usr/local/bin/cc-switch", text)
        self.assertIn("@openai/codex", text)
        self.assertIn("PURGE_NPMRC", text)
        self.assertNotIn("dnf remove", text)
        self.assertNotIn("yum remove", text)
        self.assertNotIn("apt-get remove", text)

    @unittest.skipUnless(LINUX_ROOT, "Linux root is required for dry-run integration")
    def test_dry_run_does_not_delete_project_or_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "codex-model-admin"
            (project / "src").mkdir(parents=True)
            (project / "scripts").mkdir()
            (project / "src" / "cli.py").write_text("# marker\n", encoding="utf-8")
            (project / "scripts" / "install-codex-model-admin.sh").write_text("# marker\n", encoding="utf-8")
            marker = project / "must-survive"
            marker.write_text("safe\n", encoding="utf-8")

            result = subprocess.run(
                [BASH, str(SCRIPT), "--dry-run", "--yes", "--project-dir", str(project)],
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(marker.exists())
            self.assertIn("[DRY-RUN]", result.stdout)
            self.assertIn("未修改任何文件、服务或进程", result.stdout)
            self.assertIn("保留 /root/.npmrc", result.stdout)

    @unittest.skipUnless(LINUX_ROOT, "Linux root is required for validation integration")
    def test_rejects_unsafe_project_directory_name(self):
        result = subprocess.run(
            [BASH, str(SCRIPT), "--dry-run", "--yes", "--project-dir", "/opt"],
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("目录名称必须是 codex-model-admin", result.stderr)

    @unittest.skipUnless(LINUX_ROOT, "Linux root is required for confirmation integration")
    def test_wrong_confirmation_cancels_without_deleting_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "codex-model-admin"
            (project / "src").mkdir(parents=True)
            (project / "scripts").mkdir()
            (project / "src" / "cli.py").write_text("# marker\n", encoding="utf-8")
            (project / "scripts" / "install-codex-model-admin.sh").write_text("# marker\n", encoding="utf-8")

            result = subprocess.run(
                [BASH, str(SCRIPT), "--project-dir", str(project)],
                input="NO\n",
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(project.exists())
            self.assertIn("已取消", result.stdout)


if __name__ == "__main__":
    unittest.main()
