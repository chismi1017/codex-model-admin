import contextlib
import io
import json
import os
import re
import tempfile
import unittest
import unicodedata
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from backups import BackupFile, BackupManifest
from cli import main
from stores import ModelInfo, ProviderInfo
from tui import (
    FormField,
    ModelHealthResult,
    _build_model_add_argv,
    _bulk_model_ids,
    _catalog_model_choices,
    _chat_completions_endpoint,
    _check_model_health_from_api,
    _check_model_health,
    _check_provider_default_model_health,
    _clear_provider_default_health,
    _escape_sequence_complete,
    _existing_model_ids,
    _fetch_models_from_api,
    _fetch_provider_models,
    _filter_models,
    _is_next_focus_key,
    _is_previous_focus_key,
    _is_right_key,
    _mask_argv,
    _model_catalog_sync_messages,
    _model_delete_enabled,
    _model_delete_is_current_default,
    _menu_status,
    _model_selector_hint_lines,
    _models_endpoint,
    _next_choice,
    _provider_default_health_matches,
    _provider_model_choices,
    _provider_switch,
    _provider_update,
    _provider_update_form_values,
    _raw_newlines,
    _redraw_focus_form,
    _responses_endpoint,
    _run_bulk_output_commands,
    _run_backup_delete_selector,
    _run_default_model_selector,
    _run_model_delete_quietly,
    _run_model_delete_output_commands,
    _run_model_delete_selector,
    _run_model_health_check,
    _run_model_sync_current_quietly,
    _run_output_command,
    _selected_models_summary,
    _set_bulk_model_ids,
    _switchable_provider_choices,
    _utf8_expected_length,
    render_action_form,
    render_backup_delete_confirm,
    render_backup_delete_selector,
    render_default_model_selector,
    render_focus_form,
    render_model_delete_confirm,
    render_model_delete_selector,
    render_model_detail,
    render_model_health_check,
    render_model_list_interactive,
    render_menu,
    render_model_selector,
    run_tui,
)


def _display_width(text: str) -> int:
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


class TuiRenderingTests(unittest.TestCase):
    def test_render_menu_uses_codex_cli_style_sections(self):
        text = render_menu(width=86, color=False)

        self.assertIn("╭", text)
        self.assertIn("╰", text)
        self.assertIn(">_ Codex Model Admin", text)
        self.assertIn("当前路由：", text)
        self.assertIn("供应商：", text)
        self.assertIn("默认模型：", text)
        self.assertIn("工作目录：", text)
        self.assertIn("环境 / 安装", text)
        self.assertIn("供应商", text)
        self.assertIn("修改供应商", text)
        self.assertIn("模型", text)
        self.assertIn("代理", text)
        self.assertIn("备份", text)
        self.assertIn("删除备份", text)
        self.assertIn("界面语言 (Language)", text)
        self.assertIn("› 请选择操作", text)

    def test_render_menu_shows_current_provider_and_model_status(self):
        text = render_menu(
            width=112,
            color=False,
            provider_status="example-provider",
            model_status="example-model",
        )

        self.assertIn("供应商：", text)
        self.assertIn("example-provider", text)
        self.assertIn("默认模型：", text)
        self.assertIn("example-model", text)

    def test_render_menu_uses_english_header_labels(self):
        text = render_menu(
            width=112,
            color=False,
            language="en",
            provider_status="example-provider",
            model_status="example-model",
        )

        self.assertIn("routing:", text)
        self.assertIn("provider:", text)
        self.assertIn("model:", text)
        self.assertIn("directory:", text)
        self.assertNotIn("当前路由：", text)

    def test_current_menu_item_highlights_whole_row(self):
        text = render_menu(width=100, color=True, language="zh")

        self.assertIn("\033[7m  ›  1. 环境检查", text)
        self.assertIn("[只读]", text)
        self.assertNotIn("\033[1;36m›\033[0m", text)

    def test_render_menu_marks_selected_action(self):
        text = render_menu(width=100, color=False, language="zh", selected_action="10")

        self.assertIn("› 10. 设置默认模型", text)
        self.assertIn("   1. 环境检查", text)

    def test_render_backup_delete_selector_supports_multi_select(self):
        backups = [
            BackupManifest(
                backup_id="20260709-100000",
                created_at="2026-07-09T10:00:00+08:00",
                reason="first",
                backup_root="/tmp/backups/20260709-100000",
                files=[BackupFile("/tmp/a", "files/tmp/a", True, None, 1)],
            ),
            BackupManifest(
                backup_id="20260709-100100",
                created_at="2026-07-09T10:01:00+08:00",
                reason="second",
                backup_root="/tmp/backups/20260709-100100",
                files=[BackupFile("/tmp/b", "files/tmp/b", True, None, 1)],
            ),
        ]

        with patch("tui._menu_status", return_value=("example-provider", "m1")):
            text = render_backup_delete_selector(
                "zh",
                backups,
                selected_index=1,
                selected_backup_ids=["20260709-100000"],
                color=False,
            )

        self.assertIn("删除备份", text)
        self.assertIn("› [ ] 20260709-100100", text)
        self.assertIn("[x] 20260709-100000", text)
        self.assertIn("已选 1: 20260709-100000", text)
        self.assertIn("Space 选中/取消", text)
        self.assertIn("Ctrl+A 全选/清空", text)

    def test_backup_delete_selector_ctrl_a_selects_all(self):
        backups = [
            BackupManifest(
                backup_id="20260709-100000",
                created_at="2026-07-09T10:00:00+08:00",
                reason="first",
                backup_root="/tmp/backups/20260709-100000",
                files=[BackupFile("/tmp/a", "files/tmp/a", True, None, 1)],
            ),
            BackupManifest(
                backup_id="20260709-100100",
                created_at="2026-07-09T10:01:00+08:00",
                reason="second",
                backup_root="/tmp/backups/20260709-100100",
                files=[BackupFile("/tmp/b", "files/tmp/b", True, None, 1)],
            ),
        ]

        class FakeBackupManager:
            def list_backups(self):
                return backups

        class FakeStdin:
            def fileno(self):
                return 0

        class FakeTermios:
            TCSADRAIN = 0

            def tcgetattr(self, fd):
                return "old"

            def tcsetattr(self, fd, when, settings):
                return None

        class FakeTty:
            def setraw(self, fd):
                return None

        keys = iter(["\x01", "\n"])

        with (
            patch("tui.BackupManager", FakeBackupManager),
            patch("tui.sys.stdin", FakeStdin()),
            patch("tui.termios", FakeTermios()),
            patch("tui.tty", FakeTty()),
            patch("tui._clear_screen"),
            patch("tui._write_focus_form_update", return_value=0),
            patch("tui._read_key", side_effect=lambda: next(keys)),
            patch("tui._menu_status", return_value=("example-provider", "m1")),
        ):
            selected = _run_backup_delete_selector("zh")

        self.assertEqual(selected, ["20260709-100000", "20260709-100100"])

    def test_render_backup_delete_confirm_lists_selected_ids(self):
        with patch("tui._menu_status", return_value=("example-provider", "m1")):
            text = render_backup_delete_confirm("zh", ["20260709-100000", "20260709-100100"], color=False)

        self.assertIn("删除备份", text)
        self.assertIn("- 20260709-100000", text)
        self.assertIn("- 20260709-100100", text)
        self.assertIn("按 y 删除选中备份", text)

    def test_render_action_form_shows_all_fields_and_help(self):
        text = render_action_form(
            "新增模型",
            [
                FormField("model_id", "模型 ID", required=True),
                FormField("make_default", "设为默认? yes/no", "no", choices=("yes", "no")),
            ],
            {"model_id": "m1", "make_default": "no"},
            language="zh",
            color=False,
        )

        self.assertIn("新增模型", text)
        self.assertIn("1. 模型 ID *", text)
        self.assertIn("2. 设为默认? yes/no", text)
        self.assertIn("p 预览", text)

    def test_required_single_field_label_is_not_truncated(self):
        action_text = render_action_form(
            "设置默认模型",
            [FormField("model_id", "模型 ID", required=True)],
            {"model_id": ""},
            language="zh",
            color=False,
        )
        focus_text = render_focus_form(
            "设置默认模型",
            [FormField("model_id", "模型 ID", required=True)],
            {"model_id": ""},
            0,
            language="zh",
            color=False,
        )

        self.assertIn("1. 模型 ID *", action_text)
        self.assertIn("Codex Model Admin / 设置默认模型", focus_text)
        self.assertIn("› 模型 ID * [ - ]", focus_text)
        self.assertNotIn("…", action_text)
        self.assertNotIn("…", focus_text)

    def test_preview_masks_secret_argv_values(self):
        self.assertEqual(
            _mask_argv(["provider", "add", "p1", "--api-key", "sk-secret", "--yes"]),
            ["provider", "add", "p1", "--api-key", "********", "--yes"],
        )

    def test_render_focus_form_marks_current_field(self):
        text = render_focus_form(
            "新增供应商",
            [
                FormField("provider_id", "供应商 ID", required=True),
                FormField("switch", "新增后切换", "no", choices=("yes", "no")),
            ],
            {"provider_id": "p1", "switch": "no"},
            1,
            language="zh",
            color=False,
        )

        self.assertIn("› 新增后切换", text)
        self.assertIn("[ no ] (yes/no)", text)
        self.assertIn("选项字段按 Space/←→ 切换", text)
        self.assertNotIn("Space 切换选项", text)
        self.assertIn("Ctrl+P 预览", text)

    def test_next_choice_cycles_choices(self):
        field = FormField("switch", "新增后切换", "no", choices=("yes", "no"))

        self.assertEqual(_next_choice(field, "no", 1), "yes")
        self.assertEqual(_next_choice(field, "yes", 1), "no")
        self.assertEqual(_next_choice(field, "yes", -1), "no")

    def test_model_filter_matches_all_tokens(self):
        models = ["model-max", "model-coder-plus", "gpt-4o"]

        self.assertEqual(_filter_models(models, "model plus"), ["model-coder-plus"])
        self.assertEqual(_filter_models(models, ""), models)

    def test_render_model_selector_shows_filtered_model(self):
        text = render_model_selector(
            "zh",
            "example-provider",
            ["model-max", "model-coder-plus", "gpt-4o"],
            "model plus",
            0,
            ["model-coder-plus"],
            color=False,
        )

        self.assertIn("选择模型 - example-provider", text)
        self.assertIn("筛选: model plus", text)
        self.assertIn("› [x] model-coder-plus", text)
        self.assertIn("已选 1: model-coder-plus", text)
        self.assertNotIn("gpt-4o", text)

    def test_render_model_delete_selector_supports_multi_select(self):
        text = render_model_delete_selector(
            "zh",
            "example-provider",
            ["model-max", "model-coder-plus", "gpt-4o"],
            "model",
            1,
            ["model-coder-plus"],
            "model-coder-plus",
            color=False,
        )

        self.assertIn("选择要删除的模型 - example-provider", text)
        self.assertIn("筛选: model", text)
        self.assertIn("› [x] model-coder-plus (当前)", text)
        self.assertIn("已选择待删除模型 1: model-coder-plus", text)
        self.assertIn("Ctrl+A 全选/清空当前筛选", text)
        self.assertNotIn("gpt-4o", text)

    def test_model_delete_selector_ctrl_a_selects_filtered_models(self):
        keys = iter(["\x01", "\n"])

        with (
            patch("tui._clear_screen"),
            patch("tui._write_focus_form_update", return_value=0),
            patch("tui._read_key", side_effect=lambda: next(keys)),
        ):
            selected = _run_model_delete_selector(
                "zh",
                "example-provider",
                ["model-max", "model-coder-plus", "gpt-4o"],
            )

        self.assertEqual(selected, ["model-max", "model-coder-plus", "gpt-4o"])

    def test_render_model_list_interactive_highlights_selected_row(self):
        models = [
            ModelInfo("example-model", "example-model", 200000),
            ModelInfo("example-model-alt", "example-model-alt", 128000),
        ]

        with patch("tui._menu_status", return_value=("example-provider", "example-model-alt")):
            plain = render_model_list_interactive("zh", models, selected_index=1, width=80, color=False)
            colored = render_model_list_interactive("zh", models, selected_index=1, width=80, color=True)

        self.assertIn("默认模型：", plain)
        self.assertIn("› example-model-alt", plain)
        self.assertIn("健康", plain)
        self.assertIn("未检测", plain)
        self.assertIn("Space 检测当前", plain)
        self.assertIn("Ctrl+A 检测全部", plain)
        self.assertIn("s 同步 catalog", plain)
        self.assertIn("d 删除失败项", plain)
        self.assertIn("\033[7m", colored)

    def test_model_catalog_sync_message_detects_mismatch(self):
        with (
            patch("tui._current_provider_id", return_value="example-provider"),
            patch("tui._provider_model_ids", return_value={"m1", "m2"}),
            patch("tui._catalog_model_ids", return_value={"m1"}),
        ):
            messages = _model_catalog_sync_messages("zh")

        self.assertEqual(
            messages,
            ["当前供应商有 2 个模型，Codex catalog 有 1 个模型；按 s 同步当前供应商。"],
        )

    def test_run_model_sync_current_quietly_calls_cli_sync(self):
        def fake_main(argv):
            print("已同步当前供应商模型到 Codex catalog: example-provider (2 个模型)")
            print("备份: backup-1")
            return 0

        with patch("tui.main", side_effect=fake_main) as mocked_main:
            code, message = _run_model_sync_current_quietly()

        self.assertEqual(code, 0)
        self.assertEqual(message, "已同步当前供应商模型到 Codex catalog: example-provider (2 个模型)")
        mocked_main.assert_called_once_with(["model", "sync-current", "--yes"])

    def test_render_model_list_interactive_shows_health_states(self):
        models = [
            ModelInfo("ok-model", "OK Model", 128000),
            ModelInfo("fail-model", "Fail Model", 128000),
            ModelInfo("checking-model", "Checking Model", 128000),
        ]
        health_results = {
            "ok-model": ModelHealthResult("ok-model", True, "responses"),
            "fail-model": ModelHealthResult("fail-model", False, "timeout"),
        }

        with patch("tui._menu_status", return_value=("example-provider", "example-model-alt")):
            text = render_model_list_interactive(
                "zh",
                models,
                selected_index=2,
                width=90,
                color=False,
                health_results=health_results,
                checking_model="checking-model",
            )

        self.assertIn("OK", text)
        self.assertIn("FAIL", text)
        self.assertIn("检测中", text)

    def test_render_model_detail_shows_full_model_id(self):
        model = ModelInfo(
            "example-model-thinking-preview-ultra-long-experimental",
            "example-model-thinking-preview-ultra-long-experimental",
            128000,
        )

        with patch("tui._menu_status", return_value=("example-provider", "example-model-alt")):
            text = render_model_detail("zh", model, color=False)

        self.assertIn("模型详情", text)
        self.assertIn("example-model-thinking-preview-ultra-long-experimental", text)
        self.assertIn("按 Enter、Esc 或 q 返回模型列表。", text)

    def test_render_model_detail_shows_health_detail(self):
        model = ModelInfo("fail-model", "Fail Model", 128000)

        with patch("tui._menu_status", return_value=("example-provider", "example-model-alt")):
            text = render_model_detail(
                "zh",
                model,
                color=False,
                health_results={"fail-model": ModelHealthResult("fail-model", False, "timeout")},
            )

        self.assertIn("健康: FAIL (timeout)", text)

    def test_model_delete_enabled_only_for_failed_health(self):
        model = ModelInfo("fail-model", "Fail Model", 128000)
        health_results = {
            "ok-model": ModelHealthResult("ok-model", True, "responses"),
            "fail-model": ModelHealthResult("fail-model", False, "timeout"),
        }

        self.assertTrue(_model_delete_enabled(model, health_results))
        self.assertFalse(_model_delete_enabled(ModelInfo("ok-model", "OK Model", 128000), health_results))
        self.assertFalse(_model_delete_enabled(ModelInfo("unknown-model", "Unknown Model", 128000), health_results))

    def test_model_delete_is_current_default_uses_menu_status(self):
        with patch("tui._menu_status", return_value=("example-provider", "current-model")):
            self.assertTrue(_model_delete_is_current_default(ModelInfo("current-model", "Current Model", 128000)))
            self.assertFalse(_model_delete_is_current_default(ModelInfo("other-model", "Other Model", 128000)))

    def test_render_model_delete_confirm_shows_failed_model_context(self):
        model = ModelInfo("fail-model", "Fail Model", 128000)

        with patch("tui._menu_status", return_value=("example-provider", "ok-model")):
            text = render_model_delete_confirm(
                "zh",
                model,
                ModelHealthResult("fail-model", False, "timeout"),
                color=False,
            )

        self.assertIn("删除失败模型", text)
        self.assertIn("模型: fail-model", text)
        self.assertIn("显示名称: Fail Model", text)
        self.assertIn("健康: FAIL (timeout)", text)
        self.assertIn("按 y 删除此失败模型", text)

    def test_run_model_delete_quietly_calls_cli_delete(self):
        def fake_main(argv):
            print("模型已删除: fail-model")
            print("备份: backup-1")
            return 0

        with patch("tui.main", side_effect=fake_main) as mocked_main:
            code, message = _run_model_delete_quietly("example-provider", "fail-model")

        self.assertEqual(code, 0)
        self.assertEqual(message, "备份: backup-1")
        mocked_main.assert_called_once_with(["model", "delete", "fail-model", "--provider-id", "example-provider", "--yes"])

    def test_run_model_delete_output_commands_runs_each_delete(self):
        calls = []
        output = io.StringIO()

        with (
            patch("tui.main", side_effect=lambda argv: calls.append(argv) or 0),
            patch("tui._is_interactive_terminal", return_value=False),
            patch("tui._pause_after_output"),
            contextlib.redirect_stdout(output),
        ):
            _run_model_delete_output_commands(
                "zh",
                "批量删除模型",
                [
                    ("m1", ["model", "delete", "m1", "--provider-id", "p1", "--yes"]),
                    ("m2", ["model", "delete", "m2", "--provider-id", "p1", "--yes"]),
                ],
            )

        self.assertEqual(
            calls,
            [
                ["model", "delete", "m1", "--provider-id", "p1", "--yes"],
                ["model", "delete", "m2", "--provider-id", "p1", "--yes"],
            ],
        )
        text = output.getvalue()
        self.assertIn("[m1]", text)
        self.assertIn("[m2]", text)
        self.assertIn("批量删除完成：成功 2 / 失败 0", text)

    def test_render_default_model_selector_shows_current_model(self):
        text = render_default_model_selector(
            "zh",
            ["model-max", "model-coder-plus", "gpt-4o"],
            "model coder",
            0,
            "model-coder-plus",
            color=False,
        )

        self.assertIn("选择默认模型", text)
        self.assertIn("筛选: model coder", text)
        self.assertIn("› model-coder-plus (当前)", text)
        self.assertIn("Enter/Space 填入模型 ID", text)
        self.assertNotIn("gpt-4o", text)

    def test_default_model_selector_returns_current_model_on_enter(self):
        with (
            patch("tui._clear_screen"),
            patch("tui._write_focus_form_update", return_value=0),
            patch("tui._read_key", return_value="\n"),
        ):
            selected = _run_default_model_selector("zh", ["model-max", "model-coder-plus"], "model-coder-plus")

        self.assertEqual(selected, "model-coder-plus")

    def test_catalog_model_choices_reads_model_store(self):
        class FakeModelStore:
            def list_models(self):
                return [
                    ModelInfo("model-max", "Model Max", 128000),
                    ModelInfo("model-coder-plus", "model coder Plus", 128000),
                ]

        with patch("tui.ModelStore", FakeModelStore, create=True):
            self.assertEqual(_catalog_model_choices(), ["model-max", "model-coder-plus"])

    def test_selected_models_summary_truncates_long_lists(self):
        self.assertEqual(
            _selected_models_summary("zh", ["m1", "m2", "m3", "m4"]),
            "已选 4: m1, m2, m3, ... +1",
        )

    def test_render_model_health_check_shows_results(self):
        text = render_model_health_check(
            "zh",
            "example-provider",
            ["model-max", "bad-model"],
            [
                ModelHealthResult("model-max", True, "responses"),
                ModelHealthResult("bad-model", False, "model_not_found"),
            ],
            color=False,
        )

        self.assertIn("模型健康检测 - example-provider", text)
        self.assertIn("[OK]  model-max  responses", text)
        self.assertIn("[FAIL] bad-model  model_not_found", text)
        self.assertIn("通过 1 / 失败 1", text)
        self.assertIn("Enter 加入通过模型", text)

    def test_model_health_check_page_writes_crlf_in_raw_mode(self):
        writes = []

        with (
            patch("tui._clear_screen"),
            patch("tui.write_text", side_effect=writes.append),
            patch("tui._check_model_health", return_value=ModelHealthResult("model-max", True, "responses")),
            patch("tui._read_key", return_value="\n"),
        ):
            selected = _run_model_health_check("zh", "example-provider", ["model-max"])

        self.assertEqual(selected, ["model-max"])
        output = "".join(writes)
        self.assertIn("\r\n", output)
        for index, char in enumerate(output):
            if char == "\n":
                self.assertGreater(index, 0)
                self.assertEqual(output[index - 1], "\r")

    def test_check_model_health_uses_responses(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"id": "resp-1"}).encode()

        with (
            patch("tui._current_provider_model_api", return_value=("http://example.test/v1", "sk-test")),
            patch("tui.urlopen", return_value=FakeResponse()) as fake_urlopen,
        ):
            result = _check_model_health("example-provider", "model-max")

        request = fake_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, _responses_endpoint("http://example.test/v1"))
        self.assertEqual(payload["model"], "model-max")
        self.assertTrue(result.ok)
        self.assertEqual(result.detail, "responses")

    def test_check_model_health_from_api_uses_form_credentials(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"id": "resp-1"}).encode()

        with patch("tui.urlopen", return_value=FakeResponse()) as fake_urlopen:
            result = _check_model_health_from_api("http://example.test/v1", "sk-form", "model-max")

        request = fake_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, _responses_endpoint("http://example.test/v1"))
        self.assertEqual(request.headers["Authorization"], "Bearer sk-form")
        self.assertTrue(result.ok)
        self.assertEqual(result.detail, "responses")

    def test_check_model_health_falls_back_to_chat_completions(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"id": "chatcmpl-1"}).encode()

        calls = []

        def fake_urlopen(request, timeout):
            calls.append(request.full_url)
            if request.full_url == _responses_endpoint("http://example.test/v1"):
                raise HTTPError(request.full_url, 404, "Not Found", None, None)
            return FakeResponse()

        with (
            patch("tui._current_provider_model_api", return_value=("http://example.test/v1", "sk-test")),
            patch("tui.urlopen", side_effect=fake_urlopen),
        ):
            result = _check_model_health("example-provider", "model-max")

        self.assertEqual(
            calls,
            [
                _responses_endpoint("http://example.test/v1"),
                _chat_completions_endpoint("http://example.test/v1"),
            ],
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.detail, "chat/completions")

    def test_provider_default_model_health_signature_invalidates_on_change(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"id": "resp-1"}).encode()

        values = {
            "base_url": "http://example.test/v1",
            "api_key": "sk-form",
            "default_model": "model-max",
        }

        with patch("tui.urlopen", return_value=FakeResponse()):
            result = _check_provider_default_model_health(values)

        self.assertTrue(result.ok)
        self.assertTrue(_provider_default_health_matches(values))
        values["default_model"] = "other-model"
        self.assertFalse(_provider_default_health_matches(values))
        _clear_provider_default_health(values)
        self.assertFalse(_provider_default_health_matches(values))

    def test_bulk_model_ids_round_trip(self):
        values = {}

        _set_bulk_model_ids(values, ["m1", "m2"])

        self.assertEqual(_bulk_model_ids(values), ["m1", "m2"])

    def test_bulk_model_add_command_does_not_set_default_or_profile(self):
        values = {
            "display_name": "",
            "context_window": "128000",
            "provider_id": "example-provider",
            "profile_name": "shared-profile",
            "make_default": "yes",
        }

        argv = _build_model_add_argv(values, "m1", allow_default=False, allow_profile=False)

        self.assertEqual(
            argv,
            ["model", "add", "m1", "--display-name", "m1", "--context-window", "128000", "--provider-id", "example-provider", "--yes"],
        )

    def test_bulk_output_skips_existing_models_without_calling_main(self):
        calls = []
        output = io.StringIO()

        with (
            patch("tui.main", side_effect=lambda argv: calls.append(argv) or 0),
            patch("tui._is_interactive_terminal", return_value=False),
            patch("tui._pause_after_output"),
            contextlib.redirect_stdout(output),
        ):
            _run_bulk_output_commands(
                "zh",
                "批量新增模型",
                [
                    ("existing-model", None),
                    ("new-model", ["model", "add", "new-model"]),
                ],
            )

        self.assertEqual(calls, [["model", "add", "new-model"]])
        text = output.getvalue()
        self.assertIn("[existing-model]", text)
        self.assertIn("已存在，跳过", text)
        self.assertIn("批量新增完成：成功 1 / 跳过 1 / 失败 0", text)

    def test_existing_model_ids_combines_catalog_and_provider(self):
        with (
            patch("tui._catalog_model_ids", return_value={"catalog-model", "shared-model"}),
            patch("tui._provider_model_ids", return_value={"provider-model", "shared-model"}),
        ):
            self.assertEqual(
                _existing_model_ids("example-provider"),
                {"catalog-model", "provider-model", "shared-model"},
            )

    def test_model_selector_hint_returns_when_model_field_regains_focus(self):
        fields = [
            FormField("model_id", "模型 ID", required=True),
            FormField("display_name", "显示名称"),
        ]

        self.assertEqual(_model_selector_hint_lines(True, fields, 1, "zh"), [])
        self.assertEqual(
            _model_selector_hint_lines(True, fields, 0, "zh"),
            ["模型 ID 字段可按 Ctrl+L 或 Space 从供应商拉取并选择模型。"],
        )
        self.assertEqual(
            _model_selector_hint_lines(False, fields, 0, "zh", catalog_model_selector=True),
            ["模型 ID 字段可按 Ctrl+L 或 Space 从已加入模型选择。"],
        )
        self.assertEqual(
            _model_selector_hint_lines(False, fields, 0, "zh", model_delete_selector=True),
            ["模型 ID 字段可按 Ctrl+L 或 Space 从当前供应商已加入模型选择，支持多选和 Ctrl+A 全选/清空。"],
        )

    def test_provider_default_model_hint_returns_on_default_model_field(self):
        fields = [
            FormField("base_url", "Base URL", required=True),
            FormField("api_key", "API Key", required=True, secret=True),
            FormField("default_model", "默认模型", required=True),
        ]

        self.assertEqual(
            _model_selector_hint_lines(False, fields, 2, "zh", provider_default_model_selector=True),
            ["默认模型字段可按 Ctrl+L 或 Space 从当前 Base URL 拉取模型；手动输入会在执行前检测健康。"],
        )
        self.assertEqual(_model_selector_hint_lines(False, fields, 0, "zh", provider_default_model_selector=True), [])

    def test_fetch_provider_models_parses_openai_response(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"data": [{"id": "model-max"}, {"id": "model-coder"}]}).encode()

        with (
            patch("tui._current_provider_model_api", return_value=("http://example.test/v1", "sk-test")),
            patch("tui.urlopen", return_value=FakeResponse()) as fake_urlopen,
        ):
            models = _fetch_provider_models("example-provider")

        self.assertEqual(models, ["model-coder", "model-max"])
        self.assertEqual(fake_urlopen.call_args.args[0].full_url, _models_endpoint("http://example.test/v1"))

    def test_provider_model_choices_reads_provider_catalog_order(self):
        calls = []

        class FakeProviderManager:
            def load_settings(self, provider_id):
                calls.append(provider_id)
                return {
                    "modelCatalog": {
                        "models": [
                            {"model": "model-b"},
                            {"model": "model-a"},
                            {"model": "model-b"},
                        ]
                    }
                }

        with patch("tui.ProviderManager", FakeProviderManager):
            self.assertEqual(_provider_model_choices("example-provider"), ["model-b", "model-a"])
        self.assertEqual(calls, ["example-provider"])

    def test_fetch_models_from_api_uses_form_credentials(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"data": [{"id": "model-max"}, {"id": "model-coder"}]}).encode()

        with patch("tui.urlopen", return_value=FakeResponse()) as fake_urlopen:
            models = _fetch_models_from_api("http://example.test/v1", "sk-form")

        request = fake_urlopen.call_args.args[0]
        self.assertEqual(models, ["model-coder", "model-max"])
        self.assertEqual(request.full_url, _models_endpoint("http://example.test/v1"))
        self.assertEqual(request.headers["Authorization"], "Bearer sk-form")

    def test_raw_newlines_use_crlf_for_raw_terminal_mode(self):
        self.assertEqual(_raw_newlines("a\nb\r\nc\rd"), "a\r\nb\r\nc\r\nd")

    def test_focus_form_redraw_does_not_clear_whole_screen_after_first_draw(self):
        fields = [FormField("provider_id", "供应商 ID", required=True)]
        values = {"provider_id": "freellmapi"}
        writes = []

        with patch("tui.write_text", side_effect=writes.append):
            previous_line_count = _redraw_focus_form(
                "新增供应商",
                fields,
                values,
                0,
                "zh",
                (),
                (),
                "example-provider",
                "m1",
                first_draw=True,
            )
            writes.clear()
            _redraw_focus_form(
                "新增供应商",
                fields,
                values,
                0,
                "zh",
                (),
                (),
                "example-provider",
                "m1",
                previous_line_count=previous_line_count,
            )

        output = "".join(writes)
        self.assertNotIn("\033[2J", output)
        self.assertNotIn("\033[J", output)
        self.assertIn("\033[?25l", output)
        self.assertIn("\033[K", output)
        self.assertRegex(output, "\033\\[[0-9]+;[0-9]+H\033\\[\\?25h\\Z")

    def test_output_command_page_clears_and_pauses_in_tty(self):
        writes = []
        calls = []

        with (
            patch("tui._is_interactive_terminal", return_value=True),
            patch("tui._supports_color", return_value=False),
            patch("tui.write_text", side_effect=writes.append),
            patch("tui.main", side_effect=lambda argv: calls.append(argv)),
            patch("builtins.input", return_value=""),
        ):
            _run_output_command("zh", "环境检查", ["doctor"])

        output = "".join(writes)
        self.assertIn("\033[2J", output)
        self.assertIn("Codex Model Admin / 环境检查", output)
        self.assertIn("输出结果", output)
        self.assertIn("按 Enter 或 Esc 返回菜单", output)
        self.assertEqual(calls, [["doctor"]])

    def test_escape_sequence_helpers_recognize_focus_keys(self):
        self.assertTrue(_escape_sequence_complete("\x1b[Z"))
        self.assertTrue(_escape_sequence_complete("\x1b[1;2Z"))
        self.assertTrue(_is_previous_focus_key("\x1b[Z"))
        self.assertTrue(_is_previous_focus_key("\x1b[1;2Z"))
        self.assertTrue(_is_previous_focus_key("\x1b[A"))
        self.assertTrue(_is_next_focus_key("\x1b[B"))
        self.assertTrue(_is_right_key("\x1b[C"))
        self.assertFalse(_is_next_focus_key("\x1b[Z"))

    def test_utf8_expected_length_handles_ascii_and_chinese_lead_bytes(self):
        self.assertEqual(_utf8_expected_length(ord("a")), 1)
        self.assertEqual(_utf8_expected_length("中".encode("utf-8")[0]), 3)

    def test_menu_status_reads_current_provider_and_default_model_id(self):
        class FakeProviderStore:
            def list_providers(self):
                return [
                    ProviderInfo("other-provider", "Other", "", "other-model", "", 1, False),
                    ProviderInfo("example-provider", "example-provider", "", "m1", "", 1, True),
                ]

        with (
            patch("tui.ProviderStore", FakeProviderStore, create=True),
            patch("tui._codex_config_model", return_value=""),
        ):
            provider_status, model_status = _menu_status()

        self.assertEqual(provider_status, "example-provider")
        self.assertEqual(model_status, "m1")

    def test_menu_status_prefers_codex_config_model_over_provider_default(self):
        class FakeProviderStore:
            def list_providers(self):
                return [
                    ProviderInfo("example-provider", "example-provider", "", "old-provider-model", "", 1, True),
                ]

        with (
            patch("tui.ProviderStore", FakeProviderStore, create=True),
            patch("tui._codex_config_model", return_value="current-codex-model"),
        ):
            provider_status, model_status = _menu_status()

        self.assertEqual(provider_status, "example-provider")
        self.assertEqual(model_status, "current-codex-model")

    def test_switchable_provider_choices_excludes_read_only_provider(self):
        class FakeProviderStore:
            def list_providers(self):
                return [
                    ProviderInfo("example-provider", "example-provider", "", "m1", "", 1, True),
                    ProviderInfo("codex-official", "OpenAI Official", "", "", "", None, False, read_only=True),
                    ProviderInfo("商汤", "商汤", "https://example.test/v1", "glm-5.2", "", 1, False),
                ]

        with patch("tui.ProviderStore", FakeProviderStore, create=True):
            self.assertEqual(_switchable_provider_choices(), ("example-provider", "商汤"))

    def test_provider_switch_form_uses_switchable_provider_choices(self):
        class FakeProviderStore:
            def list_providers(self):
                return [
                    ProviderInfo("example-provider", "example-provider", "", "m1", "", 1, True),
                    ProviderInfo("codex-official", "OpenAI Official", "", "", "", None, False, read_only=True),
                    ProviderInfo("商汤", "商汤", "https://example.test/v1", "glm-5.2", "", 1, False),
                ]

        captured = {}

        def fake_run_form(language, title, fields, build, preview_lines=(), **kwargs):
            captured["fields"] = fields

        with (
            patch("tui.ProviderStore", FakeProviderStore, create=True),
            patch("tui._run_form", side_effect=fake_run_form),
        ):
            _provider_switch("zh")

        provider_field = captured["fields"][0]
        self.assertEqual(provider_field.key, "provider_id")
        self.assertEqual(provider_field.default, "example-provider")
        self.assertEqual(provider_field.choices, ("example-provider", "商汤"))

    def test_provider_update_form_loads_selected_provider_values(self):
        settings_by_provider = {
            "p1": {
                "name": "Provider 1",
                "auth": {"OPENAI_API_KEY": "sk-one"},
                "config": 'model = "m1"\nbase_url = "http://one/v1"\nwire_api = "responses"\n',
                "modelCatalog": {"models": [{"model": "m1", "displayName": "M1", "contextWindow": 128000}]},
            },
            "p2": {
                "name": "Provider 2",
                "auth": {"OPENAI_API_KEY": "sk-two"},
                "config": 'model = "m2"\nbase_url = "http://two/v1"\nwire_api = "chat"\n',
                "modelCatalog": {"models": [{"model": "m2", "displayName": "M2", "contextWindow": 64000}]},
            },
        }

        class FakeProviderStore:
            def list_providers(self):
                return [
                    ProviderInfo("p1", "Provider 1", "http://one/v1", "m1", "responses", 1, True),
                    ProviderInfo("p2", "Provider 2", "http://two/v1", "m2", "chat", 1, False),
                ]

        class FakeProviderManager:
            def __init__(self, *args, **kwargs):
                pass

            def load_settings(self, provider_id):
                return settings_by_provider[provider_id]

        captured = {}

        def fake_run_form(language, title, fields, build, preview_lines=(), **kwargs):
            captured["fields"] = fields
            captured["build"] = build
            captured["on_change"] = kwargs["on_change"]

        with (
            patch("tui.ProviderStore", FakeProviderStore, create=True),
            patch("tui.ProviderManager", FakeProviderManager, create=True),
            patch("tui._run_form", side_effect=fake_run_form),
        ):
            _provider_update("zh")
            fields = captured["fields"]
            self.assertEqual(fields[0].choices, ("p1", "p2"))
            self.assertEqual(fields[1].key, "new_provider_id")
            self.assertEqual(fields[1].default, "p1")
            self.assertEqual(fields[2].default, "Provider 1")
            self.assertEqual(fields[3].default, "http://one/v1")
            self.assertEqual(fields[4].default, "sk-one")
            self.assertEqual(fields[5].default, "m1")
            self.assertEqual(fields[6].default, "128000")
            self.assertEqual(fields[7].default, "responses")

            values = {field.key: field.default for field in fields}
            values["provider_id"] = "p2"
            captured["on_change"](values, fields[0])

            self.assertEqual(values["new_provider_id"], "p2")
            self.assertEqual(values["provider_name"], "Provider 2")
            self.assertEqual(values["base_url"], "http://two/v1")
            self.assertEqual(values["api_key"], "sk-two")
            self.assertEqual(values["default_model"], "m2")
            self.assertEqual(values["context_window"], "64000")
            self.assertEqual(values["api_format"], "chat")
            values["new_provider_id"] = "p2-renamed"
            self.assertEqual(
                captured["build"](values),
                [
                    "provider",
                    "update",
                    "p2",
                    "--new-id",
                    "p2-renamed",
                    "--name",
                    "Provider 2",
                    "--base-url",
                    "http://two/v1",
                    "--api-key",
                    "sk-two",
                    "--default-model",
                    "m2",
                    "--context-window",
                    "64000",
                    "--api-format",
                    "chat",
                    "--yes",
                    "--sync-current",
                    "--restart",
                ],
            )

    def test_render_menu_supports_english(self):
        text = render_menu(width=86, color=False, language="en")

        self.assertIn("Environment / Install", text)
        self.assertIn("Providers", text)
        self.assertIn("Update Provider", text)
        self.assertIn("Models", text)
        self.assertIn("Proxy", text)
        self.assertIn("Backups", text)
        self.assertIn("Delete Backups", text)
        self.assertIn("Settings", text)
        self.assertIn("Language (界面语言)", text)
        self.assertIn("[Read]", text)
        self.assertIn("[Write]", text)
        self.assertIn("› Select action", text)
        self.assertNotIn("供应商", text)
        self.assertNotIn("模型列表", text)

    def test_run_tui_prints_redesigned_menu_before_prompt(self):
        output = io.StringIO()

        with patch("builtins.input", return_value="q"), contextlib.redirect_stdout(output):
            code = run_tui()

        self.assertEqual(code, 0)
        self.assertIn(">_ Codex Model Admin", output.getvalue())
        self.assertIn("Tab/↑↓ 选择", output.getvalue())

    def test_run_tui_can_switch_and_persist_language(self):
        output = io.StringIO()
        answers = iter(["21", "1", "en", "s", "q"])
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            with (
                patch.dict(os.environ, {"CODEX_MODEL_ADMIN_SETTINGS": str(settings_path)}),
                patch("builtins.input", side_effect=lambda: next(answers)),
                contextlib.redirect_stdout(output),
            ):
                code = run_tui()
            data = json.loads(settings_path.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(data["language"], "en")
        self.assertIn("界面语言 (Language)", output.getvalue())
        self.assertIn("请选择界面语言 / Select language", output.getvalue())
        self.assertIn("Language (界面语言)", output.getvalue())
        self.assertIn("Language switched to English", output.getvalue())

    def test_cli_tui_lang_option_starts_in_english_without_persisting(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            with (
                patch.dict(os.environ, {"CODEX_MODEL_ADMIN_SETTINGS": str(settings_path)}),
                patch("builtins.input", return_value="q"),
                contextlib.redirect_stdout(output),
            ):
                code = main(["tui", "--lang", "en"])

        self.assertEqual(code, 0)
        self.assertIn("Environment / Install", output.getvalue())
        self.assertFalse(settings_path.exists())

    def test_ctrl_c_at_main_prompt_exits_without_traceback(self):
        output = io.StringIO()

        with patch("builtins.input", side_effect=KeyboardInterrupt), contextlib.redirect_stdout(output):
            code = run_tui()

        self.assertEqual(code, 130)
        self.assertIn("已退出", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_ctrl_c_inside_action_exits_without_traceback(self):
        output = io.StringIO()
        answers = iter(["4"])

        def fake_input():
            try:
                return next(answers)
            except StopIteration:
                raise KeyboardInterrupt

        with patch("builtins.input", side_effect=fake_input), contextlib.redirect_stdout(output):
            code = run_tui()

        self.assertEqual(code, 130)
        self.assertIn("已退出", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_model_add_defaults_to_current_provider(self):
        output = io.StringIO()
        calls = []
        answers = iter(["9", "1", "m2", "s", "yes", "q"])

        class FakeProviderStore:
            def list_providers(self):
                return [
                    ProviderInfo("other-provider", "other", "", "", "", 0, False),
                    ProviderInfo("current-provider", "current", "", "", "", 0, True),
                ]

        def fake_main(argv):
            calls.append(argv)
            return 0

        with (
            patch("builtins.input", side_effect=lambda: next(answers)),
            patch("tui.ProviderStore", FakeProviderStore, create=True),
            patch("tui.main", fake_main),
            contextlib.redirect_stdout(output),
        ):
            code = run_tui()

        self.assertEqual(code, 0)
        self.assertIn(
            [
                "model",
                "add",
                "m2",
                "--display-name",
                "m2",
                "--context-window",
                "128000",
                "--provider-id",
                "current-provider",
                "--yes",
            ],
            calls,
        )

    def test_form_preview_then_execute_provider_switch_without_restart(self):
        output = io.StringIO()
        calls = []
        answers = iter(["6", "1", "p2", "2", "no", "p", "s", "yes", "q"])

        def fake_main(argv):
            calls.append(argv)
            return 0

        class FakeProviderStore:
            def list_providers(self):
                return [
                    ProviderInfo("p1", "Provider 1", "", "m1", "", 1, True),
                    ProviderInfo("p2", "Provider 2", "", "m2", "", 1, False),
                ]

        with (
            patch("builtins.input", side_effect=lambda: next(answers)),
            patch("tui.main", fake_main),
            patch("tui.ProviderStore", FakeProviderStore, create=True),
            contextlib.redirect_stdout(output),
        ):
            code = run_tui()

        self.assertEqual(code, 0)
        self.assertIn("预览", output.getvalue())
        self.assertIn(["provider", "switch", "p2", "--yes", "--no-restart"], calls)

    def test_colored_menu_keeps_visible_lines_within_frame_width(self):
        text = render_menu(width=86, color=True)
        visible = re.sub(r"\x1b\[[0-9;]*m", "", text)

        for line in visible.splitlines():
            self.assertLessEqual(_display_width(line), 86, line)


if __name__ == "__main__":
    unittest.main()
