from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import glob
import hashlib
import json
from pathlib import Path
import shutil
from typing import Iterable, List, Optional


DEFAULT_BACKUP_ROOT = Path("/root/.codex/model-admin-backups")
DEFAULT_SOURCES = [
    Path("/root/.cc-switch/cc-switch.db"),
    Path("/root/.codex/config.toml"),
    Path("/root/.codex/cc-switch-model-catalog.json"),
    Path("/etc/systemd/system/cc-switch-codex-proxy.service"),
]


@dataclass(frozen=True)
class BackupFile:
    source: str
    backup: str
    exists: bool
    sha256: Optional[str]
    size: Optional[int]


@dataclass(frozen=True)
class BackupManifest:
    backup_id: str
    created_at: str
    reason: str
    backup_root: str
    files: List[BackupFile]


class BackupManager:
    def __init__(
        self,
        backup_root: Path = DEFAULT_BACKUP_ROOT,
        sources: Optional[Iterable[Path]] = None,
    ) -> None:
        self.backup_root = Path(backup_root)
        self.sources = [Path(source) for source in sources] if sources is not None else self.default_sources()

    @staticmethod
    def default_sources() -> List[Path]:
        sources = list(DEFAULT_SOURCES)
        for item in glob.glob("/root/.cc-switch/*-provider.json"):
            path = Path(item)
            if path not in sources:
                sources.append(path)
        for item in glob.glob("/root/.codex/*.config.toml"):
            path = Path(item)
            if path not in sources:
                sources.append(path)
        return sources

    def create(self, reason: str) -> BackupManifest:
        backup_id = self._next_backup_id()
        backup_dir = self.backup_root / backup_id
        files_dir = backup_dir / "files"
        files_dir.mkdir(parents=True)

        entries: List[BackupFile] = []
        for source in sorted(set(self.sources), key=lambda item: str(item)):
            backup_path = files_dir / self._relative_source(source)
            exists = source.exists() and source.is_file()
            digest = None
            size = None
            if exists:
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, backup_path)
                digest = self._sha256(source)
                size = source.stat().st_size
            entries.append(
                BackupFile(
                    source=str(source),
                    backup=str(backup_path.relative_to(backup_dir)),
                    exists=exists,
                    sha256=digest,
                    size=size,
                )
            )

        manifest = BackupManifest(
            backup_id=backup_id,
            created_at=datetime.now().astimezone().isoformat(),
            reason=reason,
            backup_root=str(backup_dir),
            files=entries,
        )
        self._write_manifest(backup_dir, manifest)
        return manifest

    def _next_backup_id(self) -> str:
        base_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        if not (self.backup_root / base_id).exists():
            return base_id
        for index in range(1, 1000):
            candidate = f"{base_id}-{index:03d}"
            if not (self.backup_root / candidate).exists():
                return candidate
        raise RuntimeError(f"backup id exhausted for timestamp: {base_id}")

    def list_backups(self) -> List[BackupManifest]:
        if not self.backup_root.exists():
            return []
        manifests: List[BackupManifest] = []
        for manifest_path in sorted(self.backup_root.glob("*/manifest.json")):
            manifests.append(self._read_manifest(manifest_path))
        return manifests

    def restore(self, backup_id: str, confirm: bool) -> str:
        if not confirm:
            return "confirmation-required"

        backup_dir = self.backup_root / backup_id
        manifest_path = backup_dir / "manifest.json"
        if not manifest_path.exists():
            return f"not-found: {backup_id}"

        manifest = self._read_manifest(manifest_path)
        for entry in manifest.files:
            if not entry.exists:
                continue
            backup_file = backup_dir / entry.backup
            target = Path(entry.source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_file, target)
        return "restored"

    def delete(self, backup_ids: Iterable[str], confirm: bool) -> str:
        if not confirm:
            return "confirmation-required"

        ids = list(dict.fromkeys(item.strip() for item in backup_ids if item.strip()))
        if not ids:
            return "no-backup-id"

        backup_dirs = []
        missing = []
        try:
            for backup_id in ids:
                backup_dir = self._backup_dir(backup_id)
                if not (backup_dir / "manifest.json").exists():
                    missing.append(backup_id)
                    continue
                backup_dirs.append(backup_dir)
        except ValueError as exc:
            return f"invalid: {exc}"

        if missing:
            return "not-found: " + ", ".join(missing)

        for backup_dir in backup_dirs:
            shutil.rmtree(backup_dir)
        return f"deleted: {len(backup_dirs)}"

    def _write_manifest(self, backup_dir: Path, manifest: BackupManifest) -> None:
        data = {
            "id": manifest.backup_id,
            "created_at": manifest.created_at,
            "reason": manifest.reason,
            "backup_root": manifest.backup_root,
            "files": [entry.__dict__ for entry in manifest.files],
        }
        (backup_dir / "manifest.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _read_manifest(self, manifest_path: Path) -> BackupManifest:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return BackupManifest(
            backup_id=str(data["id"]),
            created_at=str(data["created_at"]),
            reason=str(data.get("reason") or ""),
            backup_root=str(data.get("backup_root") or manifest_path.parent),
            files=[
                BackupFile(
                    source=str(entry["source"]),
                    backup=str(entry["backup"]),
                    exists=bool(entry["exists"]),
                    sha256=entry.get("sha256"),
                    size=entry.get("size"),
                )
                for entry in data.get("files", [])
            ],
        )

    def _backup_dir(self, backup_id: str) -> Path:
        raw = Path(backup_id)
        if raw.name != backup_id or backup_id in {"", ".", ".."}:
            raise ValueError(f"invalid backup id: {backup_id}")
        root = self.backup_root.resolve()
        backup_dir = (self.backup_root / backup_id).resolve()
        try:
            backup_dir.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"invalid backup id: {backup_id}") from exc
        if backup_dir == root:
            raise ValueError(f"invalid backup id: {backup_id}")
        return backup_dir

    def _relative_source(self, source: Path) -> Path:
        if source.is_absolute():
            return source.relative_to(source.anchor)
        return source

    def _sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
