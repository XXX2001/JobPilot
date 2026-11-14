"""Text generation through the owner's logged-in Claude Code and Codex CLIs."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import tempfile
from pathlib import Path
from typing import Any, Type, TypeVar

from pydantic import BaseModel

from backend.config import settings
from backend.llm.base import LLMCallFailed, LLMClient, LLMJSONError, parse_json_response

T = TypeVar("T", bound=BaseModel)


def _strict_codex_schema(value: Any) -> Any:
    """Copy a schema into Codex's strict form, including referenced definitions."""
    if isinstance(value, list):
        return [_strict_codex_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, child in value.items():
        if key == "default":
            continue
        if key in {"properties", "$defs", "definitions", "patternProperties", "dependentSchemas"}:
            # These maps contain names, which may themselves be "default" or "type".
            result[key] = {name: _strict_codex_schema(node) for name, node in child.items()}
        else:
            result[key] = _strict_codex_schema(child)
    if result.get("type") == "object" or "properties" in result:
        result["additionalProperties"] = False
        result["required"] = list(result.get("properties", {}))
    return result


def _stdout_error(stdout: str) -> str | None:
    """Read Claude's result envelope or the last Codex error event."""
    try:
        result = json.loads(stdout)
    except ValueError:
        pass
    else:
        if isinstance(result, dict) and result.get("is_error") and result.get("result"):
            return str(result["result"])
    for line in reversed(stdout.splitlines()):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") not in {"error", "turn.failed"}:
            continue
        message = event.get("message")
        if not message and isinstance(event.get("error"), dict):
            message = event["error"].get("message")
        return str(message) if message else None
    return None


async def _run_cli(argv: list[str], prompt: str, timeout: float, *, cwd: str | None = None) -> str:
    excluded = {
        "PYTHONPATH",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "LLM_API_KEY",
        "BROWSER_LLM_API_KEY",
    }
    env = {key: value for key, value in os.environ.items() if key not in excluded}
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            start_new_session=True,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise LLMCallFailed(f"Could not start {argv[0]}: {exc}") from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(prompt.encode()), timeout)
    except (TimeoutError, asyncio.CancelledError) as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.communicate(), timeout=5)
        except TimeoutError:
            pass
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise LLMCallFailed("timed out") from exc
    if process.returncode:
        message = (
            _stdout_error(stdout.decode(errors="replace"))
            or stderr.decode(errors="replace")[-2000:]
        )
        raise LLMCallFailed(f"{argv[0]} exited with {process.returncode}: {message}")
    return stdout.decode(errors="replace")


class _CLIClient(LLMClient):
    async def generate_json(self, prompt: str, schema: Type[T]) -> T:
        text = await self.generate_text(
            prompt,
            response_mime_type="application/json",
            response_schema=schema.model_json_schema(),
        )
        try:
            return schema.model_validate(parse_json_response(text))
        except (ValueError, TypeError, IndexError) as exc:
            raise LLMJSONError(f"Invalid JSON from CLI: {exc}") from exc


class ClaudeCLIClient(_CLIClient):
    def __init__(
        self, model: str = "sonnet", timeout: float = settings.LLM_TIMEOUT_SECONDS
    ) -> None:
        self._model = model
        self._timeout = timeout

    async def generate_text(
        self,
        prompt: str,
        *,
        response_mime_type: str | None = None,
        response_schema: dict | None = None,
    ) -> str:
        argv = [
            "claude",
            "-p",
            "--output-format",
            "json",
            "--tools",
            "",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--model",
            self._model,
        ]
        if response_schema is not None:
            argv.extend(["--json-schema", json.dumps(response_schema)])
        elif response_mime_type == "application/json":
            prompt += "\n\nRespond with only a single valid JSON object, no prose or code fences."
        raw = await _run_cli(argv, prompt, self._timeout)
        try:
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("expected a result object")
            if result.get("is_error"):
                raise LLMCallFailed(str(result.get("result") or "Claude reported an error"))
            if response_schema is not None:
                return json.dumps(result["structured_output"])
            text = result["result"]
            if not isinstance(text, str):
                raise ValueError("expected a text result")
            return text
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMJSONError(f"Invalid JSON result from Claude: {exc}") from exc


class CodexCLIClient(_CLIClient):
    def __init__(self, model: str = "", timeout: float = settings.LLM_TIMEOUT_SECONDS) -> None:
        self._model = model
        self._timeout = timeout

    async def generate_text(
        self,
        prompt: str,
        *,
        response_mime_type: str | None = None,
        response_schema: dict | None = None,
    ) -> str:
        prompt = (
            "Answer from the text below only. Do not run commands, read files or browse.\n" + prompt
        )
        with tempfile.TemporaryDirectory(prefix="jobpilot-codex-") as directory:
            output = Path(directory) / "last-message.txt"
            argv = [
                "codex",
                "exec",
                "--json",
                "-s",
                "read-only",
                "--skip-git-repo-check",
                "--ephemeral",
                "--ignore-user-config",
                "-C",
                directory,
            ]
            for feature in (
                "browser_use",
                "browser_use_external",
                "computer_use",
                "in_app_browser",
                "apps",
            ):
                argv.extend(["--disable", feature])
            if response_schema is not None:
                schema_file = Path(directory) / "schema.json"
                schema_file.write_text(
                    json.dumps(_strict_codex_schema(response_schema)), encoding="utf-8"
                )
                argv.extend(["--output-schema", str(schema_file)])
            elif response_mime_type == "application/json":
                prompt += (
                    "\n\nRespond with only a single valid JSON object, no prose or code fences."
                )
            if self._model:
                argv.extend(["--model", self._model])
            argv.extend(["-o", str(output), "-"])
            await _run_cli(argv, prompt, self._timeout, cwd=directory)
            try:
                return output.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise LLMCallFailed(f"Could not read Codex's last message: {exc}") from exc
