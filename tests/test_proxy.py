import tempfile
import unittest
from pathlib import Path

from commands import CommandResult, FakeRunner
from proxy import ProxyManager


class ProxyManagerTests(unittest.TestCase):
    def test_status_parses_proxy_show_output(self):
        runner = FakeRunner(
            run_map={
                ("systemctl", "is-active", "cc-switch-codex-proxy.service"): CommandResult(0, "active\n", ""),
                ("cc-switch", "-a", "codex", "proxy", "show"): CommandResult(
                    0,
                    "listen_address: 0.0.0.0\nlisten_port: 18080\n",
                    "",
                ),
            }
        )

        status = ProxyManager(runner).status()

        self.assertEqual(status.active, "active")
        self.assertEqual(status.listen_address, "0.0.0.0")
        self.assertEqual(status.listen_port, 18080)

    def test_configure_writes_service_and_codex_base_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = root / "cc-switch-codex-proxy.service"
            config = root / "config.toml"
            config.write_text('base_url = "http://old/v1"\n', encoding="utf-8")
            runner = FakeRunner()

            ProxyManager(runner, service_path=service, codex_config=config).configure("0.0.0.0", 18080)

            service_text = service.read_text(encoding="utf-8")
            self.assertIn("--listen-address 0.0.0.0 --listen-port 18080", service_text)
            self.assertNotIn("--takeover codex", service_text)
            self.assertIn('base_url = "http://127.0.0.1:18080/v1"', config.read_text(encoding="utf-8"))
            self.assertIn(["cc-switch", "-a", "codex", "proxy", "enable"], runner.calls)
            self.assertIn(["systemctl", "daemon-reload"], runner.calls)

    def test_restart_runs_systemctl(self):
        runner = FakeRunner()

        ProxyManager(runner).restart()

        self.assertIn(["cc-switch", "-a", "codex", "proxy", "enable"], runner.calls)
        self.assertIn(["systemctl", "daemon-reload"], runner.calls)
        self.assertIn(["systemctl", "restart", "cc-switch-codex-proxy.service"], runner.calls)

    def test_restart_removes_legacy_codex_takeover_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = Path(tmp) / "cc-switch-codex-proxy.service"
            service.write_text(
                "ExecStart=/usr/local/bin/cc-switch -a codex proxy serve "
                "--listen-address 127.0.0.1 --listen-port 15721 --takeover codex\n",
                encoding="utf-8",
            )
            runner = FakeRunner()

            ProxyManager(runner, service_path=service).restart()

            self.assertNotIn("--takeover codex", service.read_text(encoding="utf-8"))
            self.assertIn(["cc-switch", "-a", "codex", "proxy", "enable"], runner.calls)
            self.assertIn(["systemctl", "restart", "cc-switch-codex-proxy.service"], runner.calls)


if __name__ == "__main__":
    unittest.main()
