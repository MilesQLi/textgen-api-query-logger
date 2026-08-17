"""Log and inspect text-generation-webui API queries and outputs."""

from __future__ import annotations

import html
import json
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import gradio as gr

from modules import chat, shared


params = {
    "display_name": "API Query Logger",
    "is_tab": True,
    "log_file": "logs/api_query_logger.jsonl",
    "max_entries": 200,
}


class ApiQueryLog:
    """Thread-safe JSONL query log with a bounded in-memory index."""

    def __init__(self, path: Path, max_entries: int = 200):
        self.path = Path(path)
        self.max_entries = max(1, int(max_entries))
        self._entries = deque(maxlen=self.max_entries)
        self._lock = threading.RLock()
        self._load()

    def _load(self):
        if not self.path.exists():
            return

        with self._lock:
            with self.path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        entry = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if isinstance(entry, dict) and entry.get("id"):
                        self._remember(entry)

    def _remember(self, entry: dict):
        """Keep only the newest snapshot for each generation in memory."""
        previous = next((item for item in self._entries if item["id"] == entry["id"]), None)
        if previous is not None:
            self._entries.remove(previous)
        self._entries.append(entry)

    def append(self, entry: dict):
        serialized = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized + "\n")
            self._remember(entry)

    def record_output(self, entry_id: str, output: str) -> bool:
        """Add an output snapshot for a previously recorded generation."""
        with self._lock:
            entry = next((item for item in reversed(self._entries) if item["id"] == entry_id), None)
            if entry is None:
                return False

            updated = {**entry, "output": str(output)}
            serialized = json.dumps(updated, ensure_ascii=False, separators=(",", ":"))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized + "\n")
            self._remember(updated)
            return True

    def entries(self) -> list[dict]:
        with self._lock:
            return list(reversed(self._entries))

    def find(self, entry_id: str | None) -> dict | None:
        with self._lock:
            return next((item for item in reversed(self._entries) if item["id"] == entry_id), None)

    def clear(self):
        with self._lock:
            self._entries.clear()
            if self.path.exists():
                self.path.unlink()


_query_log: ApiQueryLog | None = None
_STATE_ENTRY_ID = "_api_query_logger_entry_id"


def _log_path() -> Path:
    configured = Path(params["log_file"])
    return configured if configured.is_absolute() else shared.user_data_dir / configured


def setup():
    global _query_log
    _query_log = ApiQueryLog(_log_path(), params["max_entries"])


def _get_log() -> ApiQueryLog:
    global _query_log
    if _query_log is None:
        setup()
    return _query_log


def _record(user_input: str, rendered_prompt: str, state: dict, source: str):
    tools = state.get("tools") or []
    entry = {
        "id": uuid4().hex,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "mode": state.get("mode"),
        "model": getattr(shared, "model_name", None),
        "user_input": str(user_input),
        "rendered_prompt": str(rendered_prompt),
        "tools": tools,
        "selected_tools": state.get("selected_tools") or [],
    }
    _get_log().append(entry)
    return entry


def custom_generate_chat_prompt(user_input, state, **kwargs):
    """Render normally, then capture exactly what will be sent to generation."""
    rendered_prompt = chat.generate_chat_prompt(user_input, state, **kwargs)
    entry = _record(user_input, rendered_prompt, state, "chat")
    state[_STATE_ENTRY_ID] = entry["id"]
    return rendered_prompt


def input_modifier(text, state, is_chat=False):
    """Capture non-chat Default/Notebook prompts without changing them."""
    if not is_chat:
        entry = _record(text, text, state, "completion")
        state[_STATE_ENTRY_ID] = entry["id"]
    return text


def output_modifier(text, state, is_chat=False):
    """Attach the completed model output without changing what is returned."""
    entry_id = state.pop(_STATE_ENTRY_ID, None)
    if entry_id is not None:
        _get_log().record_output(entry_id, text)
    return text


def _label(entry: dict) -> str:
    timestamp = entry.get("timestamp", "unknown time").replace("T", " ")
    preview = entry.get("user_input", "").replace("\n", " ").strip()
    if len(preview) > 70:
        preview = preview[:67] + "..."
    return f"{timestamp} — {preview or '(empty input)'}"


def _details(entry: dict | None):
    if entry is None:
        return "", "", "", [], {}

    metadata = {
        "id": entry.get("id"),
        "timestamp": entry.get("timestamp"),
        "source": entry.get("source"),
        "mode": entry.get("mode"),
        "model": entry.get("model"),
        "selected_tools": entry.get("selected_tools", []),
    }
    return (
        entry.get("user_input", ""),
        entry.get("rendered_prompt", ""),
        html.unescape(entry.get("output", "")),
        entry.get("tools", []),
        metadata,
    )


def refresh_log(selected_id=None):
    entries = _get_log().entries()
    choices = [(_label(entry), entry["id"]) for entry in entries]
    ids = {entry["id"] for entry in entries}
    selected_id = selected_id if selected_id in ids else (entries[0]["id"] if entries else None)
    selected = next((entry for entry in entries if entry["id"] == selected_id), None)
    return (gr.update(choices=choices, value=selected_id), *_details(selected))


def select_entry(entry_id):
    return _details(_get_log().find(entry_id))


def clear_log():
    _get_log().clear()
    return gr.update(choices=[], value=None), "", "", "", [], {}


def ui():
    gr.Markdown(
        "Inspect the raw query, fully rendered prompt, and tool definitions available "
        "to each generation. Logs are stored as JSONL in `user_data/logs/api_query_logger.jsonl`."
    )
    with gr.Row():
        refresh = gr.Button("Refresh", variant="primary")
        clear = gr.Button("Clear log", variant="stop")

    entry = gr.Dropdown(label="Logged generation", choices=[], interactive=True)
    with gr.Tabs():
        with gr.Tab("Rendered prompt"):
            rendered_prompt = gr.Textbox(label="Prompt sent to the model", lines=24, interactive=False)
        with gr.Tab("Raw query"):
            user_input = gr.Textbox(label="User input", lines=12, interactive=False)
        with gr.Tab("Output"):
            output = gr.Textbox(label="Model output", lines=24, interactive=False)
        with gr.Tab("Available tools"):
            tools = gr.JSON(label="Tool definitions")
        with gr.Tab("Metadata"):
            metadata = gr.JSON(label="Generation metadata")

    details = [user_input, rendered_prompt, output, tools, metadata]
    refresh.click(refresh_log, inputs=entry, outputs=[entry, *details], show_progress=False)
    entry.change(select_entry, inputs=entry, outputs=details, show_progress=False)
    clear.click(clear_log, outputs=[entry, *details], show_progress=False)
