import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from cli import main, write_text
from rendering import _display_width, render_models
from stores import ModelInfo
from test_operations import REQUIRED_CODEX_MODEL_KEYS


class RejectingStream(io.StringIO):
    encoding = "gbk"

    def write(self, text):
        if "✅" in text:
            raise UnicodeEncodeError("gbk", text, text.index("✅"), text.index("✅") + 1, "illegal multibyte sequence")
        return super().write(text)


class CliTests(unittest.TestCase):
    def test_doctor_command_prints_environment_check(self):
        output = io.StringIO()

        with redirect_stdout(output):
            code = main(["doctor"])

        self.assertEqual(code, 0)
        self.assertIn("环境检查 / 安装", output.getvalue())

    def test_install_without_yes_requires_confirmation(self):
        output = io.StringIO()

        with redirect_stdout(output):
            code = main(["install", "codex"])

        self.assertEqual(code, 2)
        self.assertIn("需要确认", output.getvalue())

    def test_write_text_replaces_unencodable_characters(self):
        stream = RejectingStream()

        write_text("✅ codex\n", stream=stream)

        self.assertIn("? codex", stream.getvalue())

    def test_provider_list_command_prints_providers(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "cc-switch.db"
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    "create table providers (id text, name text, app_type text, settings_config text, is_current integer)"
                )
                conn.execute(
                    "insert into providers values (?, ?, ?, ?, ?)",
                    (
                        "example-provider",
                        "example-provider",
                        "codex",
                        json.dumps({"config": 'model = "m1"\nbase_url = "http://example/v1"\n'}),
                        1,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["provider", "list", "--db", str(db_path)])

        self.assertEqual(code, 0)
        self.assertIn("供应商列表", output.getvalue())
        self.assertIn("example-provider", output.getvalue())

    def test_provider_list_prints_provider_name_and_unknown_model_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "cc-switch.db"
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    "create table providers (id text, name text, app_type text, settings_config text, is_current integer)"
                )
                conn.execute(
                    "insert into providers values (?, ?, ?, ?, ?)",
                    ("codex-official", "OpenAI Official", "codex", json.dumps({"auth": {}, "config": ""}), 0),
                )
                conn.commit()
            finally:
                conn.close()

            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["provider", "list", "--db", str(db_path)])

        text = output.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("OpenAI Official", text)
        self.assertRegex(text, r"codex-official\s+OpenAI Official\s+官方内置\s+动态\s+系统只读，不可切换")

    def test_provider_switch_refuses_codex_official_before_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "cc-switch.db"
            backup_root = root / "backups"
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    "create table providers (id text, name text, app_type text, settings_config text, is_current integer)"
                )
                conn.execute(
                    "insert into providers values (?, ?, ?, ?, ?)",
                    ("codex-official", "OpenAI Official", "codex", json.dumps({"auth": {}, "config": ""}), 0),
                )
                conn.commit()
            finally:
                conn.close()

            output = io.StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "provider",
                        "switch",
                        "codex-official",
                        "--db",
                        str(db_path),
                        "--catalog",
                        str(root / "catalog.json"),
                        "--config",
                        str(root / "config.toml"),
                        "--backup-root",
                        str(backup_root),
                        "--yes",
                    ]
                )

        self.assertEqual(code, 2)
        self.assertIn("不可由本工具切换", output.getvalue())
        self.assertFalse(backup_root.exists())

    def test_provider_delete_refuses_codex_official_even_with_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "cc-switch.db"
            backup_root = root / "backups"
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    "create table providers (id text, name text, app_type text, settings_config text, is_current integer)"
                )
                conn.execute(
                    "insert into providers values (?, ?, ?, ?, ?)",
                    ("codex-official", "OpenAI Official", "codex", json.dumps({"auth": {}, "config": ""}), 0),
                )
                conn.commit()
            finally:
                conn.close()

            output = io.StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "provider",
                        "delete",
                        "codex-official",
                        "--db",
                        str(db_path),
                        "--backup-root",
                        str(backup_root),
                        "--force",
                        "--yes",
                    ]
                )

        self.assertEqual(code, 2)
        self.assertIn("不可由本工具删除", output.getvalue())
        self.assertFalse(backup_root.exists())

    def test_provider_update_can_rename_provider_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "cc-switch.db"
            backup_root = root / "backups"
            conn = sqlite3.connect(str(db_path))
            try:
                settings = {
                    "config": 'model = "m1"\nbase_url = "http://old/v1"\nwire_api = "responses"\n',
                    "modelCatalog": {"models": [{"model": "m1", "displayName": "M1", "contextWindow": 128000}]},
                }
                conn.execute(
                    "create table providers (id text, name text, app_type text, settings_config text, is_current integer)"
                )
                conn.execute(
                    "insert into providers values (?, ?, ?, ?, ?)",
                    ("p1", "Provider One", "codex", json.dumps(settings), 1),
                )
                conn.commit()
            finally:
                conn.close()

            output = io.StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "provider",
                        "update",
                        "p1",
                        "--new-id",
                        "p-renamed",
                        "--name",
                        "Provider Renamed",
                        "--base-url",
                        "http://new/v1",
                        "--api-key",
                        "sk-new",
                        "--default-model",
                        "m1",
                        "--context-window",
                        "128000",
                        "--db",
                        str(db_path),
                        "--catalog",
                        str(root / "catalog.json"),
                        "--config",
                        str(root / "config.toml"),
                        "--backup-root",
                        str(backup_root),
                        "--yes",
                    ]
                )

            conn = sqlite3.connect(str(db_path))
            try:
                rows = conn.execute("select id, name, is_current from providers order by id").fetchall()
            finally:
                conn.close()

        self.assertEqual(code, 0)
        self.assertEqual(rows, [("p-renamed", "Provider Renamed", 1)])
        self.assertIn("供应商已修改: p1 -> p-renamed", output.getvalue())

    def test_model_list_command_prints_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog_path = Path(tmp) / "catalog.json"
            catalog_path.write_text(
                json.dumps({"models": [{"slug": "m1", "display_name": "Model One", "context_window": 128000}]}),
                encoding="utf-8",
            )

            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["model", "list", "--catalog", str(catalog_path)])

        self.assertEqual(code, 0)
        self.assertIn("模型列表", output.getvalue())
        self.assertIn("m1", output.getvalue())

    def test_model_list_warns_when_current_provider_catalog_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "cc-switch.db"
            catalog_path = root / "catalog.json"
            catalog_path.write_text(
                json.dumps({"models": [{"slug": "catalog-model", "display_name": "Catalog Model", "context_window": 128000}]}),
                encoding="utf-8",
            )
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    "create table providers (id text, name text, app_type text, settings_config text, is_current integer)"
                )
                settings = {
                    "config": 'model = "provider-model"\nbase_url = "http://example.test/v1"\n',
                    "modelCatalog": {"models": [{"model": "provider-model", "displayName": "Provider Model", "contextWindow": 128000}]},
                }
                conn.execute(
                    "insert into providers values (?, ?, ?, ?, ?)",
                    ("example-provider", "Example", "codex", json.dumps(settings), 1),
                )
                conn.commit()
            finally:
                conn.close()

            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["model", "list", "--catalog", str(catalog_path), "--db", str(db_path)])

        self.assertEqual(code, 0)
        self.assertIn("警告：当前供应商 example-provider 有 1 个模型，Codex catalog 有 1 个模型", output.getvalue())
        self.assertIn("model sync-current --yes", output.getvalue())

    def test_model_sync_current_rewrites_catalog_from_current_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "cc-switch.db"
            catalog_path = root / "catalog.json"
            config_path = root / "config.toml"
            backup_root = root / "backups"
            catalog_path.write_text(
                json.dumps({"models": [{"slug": "stale-model", "display_name": "Stale", "context_window": 64000}]}),
                encoding="utf-8",
            )
            config_path.write_text('model = "stale-model"\n', encoding="utf-8")
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    "create table providers (id text, name text, app_type text, settings_config text, is_current integer)"
                )
                settings = {
                    "config": 'model = "provider-model-2"\nbase_url = "http://example.test/v1"\n',
                    "modelCatalog": {
                        "models": [
                            {"model": "provider-model-1", "displayName": "Provider Model 1", "contextWindow": 128000},
                            {"model": "provider-model-2", "displayName": "Provider Model 2", "contextWindow": 200000},
                        ]
                    },
                }
                conn.execute(
                    "insert into providers values (?, ?, ?, ?, ?)",
                    ("example-provider", "Example", "codex", json.dumps(settings), 1),
                )
                conn.commit()
            finally:
                conn.close()

            output = io.StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "model",
                        "sync-current",
                        "--db",
                        str(db_path),
                        "--catalog",
                        str(catalog_path),
                        "--config",
                        str(config_path),
                        "--backup-root",
                        str(backup_root),
                        "--yes",
                    ]
                )

            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            config_text = config_path.read_text(encoding="utf-8")
            snapshot = json.loads((root / "example-provider-provider.json").read_text(encoding="utf-8"))
            backup_created = any(backup_root.glob("*/manifest.json"))

        self.assertEqual(code, 0)
        self.assertIn("已同步当前供应商模型到 Codex catalog: example-provider (2 个模型)", output.getvalue())
        self.assertEqual([model["slug"] for model in catalog["models"]], ["provider-model-1", "provider-model-2"])
        self.assertEqual([model["model"] for model in snapshot["modelCatalog"]["models"]], ["provider-model-1", "provider-model-2"])
        for model in catalog["models"]:
            self.assertTrue(REQUIRED_CODEX_MODEL_KEYS.issubset(model.keys()))
        self.assertIn('model = "provider-model-2"', config_text)
        self.assertIn('base_url = "http://127.0.0.1:15721/v1"', config_text)
        self.assertTrue(backup_created)

    def test_model_list_clips_long_names_without_overlapping_columns(self):
        model_id = "example-model-thinking-preview-ultra-long-experimental"
        text = render_models(
            [
                ModelInfo(
                    slug=model_id,
                    display_name=model_id,
                    context_window=128000,
                )
            ],
            width=80,
        )

        self.assertIn("…", text)
        for line in text.splitlines()[2:]:
            self.assertLessEqual(_display_width(line), 80)

    def test_model_list_caps_wide_terminal_width(self):
        text = render_models(
            [
                ModelInfo(
                    slug="example-model",
                    display_name="example-model",
                    context_window=200000,
                )
            ],
            width=200,
        )

        self.assertLessEqual(_display_width(text.splitlines()[2]), 120)
        self.assertLessEqual(_display_width(text.splitlines()[3]), 120)

    def test_backup_create_and_list_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "config.toml"
            source.write_text("before\n", encoding="utf-8")
            backup_root = root / "backups"

            output = io.StringIO()
            with redirect_stdout(output):
                create_code = main(
                    [
                        "backup",
                        "create",
                        "--backup-root",
                        str(backup_root),
                        "--source",
                        str(source),
                        "--reason",
                        "cli-test",
                    ]
                )

            list_output = io.StringIO()
            with redirect_stdout(list_output):
                list_code = main(["backup", "list", "--backup-root", str(backup_root)])

        self.assertEqual(create_code, 0)
        self.assertEqual(list_code, 0)
        self.assertIn("备份已创建", output.getvalue())
        self.assertIn("备份列表", list_output.getvalue())
        self.assertIn("cli-test", list_output.getvalue())

    def test_backup_restore_without_yes_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup_root = root / "backups"

            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["backup", "restore", "missing-id", "--backup-root", str(backup_root)])

        self.assertEqual(code, 2)
        self.assertIn("需要确认", output.getvalue())

    def test_backup_delete_command_removes_selected_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "config.toml"
            source.write_text("before\n", encoding="utf-8")
            backup_root = root / "backups"
            create_output = io.StringIO()
            with redirect_stdout(create_output):
                main(["backup", "create", "--backup-root", str(backup_root), "--source", str(source), "--reason", "delete-1"])
                main(["backup", "create", "--backup-root", str(backup_root), "--source", str(source), "--reason", "delete-2"])
            backup_ids = [path.name for path in sorted(backup_root.iterdir())]

            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["backup", "delete", *backup_ids, "--backup-root", str(backup_root), "--yes"])

            self.assertEqual(code, 0)
            self.assertIn("deleted: 2", output.getvalue())
            self.assertFalse(any(backup_root.iterdir()))

    def test_backup_delete_without_yes_requires_confirmation(self):
        output = io.StringIO()

        with redirect_stdout(output):
            code = main(["backup", "delete", "backup-id"])

        self.assertEqual(code, 2)
        self.assertIn("需要确认", output.getvalue())

    def test_model_set_default_command_updates_config_with_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.toml"
            config.write_text('model = "old"\n', encoding="utf-8")
            backup_root = root / "backups"

            output = io.StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "model",
                        "set-default",
                        "new-model",
                        "--config",
                        str(config),
                        "--backup-root",
                        str(backup_root),
                        "--yes",
                    ]
                )
            config_text = config.read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        self.assertIn('model = "new-model"', config_text)
        self.assertIn('base_url = "http://127.0.0.1:15721/v1"', config_text)
        self.assertIn("备份:", output.getvalue())

    def test_model_add_without_yes_requires_confirmation(self):
        output = io.StringIO()

        with redirect_stdout(output):
            code = main(["model", "add", "m2"])

        self.assertEqual(code, 2)
        self.assertIn("需要确认", output.getvalue())

    def test_provider_switch_command_updates_current_and_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "cc-switch.db"
            catalog = root / "catalog.json"
            config = root / "config.toml"
            config.write_text('model = "old"\n', encoding="utf-8")
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    "create table providers (id text, name text, app_type text, settings_config text, is_current integer)"
                )
                p1 = {"config": 'model = "m1"\n', "modelCatalog": {"models": [{"model": "m1"}]}}
                p2 = {
                    "config": 'model = "m2"\n',
                    "modelCatalog": {"models": [{"model": "m2", "displayName": "Model Two", "contextWindow": 64000}]},
                }
                conn.execute("insert into providers values (?, ?, ?, ?, ?)", ("p1", "p1", "codex", json.dumps(p1), 1))
                conn.execute("insert into providers values (?, ?, ?, ?, ?)", ("p2", "p2", "codex", json.dumps(p2), 0))
                conn.commit()
            finally:
                conn.close()

            output = io.StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "provider",
                        "switch",
                        "p2",
                        "--db",
                        str(db_path),
                        "--catalog",
                        str(catalog),
                        "--config",
                        str(config),
                        "--backup-root",
                        str(root / "backups"),
                        "--no-restart",
                        "--yes",
                    ]
                )
            conn = sqlite3.connect(str(db_path))
            try:
                rows = conn.execute("select id, is_current from providers order by id").fetchall()
            finally:
                conn.close()
            catalog_data = json.loads(catalog.read_text(encoding="utf-8"))
            config_text = config.read_text(encoding="utf-8")
            snapshot = json.loads((root / "p2-provider.json").read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(rows, [("p1", 0), ("p2", 1)])
        self.assertEqual(catalog_data["models"][0]["slug"], "m2")
        self.assertTrue(REQUIRED_CODEX_MODEL_KEYS.issubset(catalog_data["models"][0].keys()))
        self.assertIn('model = "m2"', config_text)
        self.assertIn('base_url = "http://127.0.0.1:15721/v1"', config_text)
        self.assertEqual(snapshot["modelCatalog"]["models"][0]["model"], "m2")

    def test_provider_update_command_updates_current_provider_and_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "cc-switch.db"
            catalog = root / "catalog.json"
            config = root / "config.toml"
            config.write_text('model = "old"\n', encoding="utf-8")
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    "create table providers (id text, name text, app_type text, settings_config text, is_current integer)"
                )
                settings = {
                    "name": "Provider 1",
                    "auth": {"OPENAI_API_KEY": "sk-old"},
                    "config": 'model = "m1"\nbase_url = "http://old/v1"\nwire_api = "responses"\n',
                    "modelCatalog": {"models": [{"model": "m1", "displayName": "Model One", "contextWindow": 128000}]},
                }
                conn.execute("insert into providers values (?, ?, ?, ?, ?)", ("p1", "Provider 1", "codex", json.dumps(settings), 1))
                conn.commit()
            finally:
                conn.close()

            output = io.StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "provider",
                        "update",
                        "p1",
                        "--name",
                        "Provider Updated",
                        "--base-url",
                        "http://updated/v1",
                        "--api-key",
                        "sk-updated",
                        "--default-model",
                        "m2",
                        "--context-window",
                        "64000",
                        "--api-format",
                        "chat",
                        "--sync-current",
                        "--db",
                        str(db_path),
                        "--catalog",
                        str(catalog),
                        "--config",
                        str(config),
                        "--backup-root",
                        str(root / "backups"),
                        "--yes",
                    ]
                )
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute("select name, settings_config from providers where id='p1'").fetchone()
            finally:
                conn.close()
            settings = json.loads(row[1])
            catalog_data = json.loads(catalog.read_text(encoding="utf-8"))
            config_text = config.read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        self.assertEqual(row[0], "Provider Updated")
        self.assertEqual(settings["auth"]["OPENAI_API_KEY"], "sk-updated")
        self.assertEqual(catalog_data["models"][-1]["slug"], "m2")
        self.assertTrue(REQUIRED_CODEX_MODEL_KEYS.issubset(catalog_data["models"][-1].keys()))
        self.assertIn('model = "m2"', config_text)
        self.assertIn('base_url = "http://127.0.0.1:15721/v1"', config_text)
        self.assertIn("供应商已修改: p1", output.getvalue())


if __name__ == "__main__":
    unittest.main()
