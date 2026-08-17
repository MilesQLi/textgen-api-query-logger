# API Query Logger

API Query Logger is an extension for text-generation-webui that records and displays:

- the raw user query;
- the fully rendered prompt sent to text generation;
- the model output;
- the tool definitions available to the model;
- the selected tools, model, mode, source, and UTC timestamp.

Records are appended as JSON Lines to `user_data/logs/api_query_logger.jsonl`. A completed generation appends a second snapshot with the same ID and its output; the UI coalesces snapshots so each generation appears once. The in-memory browser is bounded to the newest 200 generations by default, while the JSONL file remains an append-only audit log until **Clear log** is pressed.

## Install and run

Place this directory at `extensions/api_query_logger`, then start text-generation-webui with:

```text
python server.py --extensions api_query_logger
```

Open the **API Query Logger** tab and press **Refresh** after a generation. Select an entry to inspect its rendered prompt, raw query, model output, available tool schemas, and metadata.

The extension uses `custom_generate_chat_prompt` so it sees the final output of the active chat template, including any tools supplied to that template. text-generation-webui only runs the first extension that implements this hook, so list `api_query_logger` before another extension that replaces chat prompt rendering.

Default and Notebook prompts are recorded through `input_modifier`. If another extension changes those prompts, list `api_query_logger` after it to capture the modified text.

Outputs are captured through `output_modifier` after generation completes. The captured value reflects any output extensions that run before `api_query_logger`; the extension always returns it unchanged.
The Output tab decodes HTML entities added by the text-generation-webui chat display, so tags such as `<think>` are shown as generated rather than as `&lt;think&gt;`.

## Settings

The following keys can be added to `user_data/settings.yaml`:

```yaml
api_query_logger-log_file: logs/api_query_logger.jsonl
api_query_logger-max_entries: 200
```

`log_file` may be absolute or relative to `user_data`.

## Test

From the text-generation-webui repository root:

```text
python -m unittest discover -s extensions/api_query_logger/tests -v
```

## License

API Query Logger is licensed under the [GNU Affero General Public License v3.0](LICENSE) (`AGPL-3.0-only`).
