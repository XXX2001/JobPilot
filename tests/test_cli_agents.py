"""CLI generation stays offline: every subprocess runs a temporary fake executable."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import BaseModel, SecretStr

from backend.config import Settings, settings
from backend.llm import factory
from backend.llm.base import LLMCallFailed, LLMClient, LLMJSONError
from backend.llm.providers.cli_agents import ClaudeCLIClient, CodexCLIClient


class Answer(BaseModel):
    answer: int


class Detail(BaseModel):
    value: int = 4


class NestedAnswer(BaseModel):
    details: list[Detail] | None = None


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    capture = tmp_path / "capture.json"

    def write(name, body):
        executable = tmp_path / name
        executable.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys, time\n"
            "from pathlib import Path\n"
            "args = sys.argv[1:]\n"
            "record = {'argv': args, 'stdin': sys.stdin.read(), 'cwd': os.getcwd(), "
            "'pid': os.getpid(), 'sid': os.getsid(0), 'pgid': os.getpgrp(), "
            "'env': {key: os.environ.get(key) for key in "
            "('PYTHONPATH', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'LLM_API_KEY', "
            "'BROWSER_LLM_API_KEY', 'HOME', 'CODEX_HOME')}}\n"
            "if '--output-schema' in args:\n"
            "    schema_path = Path(args[args.index('--output-schema') + 1])\n"
            "    record['schema'] = json.loads(schema_path.read_text())\n"
            f"capture = Path({str(capture)!r})\n"
            "capture.write_text(json.dumps(record))\n" + body,
            encoding="utf-8",
        )
        executable.chmod(0o755)
        return capture

    return write


def _output(name, value):
    if name == "claude":
        return f"print({value!r})\n"
    return f"Path(args[args.index('-o') + 1]).write_text({value!r})\n"


def _no_keys(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "auto")
    monkeypatch.setattr(settings, "BROWSER_LLM_PROVIDER", "openai")
    for name in ("LLM_BASE_URL", "BROWSER_LLM_BASE_URL"):
        monkeypatch.setattr(settings, name, "")
    for name in ("LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "BROWSER_LLM_API_KEY"):
        monkeypatch.setattr(settings, name, SecretStr(""))


async def test_claude_generate_text_reads_result(fake_cli):
    capture = fake_cli(
        "claude",
        _output(
            "claude", '{"type":"result","subtype":"success","is_error":false,"result":"hello"}'
        ),
    )
    client = ClaudeCLIClient()
    assert isinstance(client, LLMClient)
    assert await client.generate_text("hello\nfrom stdin") == "hello"
    record = json.loads(capture.read_text())
    args = record["argv"]
    assert "-p" in args
    for flag, value in (
        ("--output-format", "json"),
        ("--tools", ""),
        ("--setting-sources", ""),
        ("--model", "sonnet"),
    ):
        assert args[args.index(flag) + 1] == value
    assert "--strict-mcp-config" in args
    assert "--no-session-persistence" in args
    assert record["stdin"] == "hello\nfrom stdin"


async def test_claude_generate_json_reads_structured_output(fake_cli):
    capture = fake_cli(
        "claude",
        _output("claude", '{"result":"","structured_output":{"answer":4},"is_error":false}'),
    )
    assert await ClaudeCLIClient().generate_json("answer", Answer) == Answer(answer=4)
    args = json.loads(capture.read_text())["argv"]
    assert json.loads(args[args.index("--json-schema") + 1]) == Answer.model_json_schema()


async def test_codex_generate_json_uses_output_schema_and_last_message(fake_cli):
    capture = fake_cli(
        "codex", _output("codex", '{"answer":4}') + "print('event stream, not the answer')\n"
    )
    client = CodexCLIClient()
    assert isinstance(client, LLMClient)
    assert await client.generate_json("answer", Answer) == Answer(answer=4)
    record = json.loads(capture.read_text())
    args = record["argv"]
    assert args[0] == "exec"
    assert "--json" in args
    assert "--skip-git-repo-check" in args
    assert "--ephemeral" in args
    assert args[args.index("-s") + 1] == "read-only"
    assert "--output-schema" in args
    assert "-o" in args
    assert args[-1] == "-"
    assert record["schema"] == {**Answer.model_json_schema(), "additionalProperties": False}
    directory = args[args.index("-C") + 1]
    assert record["cwd"] == directory
    assert not Path(directory).exists()  # workspace and output files are cleaned up


@pytest.mark.parametrize(
    "name,client_cls", [("claude", ClaudeCLIClient), ("codex", CodexCLIClient)]
)
async def test_generate_json_nested_schema_with_defaults(fake_cli, name, client_cls):
    answer = {"details": [{"value": 4}]}
    payload = {"structured_output": answer} if name == "claude" else answer
    capture = fake_cli(name, _output(name, json.dumps(payload)))
    assert await client_cls().generate_json("answer", NestedAnswer) == NestedAnswer(**answer)
    record = json.loads(capture.read_text())
    expected = NestedAnswer.model_json_schema()
    if name == "claude":
        actual = json.loads(record["argv"][record["argv"].index("--json-schema") + 1])
    else:
        actual = record["schema"]
        expected["additionalProperties"] = False
        expected["required"] = ["details"]
        del expected["properties"]["details"]["default"]
        detail = expected["$defs"]["Detail"]
        detail["additionalProperties"] = False
        detail["required"] = ["value"]
        del detail["properties"]["value"]["default"]
    assert actual == expected


async def test_codex_strict_schema_walks_inline_items_and_anyof_without_mutating_input(fake_cli):
    capture = fake_cli("codex", _output("codex", '{"answers":[]}'))
    schema = {
        "type": "object",
        "properties": {
            "answers": {
                "type": "array",
                "items": {
                    "anyOf": [
                        {"type": "null"},
                        {
                            "type": "object",
                            "additionalProperties": True,
                            "properties": {"default": {"type": "integer", "default": 4}},
                        },
                    ]
                },
            }
        },
    }
    original = deepcopy(schema)
    await CodexCLIClient().generate_text("answer", response_schema=schema)
    assert schema == original
    expected = deepcopy(original)
    expected.update(additionalProperties=False, required=["answers"])
    item = expected["properties"]["answers"]["items"]["anyOf"][1]
    item.update(additionalProperties=False, required=["default"])
    del item["properties"]["default"]["default"]
    assert json.loads(capture.read_text())["schema"] == expected


async def test_codex_isolates_config_tools_and_prompt(fake_cli):
    capture = fake_cli(
        "codex",
        "assert not list(Path.cwd().iterdir())\n" + _output("codex", "hello"),
    )
    await CodexCLIClient().generate_text("hello\nfrom stdin")
    record = json.loads(capture.read_text())
    args = record["argv"]
    assert "--ignore-user-config" in args
    assert {args[i + 1] for i, flag in enumerate(args) if flag == "--disable"} == {
        "browser_use",
        "browser_use_external",
        "computer_use",
        "in_app_browser",
        "apps",
    }
    assert args[args.index("-s") + 1] == "read-only"
    assert record["cwd"] == args[args.index("-C") + 1]
    assert record["stdin"] == (
        "Answer from the text below only. Do not run commands, read files or browse.\n"
        "hello\nfrom stdin"
    )


@pytest.mark.parametrize(
    "name,client_cls", [("claude", ClaudeCLIClient), ("codex", CodexCLIClient)]
)
async def test_generate_text_passes_response_schema(fake_cli, name, client_cls):
    payload = {"structured_output": {"answer": 4}} if name == "claude" else {"answer": 4}
    capture = fake_cli(name, _output(name, json.dumps(payload)))
    text = await client_cls().generate_text("answer", response_schema=Answer.model_json_schema())
    assert json.loads(text) == {"answer": 4}
    record = json.loads(capture.read_text())
    schema = record.get("schema")
    if name == "claude":
        schema = json.loads(record["argv"][record["argv"].index("--json-schema") + 1])
    expected = Answer.model_json_schema()
    if name == "codex":
        expected["additionalProperties"] = False
    assert schema == expected


async def test_codex_generate_text_uses_default_or_explicit_model(fake_cli):
    capture = fake_cli("codex", _output("codex", "hello"))
    assert await CodexCLIClient().generate_text("hello") == "hello"
    first = json.loads(capture.read_text())
    assert "--model" not in first["argv"]
    assert await CodexCLIClient(model="test-model").generate_text("hello") == "hello"
    second = json.loads(capture.read_text())
    assert second["argv"][second["argv"].index("--model") + 1] == "test-model"
    assert second["cwd"] != first["cwd"]


@pytest.mark.parametrize(
    "name,client_cls", [("claude", ClaudeCLIClient), ("codex", CodexCLIClient)]
)
async def test_cli_nonzero_exit_raises_llm_call_failed(fake_cli, name, client_cls):
    fake_cli(name, "print('not JSON'); sys.stderr.write('x' * 5000 + 'stderr tail'); sys.exit(2)\n")
    with pytest.raises(LLMCallFailed, match="stderr tail") as exc:
        await client_cls().generate_text("hello")
    assert len(str(exc.value)) < 2500


async def test_claude_nonzero_exit_prefers_stdout_error_result(fake_cli):
    fake_cli(
        "claude",
        'print(json.dumps({"is_error": True, "result": "subscription login required"}))\n'
        "sys.stderr.write('unhelpful stderr'); sys.exit(1)\n",
    )
    with pytest.raises(LLMCallFailed, match="subscription login required") as exc:
        await ClaudeCLIClient().generate_text("hello")
    assert "unhelpful stderr" not in str(exc.value)


@pytest.mark.parametrize(
    "events",
    [
        [
            {"type": "error", "message": "old error"},
            {"type": "turn.failed", "error": {"message": "final error"}},
        ],
        [
            {"type": "turn.failed", "error": {"message": "old error"}},
            {"type": "error", "message": "final error"},
        ],
        [{"type": "turn.failed", "message": "final error"}],
    ],
)
async def test_codex_nonzero_exit_prefers_last_stdout_error(fake_cli, events):
    stdout = "diagnostic, not JSON\n" + "\n".join(
        json.dumps(event) for event in [*events, {"type": "thread.started"}, []]
    )
    fake_cli("codex", f"print({stdout!r})\nsys.stderr.write('unhelpful stderr'); sys.exit(1)\n")
    with pytest.raises(LLMCallFailed, match="final error") as exc:
        await CodexCLIClient().generate_text("hello")
    assert "old error" not in str(exc.value)
    assert "unhelpful stderr" not in str(exc.value)


@pytest.mark.parametrize(
    "name,client_cls", [("claude", ClaudeCLIClient), ("codex", CodexCLIClient)]
)
@pytest.mark.parametrize("payload", ["not json", '{"answer":"invalid"}'])
async def test_cli_bad_json_raises_llm_json_error(fake_cli, name, client_cls, payload):
    if name == "claude" and payload.startswith("{"):
        payload = json.dumps({"structured_output": json.loads(payload)})
    fake_cli(name, _output(name, payload))
    with pytest.raises(LLMJSONError):
        await client_cls().generate_json("answer", Answer)


async def test_claude_error_result_raises_llm_call_failed(fake_cli):
    fake_cli("claude", _output("claude", '{"is_error":true,"result":"login required"}'))
    with pytest.raises(LLMCallFailed, match="login required"):
        await ClaudeCLIClient().generate_text("hello")


@pytest.mark.parametrize(
    "name,client_cls", [("claude", ClaudeCLIClient), ("codex", CodexCLIClient)]
)
async def test_child_env_has_no_pythonpath_or_api_keys(fake_cli, monkeypatch, name, client_cls):
    excluded = (
        "PYTHONPATH",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "LLM_API_KEY",
        "BROWSER_LLM_API_KEY",
    )
    for key in excluded:
        monkeypatch.setenv(key, "must-not-reach-child")
    payload = '{"result":"hello"}' if name == "claude" else "hello"
    capture = fake_cli(name, _output(name, payload))
    await client_cls().generate_text("hello")
    child_env = json.loads(capture.read_text())["env"]
    for key in excluded:
        assert child_env[key] is None
        assert os.environ[key] == "must-not-reach-child"
    for key in ("HOME", "CODEX_HOME"):
        assert child_env[key] == os.environ.get(key)


@pytest.mark.parametrize(
    "name,client_cls", [("claude", ClaudeCLIClient), ("codex", CodexCLIClient)]
)
async def test_cli_timeout_kills_child(fake_cli, name, client_cls):
    capture = fake_cli(name, "time.sleep(30)\n")
    with pytest.raises(LLMCallFailed, match="timed out"):
        await client_cls(timeout=0.5).generate_text("hello")
    pid = json.loads(capture.read_text())["pid"]
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.fixture
def cli_process_tree(fake_cli):
    def write(name):
        return fake_cli(
            name,
            "import subprocess\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "record['grandchild_pid'] = child.pid\n"
            "capture.write_text(json.dumps(record))\n"
            "time.sleep(60)\n",
        )

    return write


async def _wait_for_process_tree(capture):
    async with asyncio.timeout(5):
        while True:
            try:
                record = json.loads(capture.read_text())
                if "grandchild_pid" in record:
                    return record
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            await asyncio.sleep(0.01)


def _is_running(pid):
    # An orphan can remain a zombie until init reaps it; it no longer holds pipes.
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


async def _cleanup_process_tree(capture, task):
    # Keep the regression tests bounded and leak-free even against the broken implementation.
    if capture.exists():
        record = json.loads(capture.read_text())
        for key in ("grandchild_pid", "pid"):
            if key in record:
                try:
                    os.kill(record[key], signal.SIGKILL)
                except ProcessLookupError:
                    pass
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)


@pytest.mark.parametrize(
    "name,client_cls", [("claude", ClaudeCLIClient), ("codex", CodexCLIClient)]
)
async def test_cli_timeout_kills_group_with_grandchild_holding_stdout(
    cli_process_tree, name, client_cls
):
    capture = cli_process_tree(name)
    timeout = 1
    task = asyncio.create_task(client_cls(timeout=timeout).generate_text("hello"))
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout + 6)
        assert task in done, "CLI hung after its timeout with a grandchild holding stdout open"
        with pytest.raises(LLMCallFailed, match="timed out"):
            task.result()
        record = json.loads(capture.read_text())
        assert record["sid"] == record["pgid"] == record["pid"]
        assert not _is_running(record["pid"])
        assert not _is_running(record["grandchild_pid"])
    finally:
        await _cleanup_process_tree(capture, task)


@pytest.mark.parametrize(
    "name,client_cls", [("claude", ClaudeCLIClient), ("codex", CodexCLIClient)]
)
async def test_cli_cancellation_kills_process_group(cli_process_tree, name, client_cls):
    capture = cli_process_tree(name)
    task = asyncio.create_task(client_cls(timeout=60).generate_text("hello"))
    try:
        record = await _wait_for_process_tree(capture)
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=6)
        assert task in done, "CLI cancellation hung with a grandchild holding stdout open"
        with pytest.raises(asyncio.CancelledError):
            task.result()
        assert record["sid"] == record["pgid"] == record["pid"]
        assert not _is_running(record["pid"])
        assert not _is_running(record["grandchild_pid"])
    finally:
        await _cleanup_process_tree(capture, task)


@pytest.mark.parametrize("client_cls", [ClaudeCLIClient, CodexCLIClient])
async def test_missing_cli_raises_llm_call_failed(fake_cli, client_cls):
    with pytest.raises(LLMCallFailed):
        await client_cls().generate_text("hello")


def test_auto_provider_prefers_claude_then_codex_then_keys(fake_cli, monkeypatch, tmp_path):
    _no_keys(monkeypatch)
    fake_cli("claude", "")
    fake_cli("codex", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", SecretStr("test-key"))
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", SecretStr("test-key"))
    assert factory.resolve_llm_provider() == "claude"
    (tmp_path / "claude").unlink()
    assert factory.resolve_llm_provider() == "codex"
    (tmp_path / "codex").unlink()
    assert factory.resolve_llm_provider() == "openai"
    monkeypatch.setattr(settings, "OPENAI_API_KEY", SecretStr(""))
    assert factory.resolve_llm_provider() == "anthropic"
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", SecretStr(""))
    assert factory.resolve_llm_provider() == "none"
    assert not settings.writing_model_available()
    with pytest.raises(ValueError, match="^no writing model$"):
        factory.make_llm_client()


def test_startup_needs_no_key(fake_cli, monkeypatch, tmp_path):
    _no_keys(monkeypatch)
    assert Settings.model_fields["LLM_PROVIDER"].default == "auto"
    fake_cli("claude", "")
    assert settings.validate_runtime_config() == []
    assert settings.writing_model_available()
    assert settings.llm_configured()
    (tmp_path / "claude").unlink()
    assert settings.validate_runtime_config() == []
    assert not settings.llm_configured()


def test_lifespan_without_writing_model_keeps_services_available(fake_cli, monkeypatch, capfd):
    from starlette.testclient import TestClient

    from backend.main import app

    _no_keys(monkeypatch)
    with TestClient(app) as client:
        assert client.get("/api/health").json()["llm_key_set"] is False
        assert app.state.apply_engine is not None
        assert app.state.batch_runner is not None
        with pytest.raises(LLMCallFailed, match="no writing model"):
            asyncio.run(app.state.llm.generate_text("hello"))
    stderr = capfd.readouterr().err
    assert "no writing model" in stderr
    assert "Tier 2 disabled" in stderr


@pytest.mark.parametrize("mode", ["auto", "assisted"])
async def test_tier2_skips_unconfigured_browser_llm(monkeypatch, caplog, mode):
    from backend.applier import assisted_apply, auto_apply

    module = auto_apply if mode == "auto" else assisted_apply
    strategy_cls = (
        auto_apply.AutoApplyStrategy if mode == "auto" else assisted_apply.AssistedApplyStrategy
    )
    monkeypatch.setattr(module, "_BROWSER_USE_AVAILABLE", True)
    monkeypatch.setattr(module, "Agent", Mock())
    build_browser = Mock(side_effect=AssertionError("Tier 2 must not open a browser"))
    monkeypatch.setattr(module, "build_browser", build_browser)
    monkeypatch.setattr(
        factory, "make_browser_llm", Mock(side_effect=ValueError("no browser model"))
    )
    strategy = strategy_cls.__new__(strategy_cls)
    strategy._form_filler = None
    kwargs = {"job_id": 1} if mode == "auto" else {}
    result = await strategy.apply(apply_url="https://example.com/apply", **kwargs)
    assert result.status == "awaiting_user_submission"
    assert result.next_action_url == "https://example.com/apply"
    assert "Tier 2 disabled" in caplog.text
    build_browser.assert_not_called()
