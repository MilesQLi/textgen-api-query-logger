import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).parents[1] / "script.py"


def load_script(user_data_dir):
    fake_gradio = types.ModuleType("gradio")
    fake_gradio.update = lambda **kwargs: kwargs

    fake_chat = types.ModuleType("modules.chat")
    fake_chat.generate_chat_prompt = lambda user_input, state, **kwargs: f"rendered::{user_input}"

    fake_shared = types.ModuleType("modules.shared")
    fake_shared.user_data_dir = Path(user_data_dir)
    fake_shared.model_name = "test-model"

    fake_modules = types.ModuleType("modules")
    fake_modules.chat = fake_chat
    fake_modules.shared = fake_shared

    module_name = f"api_query_logger_test_{id(user_data_dir)}"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {"gradio": fake_gradio, "modules": fake_modules, "modules.chat": fake_chat, "modules.shared": fake_shared},
    ):
        spec.loader.exec_module(module)
    return module


class ApiQueryLogTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.script = load_script(self.temp_dir.name)

    def test_append_reload_bounds_memory_and_skips_malformed_lines(self):
        path = Path(self.temp_dir.name) / "prompt.jsonl"
        log = self.script.ApiQueryLog(path, max_entries=2)
        for number in range(3):
            log.append({"id": str(number), "user_input": f"query {number}"})

        with path.open("a", encoding="utf-8") as handle:
            handle.write("not-json\n")

        reloaded = self.script.ApiQueryLog(path, max_entries=2)
        self.assertEqual([entry["id"] for entry in reloaded.entries()], ["2", "1"])
        self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 4)

    def test_chat_hook_records_rendered_prompt_and_tools(self):
        self.script.setup()
        state = {
            "mode": "instruct",
            "tools": [{"type": "function", "function": {"name": "weather"}}],
            "selected_tools": ["weather"],
        }

        result = self.script.custom_generate_chat_prompt("Toronto?", state)
        entry = self.script._get_log().entries()[0]

        self.assertEqual(result, "rendered::Toronto?")
        self.assertEqual(entry["user_input"], "Toronto?")
        self.assertEqual(entry["rendered_prompt"], result)
        self.assertEqual(entry["tools"][0]["function"]["name"], "weather")
        self.assertEqual(entry["model"], "test-model")
        persisted = json.loads(self.script._get_log().path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["id"], entry["id"])

    def test_completion_hook_does_not_duplicate_chat_entries(self):
        self.script.setup()
        state = {"mode": "default"}

        self.assertEqual(self.script.input_modifier("plain prompt", state), "plain prompt")
        self.assertEqual(self.script.input_modifier("chat input", state, is_chat=True), "chat input")

        entries = self.script._get_log().entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["source"], "completion")

    def test_output_hook_records_output_and_returns_it_unchanged(self):
        self.script.setup()
        state = {"mode": "instruct"}
        self.script.custom_generate_chat_prompt("hello", state)

        self.assertEqual(self.script.output_modifier("model reply", state, is_chat=True), "model reply")
        entry = self.script._get_log().entries()[0]
        self.assertEqual(entry["output"], "model reply")
        self.assertNotIn(self.script._STATE_ENTRY_ID, state)

        reloaded = self.script.ApiQueryLog(self.script._get_log().path)
        self.assertEqual(len(reloaded.entries()), 1)
        self.assertEqual(reloaded.entries()[0]["output"], "model reply")

    def test_details_decode_ui_html_escaping_in_output(self):
        entry = {
            "id": "escaped-output",
            "output": "&lt;think&gt;Tom &amp; Jerry&#x27;s plan&lt;/think&gt;",
        }

        self.assertEqual(
            self.script._details(entry)[2],
            "<think>Tom & Jerry's plan</think>",
        )

    def test_refresh_select_and_clear(self):
        self.script.setup()
        first = self.script._record("first", "rendered first", {}, "chat")
        second = self.script._record("second", "rendered second", {}, "chat")

        dropdown, raw, rendered, output, tools, metadata = self.script.refresh_log(first["id"])
        self.assertEqual(dropdown["value"], first["id"])
        self.assertEqual(raw, "first")
        self.assertEqual(rendered, "rendered first")
        self.assertEqual(output, "")
        self.assertEqual(len(dropdown["choices"]), 2)

        self.assertEqual(self.script.select_entry(second["id"])[0], "second")
        cleared = self.script.clear_log()
        self.assertEqual(cleared[0]["choices"], [])
        self.assertFalse(self.script._get_log().path.exists())


if __name__ == "__main__":
    unittest.main()
