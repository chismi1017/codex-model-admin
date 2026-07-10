import unittest

from rendering import _display_width, render_providers
from stores import ProviderInfo


class ProviderRenderingTests(unittest.TestCase):
    def test_provider_columns_stay_aligned_with_long_base_url(self):
        text = render_providers(
            [
                ProviderInfo(
                    "very-long-provider-id",
                    "Very Long Provider Name",
                    "https://api.example.com/v1/extra/path",
                    "model-default",
                    "responses",
                    123,
                    True,
                )
            ]
        )

        row = text.splitlines()[3]
        default_column = row.index("model-default")

        self.assertEqual(_display_width(row[:default_column]), 89)
        self.assertIn("…", row)
        self.assertIn(" 123  model-default", row)

    def test_codex_official_renders_as_read_only_system_provider(self):
        text = render_providers(
            [
                ProviderInfo(
                    "codex-official",
                    "OpenAI Official",
                    "",
                    "",
                    "",
                    None,
                    False,
                    True,
                )
            ]
        )

        self.assertIn("官方内置", text)
        self.assertIn("动态", text)
        self.assertIn("系统只读，不可切换", text)


if __name__ == "__main__":
    unittest.main()
