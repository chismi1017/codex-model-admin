import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from operations import (
    CatalogManager,
    CodexConfigManager,
    ModelManager,
    ProviderManager,
)


REQUIRED_CODEX_MODEL_KEYS = {
    "additional_speed_tiers",
    "apply_patch_tool_type",
    "availability_nux",
    "base_instructions",
    "context_window",
    "default_reasoning_level",
    "default_reasoning_summary",
    "default_verbosity",
    "description",
    "display_name",
    "effective_context_window_percent",
    "experimental_supported_tools",
    "input_modalities",
    "max_context_window",
    "model_messages",
    "priority",
    "service_tiers",
    "shell_type",
    "slug",
    "support_verbosity",
    "supported_in_api",
    "supported_reasoning_levels",
    "supports_image_detail_original",
    "supports_parallel_tool_calls",
    "supports_reasoning_summaries",
    "supports_search_tool",
    "truncation_policy",
    "upgrade",
    "use_responses_lite",
    "visibility",
    "web_search_tool_type",
}


def create_provider_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("create table providers (id text, name text, app_type text, settings_config text, is_current integer)")
        settings = {
            "config": 'model = "m1"\nbase_url = "http://example/v1"\nwire_api = "responses"\n',
            "modelCatalog": {"models": [{"model": "m1", "displayName": "Model One", "contextWindow": 128000}]},
        }
        conn.execute("insert into providers values (?, ?, ?, ?, ?)", ("p1", "p1", "codex", json.dumps(settings), 1))
        conn.commit()
    finally:
        conn.close()


class CodexConfigManagerTests(unittest.TestCase):
    def test_set_default_model_replaces_existing_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('model = "old"\nbase_url = "http://old/v1"\n', encoding="utf-8")

            CodexConfigManager(path).set_default_model("new-model")

            self.assertIn('model = "new-model"', path.read_text(encoding="utf-8"))


class ProviderManagerTests(unittest.TestCase):
    def test_add_switch_and_delete_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "cc-switch.db"
            create_provider_db(db_path)
            manager = ProviderManager(db_path)

            manager.add_provider(
                provider_id="p2",
                name="Provider Two",
                base_url="http://two/v1",
                api_key="sk-test",
                default_model="m2",
                context_window=64000,
                switch=True,
            )
            switched = manager.switch_provider("p2")
            manager.delete_provider("p1")
            snapshot = json.loads((db_path.parent / "p2-provider.json").read_text(encoding="utf-8"))

            conn = sqlite3.connect(str(db_path))
            try:
                rows = conn.execute("select id, is_current from providers order by id").fetchall()
            finally:
                conn.close()

        self.assertEqual(rows, [("p2", 1)])
        self.assertIn("modelCatalog", switched)
        self.assertEqual(snapshot["modelCatalog"]["models"][0]["model"], "m2")

    def test_update_provider_preserves_existing_models_and_updates_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "cc-switch.db"
            create_provider_db(db_path)
            manager = ProviderManager(db_path)
            manager.add_model_to_provider("p1", "m-extra", "Extra Model", 32000)

            settings = manager.update_provider(
                provider_id="p1",
                name="Provider One Updated",
                base_url="http://updated/v1",
                api_key="sk-updated",
                default_model="m2",
                context_window=64000,
                api_format="chat",
            )
            snapshot = json.loads((db_path.parent / "p1-provider.json").read_text(encoding="utf-8"))

            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute("select name from providers where id='p1'").fetchone()
            finally:
                conn.close()

        models = settings["modelCatalog"]["models"]
        self.assertEqual(row[0], "Provider One Updated")
        self.assertEqual(settings["auth"]["OPENAI_API_KEY"], "sk-updated")
        self.assertIn('model = "m2"', settings["config"])
        self.assertIn('base_url = "http://updated/v1"', settings["config"])
        self.assertIn('wire_api = "chat"', settings["config"])
        self.assertEqual([entry["model"] for entry in models], ["m1", "m-extra", "m2"])
        self.assertEqual(models[-1]["contextWindow"], 64000)
        self.assertEqual([entry["model"] for entry in snapshot["modelCatalog"]["models"]], ["m1", "m-extra", "m2"])

    def test_update_provider_can_rename_provider_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "cc-switch.db"
            create_provider_db(db_path)
            manager = ProviderManager(db_path)
            manager.write_provider_snapshot("p1", manager.load_settings("p1"))

            settings = manager.update_provider(
                provider_id="p1",
                new_provider_id="renamed-provider",
                name="Renamed Provider",
                base_url="http://renamed/v1",
                api_key="sk-renamed",
                default_model="m1",
                context_window=128000,
            )

            conn = sqlite3.connect(str(db_path))
            try:
                rows = conn.execute("select id, name, is_current from providers order by id").fetchall()
            finally:
                conn.close()
            old_snapshot_exists = (db_path.parent / "p1-provider.json").exists()
            new_snapshot_exists = (db_path.parent / "renamed-provider-provider.json").exists()
            renamed_is_current = manager.is_current_provider("renamed-provider")
            with self.assertRaisesRegex(ValueError, "provider not found"):
                manager.load_settings("p1")

        self.assertEqual(rows, [("renamed-provider", "Renamed Provider", 1)])
        self.assertEqual(settings["modelCatalog"]["models"][0]["model"], "m1")
        self.assertFalse(old_snapshot_exists)
        self.assertTrue(new_snapshot_exists)
        self.assertTrue(renamed_is_current)

    def test_update_provider_refuses_existing_new_provider_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "cc-switch.db"
            create_provider_db(db_path)
            manager = ProviderManager(db_path)
            manager.add_provider(
                provider_id="p2",
                name="Provider Two",
                base_url="http://two/v1",
                api_key="sk-test",
                default_model="m2",
                context_window=64000,
            )

            with self.assertRaisesRegex(ValueError, "provider already exists"):
                manager.update_provider(
                    provider_id="p1",
                    new_provider_id="p2",
                    name="Provider One",
                    base_url="http://one/v1",
                    api_key="sk-one",
                    default_model="m1",
                    context_window=128000,
                )

    def test_refuses_to_switch_or_delete_codex_official(self):
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
            manager = ProviderManager(db_path)

            with self.assertRaisesRegex(ValueError, "不可由本工具切换"):
                manager.switch_provider("codex-official")
            with self.assertRaisesRegex(ValueError, "不可由本工具删除"):
                manager.delete_provider("codex-official", force=True)


class CatalogManagerTests(unittest.TestCase):
    def test_add_model_writes_codex_compatible_catalog_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog_path = Path(tmp) / "catalog.json"

            CatalogManager(catalog_path).add_model("m1", "Model One", 128000)
            model = json.loads(catalog_path.read_text(encoding="utf-8"))["models"][0]

        self.assertTrue(REQUIRED_CODEX_MODEL_KEYS.issubset(model.keys()))
        self.assertEqual(model["shell_type"], "shell_command")
        self.assertEqual(model["default_reasoning_level"], "high")
        self.assertEqual(model["truncation_policy"], {"mode": "tokens", "limit": 128000})
        self.assertEqual([level["effort"] for level in model["supported_reasoning_levels"]], ["low", "medium", "high"])


class ModelManagerTests(unittest.TestCase):
    def test_add_set_default_and_delete_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "cc-switch.db"
            create_provider_db(db_path)
            catalog_path = root / "catalog.json"
            catalog_path.write_text(
                json.dumps({"models": [{"slug": "m1", "display_name": "Model One", "context_window": 128000}]}),
                encoding="utf-8",
            )
            config_path = root / "config.toml"
            config_path.write_text('model = "m1"\n', encoding="utf-8")
            codex_home = root / ".codex"
            manager = ModelManager(
                ProviderManager(db_path),
                CatalogManager(catalog_path),
                CodexConfigManager(config_path),
                codex_home=codex_home,
            )

            manager.add("p1", "m2", "Model Two", 64000, profile="m2-profile", make_default=True)
            manager.delete("p1", "m1", force=True)

            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            settings = ProviderManager(db_path).load_settings("p1")
            snapshot = json.loads((db_path.parent / "p1-provider.json").read_text(encoding="utf-8"))
            config_text = config_path.read_text(encoding="utf-8")
            profile_exists = (codex_home / "m2-profile.config.toml").exists()

        self.assertEqual([entry["slug"] for entry in catalog["models"]], ["m2"])
        self.assertEqual(settings["modelCatalog"]["models"][0]["model"], "m2")
        self.assertEqual(snapshot["modelCatalog"]["models"][0]["model"], "m2")
        self.assertEqual(config_text, 'model = "m2"\n')
        self.assertTrue(profile_exists)


if __name__ == "__main__":
    unittest.main()
