import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backups import BackupManager


class BackupManagerTests(unittest.TestCase):
    def test_create_backup_copies_existing_files_and_records_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "root" / ".codex" / "config.toml"
            source.parent.mkdir(parents=True)
            source.write_text('model = "m1"\n', encoding="utf-8")
            missing = root / "root" / ".codex" / "missing.toml"
            backup_root = root / "backups"

            manifest = BackupManager(backup_root=backup_root, sources=[source, missing]).create("unit-test")

            self.assertEqual(manifest.reason, "unit-test")
            self.assertEqual(len(manifest.files), 2)
            copied = backup_root / manifest.backup_id / "files" / source.relative_to(source.anchor)
            self.assertTrue(copied.exists())
            self.assertEqual(copied.read_text(encoding="utf-8"), 'model = "m1"\n')
            missing_entry = [entry for entry in manifest.files if entry.source == str(missing)][0]
            self.assertFalse(missing_entry.exists)

    def test_list_backups_reads_manifest_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "config.toml"
            source.write_text("before\n", encoding="utf-8")
            manager = BackupManager(backup_root=root / "backups", sources=[source])

            created = manager.create("first")
            backups = manager.list_backups()

        self.assertEqual([backup.backup_id for backup in backups], [created.backup_id])
        self.assertEqual(backups[0].reason, "first")

    def test_restore_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "config.toml"
            source.write_text("before\n", encoding="utf-8")
            manager = BackupManager(backup_root=root / "backups", sources=[source])
            created = manager.create("restore-test")
            source.write_text("after\n", encoding="utf-8")

            result = manager.restore(created.backup_id, confirm=False)

        self.assertEqual(result, "confirmation-required")

    def test_restore_overwrites_files_from_backup_when_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "config.toml"
            source.write_text("before\n", encoding="utf-8")
            manager = BackupManager(backup_root=root / "backups", sources=[source])
            created = manager.create("restore-test")
            source.write_text("after\n", encoding="utf-8")

            result = manager.restore(created.backup_id, confirm=True)

            self.assertEqual(result, "restored")
            self.assertEqual(source.read_text(encoding="utf-8"), "before\n")

    def test_delete_removes_one_or_more_backup_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "config.toml"
            source.write_text("before\n", encoding="utf-8")
            manager = BackupManager(backup_root=root / "backups", sources=[source])
            first = manager.create("first")
            second = manager.create("second")

            result = manager.delete([first.backup_id, second.backup_id], confirm=True)

            self.assertEqual(result, "deleted: 2")
            self.assertFalse((root / "backups" / first.backup_id).exists())
            self.assertFalse((root / "backups" / second.backup_id).exists())

    def test_delete_requires_confirmation_and_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "config.toml"
            source.write_text("before\n", encoding="utf-8")
            manager = BackupManager(backup_root=root / "backups", sources=[source])
            created = manager.create("delete-test")

            unconfirmed = manager.delete([created.backup_id], confirm=False)
            invalid = manager.delete(["../outside"], confirm=True)

            self.assertEqual(unconfirmed, "confirmation-required")
            self.assertIn("invalid:", invalid)
            self.assertTrue((root / "backups" / created.backup_id).exists())

    def test_manifest_json_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "config.toml"
            source.write_text("before\n", encoding="utf-8")
            manager = BackupManager(backup_root=root / "backups", sources=[source])

            created = manager.create("json-test")
            data = json.loads((root / "backups" / created.backup_id / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(data["id"], created.backup_id)
        self.assertEqual(data["reason"], "json-test")
        self.assertEqual(data["files"][0]["source"], str(source))

    def test_create_backup_uses_unique_id_when_called_with_same_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "config.toml"
            source.write_text("before\n", encoding="utf-8")
            manager = BackupManager(backup_root=root / "backups", sources=[source])

            class FixedDateTime:
                @classmethod
                def now(cls):
                    from datetime import datetime

                    return datetime(2026, 7, 6, 16, 6, 56)

            with patch("backups.datetime", FixedDateTime):
                first = manager.create("first")
                second = manager.create("second")

        self.assertEqual(first.backup_id, "20260706-160656")
        self.assertEqual(second.backup_id, "20260706-160656-001")
        self.assertNotEqual(first.backup_root, second.backup_root)


if __name__ == "__main__":
    unittest.main()
