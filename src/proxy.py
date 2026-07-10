from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Optional

from commands import CommandResult, Runner
from operations import CodexConfigManager, set_toml_string


DEFAULT_SERVICE = "cc-switch-codex-proxy.service"
DEFAULT_SERVICE_PATH = Path("/etc/systemd/system/cc-switch-codex-proxy.service")
DEFAULT_CODEX_CONFIG = Path("/root/.codex/config.toml")


@dataclass(frozen=True)
class ProxyStatus:
    service: str
    active: str
    listen_address: str
    listen_port: int
    provider_route: str


class ProxyManager:
    def __init__(
        self,
        runner: Runner,
        service_name: str = DEFAULT_SERVICE,
        service_path: Path = DEFAULT_SERVICE_PATH,
        codex_config: Path = DEFAULT_CODEX_CONFIG,
    ) -> None:
        self.runner = runner
        self.service_name = service_name
        self.service_path = Path(service_path)
        self.codex_config = Path(codex_config)

    def status(self) -> ProxyStatus:
        active_result = self.runner.run(["systemctl", "is-active", self.service_name])
        active = (active_result.stdout or active_result.stderr).strip() or "unknown"
        show_result = self.runner.run(["cc-switch", "-a", "codex", "proxy", "show"])
        show = (show_result.stdout or "").strip()
        address = self._extract_value(show, "listen_address") or self._extract_value(show, "listenAddress") or "127.0.0.1"
        port_text = self._extract_value(show, "listen_port") or self._extract_value(show, "listenPort") or "15721"
        try:
            port = int(port_text)
        except ValueError:
            port = 15721
        return ProxyStatus(
            service=self.service_name,
            active=active,
            listen_address=address,
            listen_port=port,
            provider_route="codex",
        )

    def configure(self, listen_address: str, listen_port: int, restart: bool = False) -> None:
        self.runner.run(
            [
                "cc-switch",
                "-a",
                "codex",
                "proxy",
                "config",
                "--listen-address",
                listen_address,
                "--listen-port",
                str(listen_port),
            ],
            timeout=60,
        )
        self.enable_route()
        self.write_service(listen_address, listen_port)
        self.sync_codex_base_url(listen_port)
        self.runner.run(["systemctl", "daemon-reload"], timeout=60)
        if restart:
            self.restart()

    def enable_route(self) -> CommandResult:
        return self.runner.run(["cc-switch", "-a", "codex", "proxy", "enable"], timeout=60)

    def sync_codex_base_url(self, listen_port: Optional[int] = None) -> None:
        port = listen_port if listen_port is not None else self.status().listen_port
        CodexConfigManager(self.codex_config).set_base_url(f"http://127.0.0.1:{port}/v1")

    def write_service(self, listen_address: str, listen_port: int) -> None:
        self.service_path.parent.mkdir(parents=True, exist_ok=True)
        self.service_path.write_text(
            "\n".join(
                [
                    "[Unit]",
                    "Description=cc-switch Codex proxy",
                    "After=network-online.target",
                    "",
                    "[Service]",
                    "Type=simple",
                    "ExecStart=/usr/local/bin/cc-switch -a codex proxy serve "
                    f"--listen-address {listen_address} --listen-port {listen_port}",
                    "Restart=always",
                    "RestartSec=3",
                    "",
                    "[Install]",
                    "WantedBy=multi-user.target",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def restart(self) -> CommandResult:
        self._remove_codex_takeover_from_service()
        self.enable_route()
        self.runner.run(["systemctl", "daemon-reload"], timeout=60)
        return self.runner.run(["systemctl", "restart", self.service_name], timeout=60)

    def _remove_codex_takeover_from_service(self) -> None:
        if not self.service_path.exists():
            return
        text = self.service_path.read_text(encoding="utf-8")
        updated = text.replace(" --takeover codex", "")
        if updated != text:
            self.service_path.write_text(updated, encoding="utf-8")

    def logs(self, lines: int = 100) -> str:
        result = self.runner.run(
            ["journalctl", "-u", self.service_name, "-n", str(lines), "--no-pager"],
            timeout=60,
        )
        return result.stdout or result.stderr

    def test(self, model: str, base_url: str = "http://127.0.0.1:15721/v1") -> CommandResult:
        payload = json.dumps({"model": model, "input": "只回复 pong"}, ensure_ascii=False)
        return self.runner.run(
            [
                "curl",
                "-sS",
                "--max-time",
                "30",
                "-X",
                "POST",
                f"{base_url.rstrip('/')}/responses",
                "-H",
                "Content-Type: application/json",
                "-d",
                payload,
            ],
            timeout=45,
        )

    def _extract_value(self, text: str, key: str) -> Optional[str]:
        for line in text.splitlines():
            normalized = line.strip()
            if not normalized:
                continue
            if normalized.startswith(key):
                tail = normalized[len(key) :].strip(" :=\t")
                return tail.strip('"')
        return None
