import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from stores import ModelStore, ProviderStore


class ProviderStoreTests(unittest.TestCase):
    def test_lists_providers_with_derived_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "cc-switch.db"
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    "create table providers (id text, name text, app_type text, settings_config text, is_current integer)"
                )
                settings = {
                    "config": 'model = "example-model"\nbase_url = "http://example.test/v1"\nwire_api = "responses"\n',
                    "modelCatalog": {
                        "models": [
                            {"model": "example-model", "displayName": "Model", "contextWindow": 200000},
                            {"model": "example-model-fast", "displayName": "Fast Model", "contextWindow": 128000},
                        ]
                    },
                }
                conn.execute(
                    "insert into providers values (?, ?, ?, ?, ?)",
                    ("example-provider", "example-provider", "codex", json.dumps(settings), 1),
                )
                conn.commit()
            finally:
                conn.close()

            providers = ProviderStore(db_path).list_providers()

        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0].provider_id, "example-provider")
        self.assertEqual(providers[0].base_url, "http://example.test/v1")
        self.assertEqual(providers[0].default_model, "example-model")
        self.assertEqual(providers[0].model_count, 2)
        self.assertTrue(providers[0].current)

    def test_provider_without_model_catalog_has_unknown_model_count(self):
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

            providers = ProviderStore(db_path).list_providers()

        self.assertEqual(providers[0].provider_id, "codex-official")
        self.assertEqual(providers[0].name, "OpenAI Official")
        self.assertIsNone(providers[0].model_count)
        self.assertTrue(providers[0].read_only)


class ModelStoreTests(unittest.TestCase):
    def test_lists_models_from_codex_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog_path = Path(tmp) / "cc-switch-model-catalog.json"
            catalog_path.write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "slug": "example-model",
                                "display_name": "Model Max",
                                "context_window": 200000,
                            },
                            {
                                "slug": "example-model-fast",
                                "display_name": "Fast Model Flash",
                                "context_window": 128000,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            models = ModelStore(catalog_path).list_models()

        self.assertEqual([model.slug for model in models], ["example-model", "example-model-fast"])
        self.assertEqual(models[0].display_name, "Model Max")
        self.assertEqual(models[0].context_window, 200000)


if __name__ == "__main__":
    unittest.main()
