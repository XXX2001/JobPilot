# SE-Tools Console Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace SE-Tools' fragile `run_subprocess` + `gr.Textbox` console with a robust, observable, performant log viewer (`gr.HTML`) that survives non-UTF-8 output, surfaces problems via colored levels and foldable tracebacks, batches yields for performance, and persists every run to disk for forensic recovery.

**Architecture:** A new `gradio_tools/console/` package owns parsing, rendering, subprocess streaming, and disk persistence. Tool pages swap two lines (`gr.HTML(log_header(...)) + gr.Textbox(...)` → `log_viewer(...)`) and pass `tool_id` + `log_id` to a new `run_subprocess_v2`. A phased migration shims the old API first (fixes the encoding crash for all tools immediately), then migrates each tool page in sequence, then removes the shim.

**Tech Stack:** Python 3.13, Gradio (latest pinned), pytest (existing `tests/` dir with `conftest.py`), `subprocess.Popen` in binary mode, `collections.deque`, `html.escape`, plain CSS/JS additions to `assets/main.css` and `assets/app.js`.

**Spec:** `docs/superpowers/specs/2026-04-17-se-tools-console-redesign-design.md` (Web-automation repo).

**Working directory:** `/home/mouad/ALL/SE-Tools/` (a git repo separate from Web-automation).

---

## File map

**New files:**
- `gradio_tools/console/__init__.py` — re-exports `log_viewer`, `run_subprocess_v2`, `stop_subprocess_v2`
- `gradio_tools/console/parser.py` — `Level`, `Kind`, `ParsedLine`, `Parser`
- `gradio_tools/console/renderer.py` — `Summary`, `render()`
- `gradio_tools/console/runner.py` — `run_subprocess_v2`, `stop_subprocess_v2`, internals (`_decode`, `_BatchedYielder`, line splitter)
- `gradio_tools/console/disk_log.py` — `DiskLog` class
- `tests/test_console_parser.py`
- `tests/test_console_renderer.py`
- `tests/test_console_runner.py`
- `tests/test_console_disk_log.py`
- `tests/console_demo.py` — standalone Gradio page for visual smoke testing

**Modified files:**
- `gradio_tools/ui_templates.py` — phase 2 shim (`run_subprocess` → calls `run_subprocess_v2`); phase 4 deletion
- `gradio_tools/translation.py` — phase 3 migration (lines 452, 623)
- `gradio_tools/pdm.py` — phase 3 migration (lines 376/461 and 605/672)
- `gradio_tools/dslgp.py` — phase 3 migration (lines 634, 846)
- `Launcher.py` — phase 3 add `allowed_paths=[str(LOGS_DIR)]` to `demo.launch(...)`
- `assets/main.css` — phase 1 add `.se-console`; phase 4 remove old `.se-terminal`/`.se-log-*`
- `assets/app.js` — phase 1 add `copyConsoleContent` / `setConsoleFilter`; phase 4 remove `copyLogContent` / `clearLogContent`
- `.gitignore` — add `/logs/`
- `SE-Tools/CLAUDE.md` — phase 4 helpers table update

---

## Phase 1 — Build the package (Tasks 1-11)

### Task 1: Project skeleton

**Files:**
- Create: `gradio_tools/console/__init__.py` (empty placeholder)
- Create: `gradio_tools/console/parser.py` (empty placeholder)
- Create: `gradio_tools/console/renderer.py` (empty placeholder)
- Create: `gradio_tools/console/runner.py` (empty placeholder)
- Create: `gradio_tools/console/disk_log.py` (empty placeholder)
- Modify: `.gitignore`

- [ ] **Step 1: Create the package directory and empty modules**

```bash
mkdir -p /home/mouad/ALL/SE-Tools/gradio_tools/console
touch /home/mouad/ALL/SE-Tools/gradio_tools/console/__init__.py
touch /home/mouad/ALL/SE-Tools/gradio_tools/console/parser.py
touch /home/mouad/ALL/SE-Tools/gradio_tools/console/renderer.py
touch /home/mouad/ALL/SE-Tools/gradio_tools/console/runner.py
touch /home/mouad/ALL/SE-Tools/gradio_tools/console/disk_log.py
```

- [ ] **Step 2: Add `/logs/` to .gitignore**

The existing `*.log` line covers individual files but adding the directory keeps `git status --ignored` output cleaner. Append after the existing `*.log` line in `.gitignore`:

```
# SE-Tools console disk logs
/logs/
```

- [ ] **Step 3: Verify package imports cleanly**

Run: `cd /home/mouad/ALL/SE-Tools && python -c "import gradio_tools.console"`
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
cd /home/mouad/ALL/SE-Tools
git add gradio_tools/console/ .gitignore
git commit -m "feat(console): scaffold console package and gitignore logs/"
```

---

### Task 2: Parser — types and data model

**Files:**
- Modify: `gradio_tools/console/parser.py`
- Create: `tests/test_console_parser.py`

- [ ] **Step 1: Write failing tests for the data types**

Create `tests/test_console_parser.py`:

```python
"""Tests for gradio_tools.console.parser data model."""
from gradio_tools.console.parser import Level, Kind, ParsedLine


def test_level_enum_has_all_values():
    expected = {"PLAIN", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "DONE", "FAIL"}
    assert {l.name for l in Level} == expected


def test_kind_enum_has_all_values():
    expected = {"NORMAL", "TRACEBACK_HEADER", "TRACEBACK_FRAME", "TRACEBACK_TAIL"}
    assert {k.name for k in Kind} == expected


def test_parsed_line_is_frozen():
    pl = ParsedLine(text="hi", level=Level.INFO, kind=Kind.NORMAL, seq=0, is_decode_error=False)
    try:
        pl.text = "bye"  # type: ignore[misc]
    except Exception:
        return  # frozen dataclass raises FrozenInstanceError
    raise AssertionError("ParsedLine should be frozen")


def test_parsed_line_fields_have_correct_types():
    pl = ParsedLine(text="hi", level=Level.WARNING, kind=Kind.NORMAL, seq=42, is_decode_error=True)
    assert pl.text == "hi"
    assert pl.level is Level.WARNING
    assert pl.kind is Kind.NORMAL
    assert pl.seq == 42
    assert pl.is_decode_error is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/mouad/ALL/SE-Tools && python -m pytest tests/test_console_parser.py -v`
Expected: ImportError or AttributeError (Level/Kind/ParsedLine not defined).

- [ ] **Step 3: Implement the data types**

In `gradio_tools/console/parser.py`:

```python
"""Parser: classify a raw decoded subprocess line into ParsedLine.

Public API:
    Level    — log severity enum
    Kind     — line kind enum (normal / traceback header / frame / tail)
    ParsedLine — frozen dataclass returned by Parser.parse()
    Parser   — stateful per-run parser (tracks open traceback blocks)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Level(Enum):
    PLAIN = "PLAIN"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    DONE = "DONE"
    FAIL = "FAIL"


class Kind(Enum):
    NORMAL = "NORMAL"
    TRACEBACK_HEADER = "TRACEBACK_HEADER"
    TRACEBACK_FRAME = "TRACEBACK_FRAME"
    TRACEBACK_TAIL = "TRACEBACK_TAIL"


@dataclass(frozen=True)
class ParsedLine:
    text: str
    level: Level
    kind: Kind
    seq: int
    is_decode_error: bool
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/mouad/ALL/SE-Tools && python -m pytest tests/test_console_parser.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add gradio_tools/console/parser.py tests/test_console_parser.py
git commit -m "feat(console): add Level/Kind/ParsedLine data model"
```

---

### Task 3: Parser — `Parser.parse()` regex rules + traceback state machine + decode flag

**Files:**
- Modify: `gradio_tools/console/parser.py`
- Modify: `tests/test_console_parser.py`

- [ ] **Step 1: Write failing tests covering every rule in the spec table**

Append to `tests/test_console_parser.py`:

```python
import pytest

from gradio_tools.console.parser import Parser


@pytest.fixture
def parser():
    return Parser()


def test_plain_line(parser):
    pl = parser.parse("hello world")
    assert pl.level is Level.PLAIN
    assert pl.kind is Kind.NORMAL
    assert pl.is_decode_error is False
    assert pl.seq == 0


def test_seq_increments(parser):
    a = parser.parse("a")
    b = parser.parse("b")
    assert a.seq == 0
    assert b.seq == 1


def test_debug_keyword(parser):
    assert parser.parse("DEBUG starting up").level is Level.DEBUG


def test_info_keyword(parser):
    assert parser.parse("INFO ready").level is Level.INFO


def test_warning_keyword(parser):
    assert parser.parse("WARNING low disk").level is Level.WARNING
    p2 = Parser()
    assert p2.parse("WARN deprecated").level is Level.WARNING


def test_error_keyword(parser):
    assert parser.parse("ERROR boom").level is Level.ERROR
    p2 = Parser()
    assert p2.parse("ERR oops").level is Level.ERROR


def test_critical_keyword(parser):
    assert parser.parse("CRITICAL system failure").level is Level.CRITICAL
    p2 = Parser()
    assert p2.parse("FATAL panic").level is Level.CRITICAL


def test_case_sensitive_keywords(parser):
    # informational does NOT match INFO
    assert parser.parse("informational message").level is Level.PLAIN


def test_done_marker(parser):
    pl = parser.parse("[DONE] Complete (exit code 0)")
    assert pl.level is Level.DONE


def test_fail_marker(parser):
    pl = parser.parse("[FAIL] Failed (exit code 1)")
    assert pl.level is Level.FAIL


def test_traceback_state_machine():
    p = Parser()
    h = p.parse("Traceback (most recent call last):")
    assert h.kind is Kind.TRACEBACK_HEADER
    assert h.level is Level.ERROR

    f1 = p.parse('  File "/x.py", line 10, in foo')
    assert f1.kind is Kind.TRACEBACK_FRAME
    assert f1.level is Level.ERROR

    f2 = p.parse("    return bar()")
    assert f2.kind is Kind.TRACEBACK_FRAME

    tail = p.parse("ValueError: bad thing")
    assert tail.kind is Kind.TRACEBACK_TAIL
    assert tail.level is Level.ERROR

    # state machine resets after tail
    after = p.parse("INFO continuing")
    assert after.kind is Kind.NORMAL
    assert after.level is Level.INFO


def test_traceback_with_dotted_exception_name():
    p = Parser()
    p.parse("Traceback (most recent call last):")
    p.parse('  File "/x.py", line 10, in foo')
    tail = p.parse("subprocess.CalledProcessError: code 1")
    assert tail.kind is Kind.TRACEBACK_TAIL


def test_decode_error_additive_flag(parser):
    pl = parser.parse("UnicodeDecodeError: 'charmap' codec can't decode byte 0x8f")
    assert pl.is_decode_error is True
    # without an open traceback, this is just a plain ERROR-level line
    assert pl.level is Level.ERROR


def test_decode_error_inside_traceback_tail():
    # the user-reported case: the decode flag must stack on top of TRACEBACK_TAIL
    p = Parser()
    p.parse("Traceback (most recent call last):")
    p.parse('  File "ui_templates.py", line 515, in run_subprocess')
    tail = p.parse("UnicodeDecodeError: 'charmap' codec can't decode byte 0x8f")
    assert tail.kind is Kind.TRACEBACK_TAIL
    assert tail.is_decode_error is True


def test_decode_error_charmap_phrase(parser):
    pl = parser.parse("something happened: 'charmap' codec failed")
    assert pl.is_decode_error is True


def test_critical_beats_error(parser):
    # If both keywords appear, CRITICAL wins (earlier in match order)
    pl = parser.parse("CRITICAL ERROR something broke")
    assert pl.level is Level.CRITICAL


def test_lone_indented_line_outside_traceback_is_plain():
    p = Parser()
    pl = p.parse("    just an indented line")
    assert pl.kind is Kind.NORMAL
    assert pl.level is Level.PLAIN


def test_traceback_tail_with_no_message():
    p = Parser()
    p.parse("Traceback (most recent call last):")
    p.parse('  File "/x.py", line 1, in <module>')
    tail = p.parse("KeyboardInterrupt")
    assert tail.kind is Kind.TRACEBACK_TAIL
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/mouad/ALL/SE-Tools && python -m pytest tests/test_console_parser.py -v`
Expected: AttributeError on `Parser` (not yet defined).

- [ ] **Step 3: Implement `Parser` in `gradio_tools/console/parser.py`**

Append to the module:

```python
import re

# Compiled patterns. Order matters where it's a fall-through chain
# (CRITICAL > ERROR > WARNING > INFO > DEBUG). The traceback rules and
# the decode-error rule are NOT part of that chain.
_RE_TRACEBACK_HEADER = re.compile(r"^Traceback \(most recent call last\):")
_RE_TRACEBACK_TAIL = re.compile(r"^[\w.]+(Error|Exception|Interrupt)\b")
_RE_DECODE_ERROR = re.compile(
    r"(UnicodeDecodeError|UnicodeEncodeError|'charmap' codec)"
)
_RE_CRITICAL = re.compile(r"\b(CRITICAL|FATAL)\b")
_RE_ERROR = re.compile(r"\b(ERROR|ERR)\b")
_RE_WARNING = re.compile(r"\b(WARNING|WARN)\b")
_RE_INFO = re.compile(r"\bINFO\b")
_RE_DEBUG = re.compile(r"\bDEBUG\b")


class Parser:
    """Stateful per-run parser. One Parser instance per subprocess run.

    The traceback state machine flips _in_traceback = True on a
    'Traceback (most recent call last):' line and stays open until a
    non-indented line matching `^[\\w.]+(Error|Exception|Interrupt)\\b`,
    which becomes the TRACEBACK_TAIL and closes the block.
    """

    def __init__(self) -> None:
        self._in_traceback: bool = False
        self._seq: int = 0

    def parse(self, line: str) -> ParsedLine:
        seq = self._seq
        self._seq += 1
        is_decode_error = bool(_RE_DECODE_ERROR.search(line))

        # Traceback state machine takes precedence
        if _RE_TRACEBACK_HEADER.match(line):
            self._in_traceback = True
            return ParsedLine(
                text=line, level=Level.ERROR, kind=Kind.TRACEBACK_HEADER,
                seq=seq, is_decode_error=is_decode_error,
            )

        if self._in_traceback:
            if line.startswith((" ", "\t")):
                return ParsedLine(
                    text=line, level=Level.ERROR, kind=Kind.TRACEBACK_FRAME,
                    seq=seq, is_decode_error=is_decode_error,
                )
            if _RE_TRACEBACK_TAIL.match(line):
                self._in_traceback = False
                return ParsedLine(
                    text=line, level=Level.ERROR, kind=Kind.TRACEBACK_TAIL,
                    seq=seq, is_decode_error=is_decode_error,
                )
            # Any other unindented line breaks out of the traceback
            self._in_traceback = False
            # fall through to keyword classification below

        # Keyword chain (first-match-wins by order)
        for pat, lvl in (
            (_RE_CRITICAL, Level.CRITICAL),
            (_RE_ERROR, Level.ERROR),
            (_RE_WARNING, Level.WARNING),
            (_RE_INFO, Level.INFO),
            (_RE_DEBUG, Level.DEBUG),
        ):
            if pat.search(line):
                return ParsedLine(
                    text=line, level=lvl, kind=Kind.NORMAL,
                    seq=seq, is_decode_error=is_decode_error,
                )

        # [DONE] / [FAIL] markers
        if line.startswith("[DONE]"):
            return ParsedLine(
                text=line, level=Level.DONE, kind=Kind.NORMAL,
                seq=seq, is_decode_error=is_decode_error,
            )
        if line.startswith("[FAIL]"):
            return ParsedLine(
                text=line, level=Level.FAIL, kind=Kind.NORMAL,
                seq=seq, is_decode_error=is_decode_error,
            )

        # If is_decode_error fired but no other rule matched, give it ERROR level
        if is_decode_error:
            return ParsedLine(
                text=line, level=Level.ERROR, kind=Kind.NORMAL,
                seq=seq, is_decode_error=True,
            )

        return ParsedLine(
            text=line, level=Level.PLAIN, kind=Kind.NORMAL,
            seq=seq, is_decode_error=False,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/mouad/ALL/SE-Tools && python -m pytest tests/test_console_parser.py -v`
Expected: all tests pass (~18 tests).

- [ ] **Step 5: Commit**

```bash
git add gradio_tools/console/parser.py tests/test_console_parser.py
git commit -m "feat(console): implement Parser with regex rules + traceback state machine"
```

---

### Task 4: DiskLog — write/header/footer/rotation

**Files:**
- Modify: `gradio_tools/console/disk_log.py`
- Create: `tests/test_console_disk_log.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_console_disk_log.py`:

```python
"""Tests for gradio_tools.console.disk_log."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from gradio_tools.console import disk_log as dl


@pytest.fixture
def tmp_logs(tmp_path, monkeypatch):
    """Redirect disk-log output to tmp_path."""
    monkeypatch.setattr(dl, "LOGS_DIR", tmp_path)
    return tmp_path


def test_creates_file_with_header(tmp_logs):
    log = dl.DiskLog("translation", ["python", "x.py"], Path("/cwd"))
    assert log.path.exists()
    assert log.path.parent == tmp_logs
    assert log.path.name.startswith("translation-")
    text = log.path.read_text(encoding="utf-8", errors="replace")
    assert "# SE-Tools log" in text
    assert "translation" in text
    assert "python x.py" in text
    assert "/cwd" in text


def test_filename_includes_pid_and_timestamp(tmp_logs):
    log = dl.DiskLog("dslgp", ["python", "y.py"], Path("/cwd"))
    name = log.path.name
    assert str(os.getpid()) in name
    assert name.endswith(".log")


def test_write_appends_to_file(tmp_logs):
    log = dl.DiskLog("pdm", ["python", "z.py"], Path("/cwd"))
    log.write("first line\n")
    log.write("second line\n")
    text = log.path.read_text(encoding="utf-8", errors="replace")
    assert "first line\nsecond line\n" in text


def test_close_writes_footer(tmp_logs):
    log = dl.DiskLog("translation", ["python", "x.py"], Path("/cwd"))
    log.close(exit_code=0, duration=12.34, line_count=42)
    text = log.path.read_text(encoding="utf-8", errors="replace")
    assert "exit_code=0" in text
    assert "duration=12.34" in text
    assert "lines=42" in text


def test_interrupt_writes_footer(tmp_logs):
    log = dl.DiskLog("translation", ["python", "x.py"], Path("/cwd"))
    log.write("partial\n")
    log.interrupt()
    text = log.path.read_text(encoding="utf-8", errors="replace")
    assert "interrupted" in text


def test_byte_cap_truncates(tmp_logs, monkeypatch):
    monkeypatch.setattr(dl, "MAX_LOG_BYTES", 256)  # tiny cap
    log = dl.DiskLog("translation", ["python", "x.py"], Path("/cwd"))
    log.write("a" * 300 + "\n")
    log.write("more\n")  # should be a no-op
    text = log.path.read_text(encoding="utf-8", errors="replace")
    assert "truncated" in text
    assert "more" not in text


def test_rotation_keeps_latest_n(tmp_logs, monkeypatch):
    monkeypatch.setattr(dl, "MAX_LOG_FILES_PER_TOOL", 3)
    # create 5 stale files
    for i in range(5):
        f = tmp_logs / f"translation-old{i}.log"
        f.write_text("old", encoding="utf-8")
        # space mtimes apart so sort order is deterministic
        os.utime(f, (1000 + i, 1000 + i))
    # creating a new DiskLog should prune to 3 (incl. the new one)
    log = dl.DiskLog("translation", ["python", "x.py"], Path("/cwd"))
    files = sorted(tmp_logs.glob("translation-*.log"))
    assert len(files) == 3
    assert log.path in files


def test_unicode_in_text_safe(tmp_logs):
    log = dl.DiskLog("translation", ["python", "x.py"], Path("/cwd"))
    log.write("héllo wörld 🚀\n")
    text = log.path.read_text(encoding="utf-8", errors="replace")
    assert "héllo wörld 🚀" in text


def test_fallback_when_logs_dir_unwritable(tmp_path, monkeypatch):
    bad = tmp_path / "no-perm"
    bad.mkdir(mode=0o500)
    monkeypatch.setattr(dl, "LOGS_DIR", bad)
    fallback_root = tmp_path / "fallback-tmp"
    fallback_root.mkdir()
    monkeypatch.setattr(dl, "_fallback_dir", lambda: fallback_root / "se-tools-logs")
    log = dl.DiskLog("translation", ["python", "x.py"], Path("/cwd"))
    assert log.path.parent == fallback_root / "se-tools-logs"
    # cleanup
    bad.chmod(0o700)


def test_write_after_oserror_silently_drops(tmp_logs, monkeypatch):
    log = dl.DiskLog("translation", ["python", "x.py"], Path("/cwd"))
    log.write("ok\n")
    # simulate disk full on next write
    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(log, "_raw_append", boom)
    log.write("dropped\n")  # must not raise
    log.write("also dropped\n")
    assert log.degraded is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/mouad/ALL/SE-Tools && python -m pytest tests/test_console_disk_log.py -v`
Expected: ImportError or AttributeError.

- [ ] **Step 3: Implement `DiskLog` in `gradio_tools/console/disk_log.py`**

```python
"""Disk-log writer for run_subprocess_v2.

One DiskLog per subprocess run. File path:
    LOGS_DIR / <tool_id>-<YYYY-MM-DD_HHMMSS>_<pid>.log

If LOGS_DIR is unwritable, falls back to tempfile.gettempdir()/se-tools-logs/.
On any OSError during write, switches to no-op mode (self.degraded = True)
so the rest of the run is unaffected.
"""
from __future__ import annotations

import os
import platform
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # SE-Tools/
LOGS_DIR: Path = PROJECT_ROOT / "logs"

MAX_LOG_BYTES = 10 * 1024 * 1024            # 10 MB per file
MAX_LOG_FILES_PER_TOOL = 50                  # rotate beyond this


def _fallback_dir() -> Path:
    return Path(tempfile.gettempdir()) / "se-tools-logs"


class DiskLog:
    def __init__(self, tool_id: str, cmd: list[str], cwd: Path) -> None:
        self.tool_id = tool_id
        self.degraded = False
        self.fallback_used = False
        self._bytes_written = 0

        self.path = self._resolve_path(tool_id)
        try:
            self._write_header(cmd, cwd)
        except OSError:
            # try fallback once
            self.fallback_used = True
            fallback = _fallback_dir()
            try:
                fallback.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.degraded = True
                return
            self.path = fallback / self.path.name
            try:
                self._write_header(cmd, cwd)
            except OSError:
                self.degraded = True
                return

        self._rotate()

    def _resolve_path(self, tool_id: str) -> Path:
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            return LOGS_DIR / f"{tool_id}-{ts}_{os.getpid()}.log"
        except OSError:
            self.fallback_used = True
            fallback = _fallback_dir()
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback / f"{tool_id}-{ts}_{os.getpid()}.log"

    def _write_header(self, cmd: list[str], cwd: Path) -> None:
        header = (
            f"# SE-Tools log — {self.tool_id} — started "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"# cmd: {' '.join(cmd)}\n"
            f"# cwd: {cwd}\n"
            f"# python: {sys.version.split()[0]} ({platform.python_implementation()}) "
            f"on {sys.platform}\n"
        )
        self._raw_append(header.encode("utf-8", errors="replace"))

    def _raw_append(self, data: bytes) -> None:
        with open(self.path, "ab") as f:
            f.write(data)
            f.flush()
        self._bytes_written += len(data)

    def write(self, raw: str) -> None:
        if self.degraded:
            return
        data = raw.encode("utf-8", errors="replace")
        if self._bytes_written + len(data) > MAX_LOG_BYTES:
            try:
                self._raw_append(b"# ... truncated, log too long\n")
            except OSError:
                pass
            self.degraded = True
            return
        try:
            self._raw_append(data)
        except OSError:
            self.degraded = True

    def close(self, exit_code: int, duration: float, line_count: int) -> None:
        if self.degraded:
            return
        footer = (
            f"# exit_code={exit_code}  duration={duration:.2f}s  "
            f"lines={line_count}\n"
        )
        try:
            self._raw_append(footer.encode("utf-8"))
        except OSError:
            self.degraded = True

    def interrupt(self) -> None:
        if self.degraded:
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            self._raw_append(f"# interrupted at {ts}\n".encode("utf-8"))
        except OSError:
            self.degraded = True

    def _rotate(self) -> None:
        try:
            files = sorted(
                self.path.parent.glob(f"{self.tool_id}-*.log"),
                key=lambda p: p.stat().st_mtime,
            )
            extra = len(files) - MAX_LOG_FILES_PER_TOOL
            for old in files[: max(0, extra)]:
                if old != self.path:
                    try:
                        old.unlink()
                    except OSError:
                        pass
        except OSError:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/mouad/ALL/SE-Tools && python -m pytest tests/test_console_disk_log.py -v`
Expected: all tests pass (~10 tests).

- [ ] **Step 5: Commit**

```bash
git add gradio_tools/console/disk_log.py tests/test_console_disk_log.py
git commit -m "feat(console): add DiskLog with rotation, byte cap, OSError fallback"
```

---

### Task 5: Renderer — Summary + render() with traceback folding + filter + cache

**Files:**
- Modify: `gradio_tools/console/renderer.py`
- Create: `tests/test_console_renderer.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_console_renderer.py`:

```python
"""Tests for gradio_tools.console.renderer."""
from __future__ import annotations

from collections import deque
from pathlib import Path

from gradio_tools.console.parser import Kind, Level, ParsedLine
from gradio_tools.console.renderer import Summary, render


def _line(text, level=Level.INFO, kind=Kind.NORMAL, seq=0, decode=False):
    return ParsedLine(text=text, level=level, kind=kind, seq=seq, is_decode_error=decode)


def _summary(**overrides):
    base = dict(
        elapsed_seconds=1.0, line_count=1,
        warn_count=0, error_count=0, decode_error_count=0,
        is_running=True, download_path=None,
    )
    base.update(overrides)
    return Summary(**base)


def test_empty_buffer_renders_empty_state():
    html = render(deque(), _summary(line_count=0), "test-log")
    assert "se-console" in html
    assert 'id="test-log"' in html


def test_line_renders_with_level_class():
    buf = deque([_line("hello", Level.WARNING)])
    html = render(buf, _summary(warn_count=1), "test-log")
    assert "ln-WARNING" in html
    assert "hello" in html


def test_html_escape_protects_against_injection():
    buf = deque([_line("<script>alert(1)</script>", Level.PLAIN)])
    html = render(buf, _summary(), "test-log")
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_summary_chip_shows_counts():
    html = render(deque(), _summary(warn_count=3, error_count=1), "test-log")
    assert "3" in html
    assert "1" in html


def test_decode_chip_only_when_count_positive():
    html_zero = render(deque(), _summary(decode_error_count=0), "test-log")
    assert "chip-decode" not in html_zero
    html_one = render(deque(), _summary(decode_error_count=1), "test-log")
    assert "chip-decode" in html_one


def test_running_dot_class_when_running():
    html = render(deque(), _summary(is_running=True), "test-log")
    assert "running" in html
    html2 = render(deque(), _summary(is_running=False), "test-log")
    assert "running" not in html2 or 'class="dot"' in html2


def test_download_link_when_path_present(tmp_path):
    p = tmp_path / "x.log"
    p.write_text("ok")
    html = render(deque(), _summary(download_path=p), "test-log")
    assert "/file=" in html
    assert str(p) in html


def test_no_download_link_when_path_none():
    html = render(deque(), _summary(download_path=None), "test-log")
    assert "chip-dl" not in html


def test_traceback_renders_as_details_block():
    buf = deque([
        _line("Traceback (most recent call last):", Level.ERROR, Kind.TRACEBACK_HEADER, 0),
        _line('  File "x.py", line 10, in foo', Level.ERROR, Kind.TRACEBACK_FRAME, 1),
        _line("ValueError: bad", Level.ERROR, Kind.TRACEBACK_TAIL, 2),
    ])
    html = render(buf, _summary(error_count=1), "test-log")
    assert "<details" in html
    assert "</details>" in html
    assert "Traceback" in html
    assert "ValueError" in html


def test_long_traceback_collapsed_by_default():
    items = [_line("Traceback (most recent call last):", Level.ERROR, Kind.TRACEBACK_HEADER, 0)]
    for i in range(10):
        items.append(_line(f"  File 'x.py', line {i}", Level.ERROR, Kind.TRACEBACK_FRAME, i + 1))
    items.append(_line("ValueError: x", Level.ERROR, Kind.TRACEBACK_TAIL, 99))
    html = render(deque(items), _summary(), "test-log")
    # short tracebacks open by default; long (>5 frames) closed
    assert "<details class=\"tb\">" in html  # no `open` attribute


def test_short_traceback_open_by_default():
    items = [
        _line("Traceback (most recent call last):", Level.ERROR, Kind.TRACEBACK_HEADER, 0),
        _line("  File 'x.py', line 1", Level.ERROR, Kind.TRACEBACK_FRAME, 1),
        _line("ValueError: x", Level.ERROR, Kind.TRACEBACK_TAIL, 2),
    ])
    html = render(deque(items), _summary(), "test-log")
    assert "<details class=\"tb\" open>" in html


def test_decode_line_gets_ln_decode_class():
    buf = deque([_line("UnicodeDecodeError: ...", Level.ERROR, Kind.NORMAL, 0, decode=True)])
    html = render(buf, _summary(decode_error_count=1), "test-log")
    assert "ln-decode" in html


def test_filter_data_attribute():
    buf = deque([_line("x", Level.INFO)])
    html = render(buf, _summary(), "test-log", filter_level="ERROR")
    assert 'data-filter="ERROR"' in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/mouad/ALL/SE-Tools && python -m pytest tests/test_console_renderer.py -v`
Expected: ImportError on `Summary`/`render`.

- [ ] **Step 3: Implement renderer**

In `gradio_tools/console/renderer.py`:

```python
"""Renderer: ParsedLine deque + Summary -> HTML string for gr.HTML."""
from __future__ import annotations

import html
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .parser import Kind, Level, ParsedLine

LONG_TRACEBACK_FRAMES = 5  # > this many frames -> collapsed by default


@dataclass(frozen=True)
class Summary:
    elapsed_seconds: float
    line_count: int
    warn_count: int
    error_count: int
    decode_error_count: int
    is_running: bool
    download_path: Path | None


def _render_line(p: ParsedLine) -> str:
    classes = ["ln", f"ln-{p.level.name}"]
    if p.kind is Kind.TRACEBACK_FRAME:
        classes.append("ln-frame")
    if p.kind is Kind.TRACEBACK_TAIL:
        classes.append("ln-tail")
    if p.is_decode_error:
        classes.append("ln-decode")
    return f'<div class="{" ".join(classes)}">{html.escape(p.text)}</div>'


def _group_tracebacks(buffer: Iterable[ParsedLine]) -> list[list[ParsedLine]]:
    """Group consecutive traceback lines into blocks; non-tb lines become singletons."""
    groups: list[list[ParsedLine]] = []
    current: list[ParsedLine] = []
    for p in buffer:
        if p.kind in (Kind.TRACEBACK_HEADER, Kind.TRACEBACK_FRAME, Kind.TRACEBACK_TAIL):
            current.append(p)
            if p.kind is Kind.TRACEBACK_TAIL:
                groups.append(current)
                current = []
        else:
            if current:
                groups.append(current)
                current = []
            groups.append([p])
    if current:
        groups.append(current)  # unterminated traceback
    return groups


def _render_traceback_block(block: list[ParsedLine]) -> str:
    frame_count = sum(1 for p in block if p.kind is Kind.TRACEBACK_FRAME)
    open_attr = "" if frame_count > LONG_TRACEBACK_FRAMES else " open"
    header = next((p for p in block if p.kind is Kind.TRACEBACK_HEADER), block[0])
    rest = [p for p in block if p is not header]
    return (
        f'<details class="tb"{open_attr}>'
        f'<summary class="ln ln-{header.level.name}">{html.escape(header.text)}</summary>'
        + "".join(_render_line(p) for p in rest)
        + "</details>"
    )


def _render_summary(summary: Summary, log_id: str) -> str:
    dot_cls = "dot running" if summary.is_running else "dot"
    chips = []
    if summary.warn_count:
        chips.append(
            f'<button class="chip chip-warn" '
            f'onclick="setConsoleFilter(\'{log_id}\',\'WARNING\')">'
            f"⚠ {summary.warn_count}</button>"
        )
    if summary.error_count:
        chips.append(
            f'<button class="chip chip-err" '
            f'onclick="setConsoleFilter(\'{log_id}\',\'ERROR\')">'
            f"✖ {summary.error_count}</button>"
        )
    if summary.decode_error_count:
        chips.append(
            f'<button class="chip chip-decode" '
            f'onclick="setConsoleFilter(\'{log_id}\',\'DECODE\')">'
            f"🔤 {summary.decode_error_count}</button>"
        )
    chips.append(
        f'<button class="chip chip-copy" '
        f'onclick="copyConsoleContent(\'{log_id}\')">⧉ Copy</button>'
    )
    if summary.download_path is not None:
        chips.append(
            f'<a class="chip chip-dl" href="/file={html.escape(str(summary.download_path))}">'
            f"📄 Download log</a>"
        )
    return (
        '<div class="se-console-header">'
        f'<span class="se-console-title">Output<span class="{dot_cls}"></span></span>'
        f'<span class="se-console-meta">{summary.elapsed_seconds:.1f}s · '
        f'{summary.line_count:,} lines</span>'
        f'<span class="se-console-chips">{"".join(chips)}</span>'
        "</div>"
    )


def render(
    buffer: deque[ParsedLine],
    summary: Summary,
    log_id: str,
    filter_level: str | None = None,
) -> str:
    parts = [_render_summary(summary, log_id)]
    body_attr = f' data-filter="{html.escape(filter_level)}"' if filter_level else ""
    parts.append(f'<div class="se-console-body"{body_attr}>')
    if not buffer:
        parts.append('<div class="se-console-empty">No output yet.</div>')
    else:
        for group in _group_tracebacks(buffer):
            if (
                len(group) > 1
                and any(p.kind is Kind.TRACEBACK_HEADER for p in group)
            ):
                parts.append(_render_traceback_block(group))
            else:
                for p in group:
                    parts.append(_render_line(p))
    parts.append("</div>")
    return f'<div class="se-console" id="{log_id}">{"".join(parts)}</div>'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/mouad/ALL/SE-Tools && python -m pytest tests/test_console_renderer.py -v`
Expected: all tests pass (~13 tests).

- [ ] **Step 5: Commit**

```bash
git add gradio_tools/console/renderer.py tests/test_console_renderer.py
git commit -m "feat(console): add renderer with traceback folding, filter, and chips"
```

---

### Task 6: Runner — `_decode()` + line splitter (pure unit-testable)

**Files:**
- Modify: `gradio_tools/console/runner.py`
- Create: `tests/test_console_runner.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_console_runner.py`:

```python
"""Tests for gradio_tools.console.runner internals."""
from __future__ import annotations

import pytest

from gradio_tools.console.runner import _decode, LineSplitter, _strip_ansi, _truncate_line


def test_decode_utf8():
    assert _decode("héllo".encode("utf-8")) == "héllo"


def test_decode_cp1252_fallback():
    # 0x8f is invalid in UTF-8 but valid in CP1252
    assert _decode(b"a\x8fb")  # must not raise


def test_decode_garbage_falls_back_to_replace():
    # bytes that are also invalid in CP1252 (0x81 is "undefined")
    out = _decode(b"\x81\x8d\x8f")
    assert isinstance(out, str)


def test_strip_ansi():
    assert _strip_ansi("\x1b[31mred\x1b[0m") == "red"
    assert _strip_ansi("plain") == "plain"


def test_truncate_short_line_unchanged():
    assert _truncate_line("hello", 1024) == "hello"


def test_truncate_long_line_marked():
    long = "x" * 2048
    out = _truncate_line(long, 100)
    assert len(out) <= 200
    assert "truncated" in out


def test_line_splitter_basic():
    s = LineSplitter()
    s.feed(b"hello\nworld\n")
    assert s.lines() == ["hello", "world"]
    assert s.lines() == []  # consumed


def test_line_splitter_partial_at_chunk_boundary():
    s = LineSplitter()
    s.feed(b"hel")
    assert s.lines() == []
    s.feed(b"lo\nworld")
    assert s.lines() == ["hello"]
    s.feed(b"\n")
    assert s.lines() == ["world"]


def test_line_splitter_partial_utf8_at_chunk_boundary():
    s = LineSplitter()
    encoded = "héllo\n".encode("utf-8")  # 'é' is 2 bytes
    # split mid-char
    s.feed(encoded[:2])  # 'h' + first byte of 'é'
    assert s.lines() == []
    s.feed(encoded[2:])
    assert s.lines() == ["héllo"]


def test_line_splitter_carriage_return_progress():
    s = LineSplitter()
    s.feed(b"progress 10%\rprogress 20%\rprogress 30%\n")
    out = s.lines()
    # \r-only updates collapse to the latest before the \n
    assert out == ["progress 30%"]


def test_line_splitter_crlf_treated_as_lf():
    s = LineSplitter()
    s.feed(b"line1\r\nline2\r\n")
    assert s.lines() == ["line1", "line2"]


def test_line_splitter_flush_returns_unterminated():
    s = LineSplitter()
    s.feed(b"no newline at end")
    assert s.lines() == []
    assert s.flush() == ["no newline at end"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/mouad/ALL/SE-Tools && python -m pytest tests/test_console_runner.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement decoder, splitter, and helpers**

In `gradio_tools/console/runner.py`:

```python
"""Subprocess runner with binary-mode IO, manual decode, batched yielding.

Public API:
    run_subprocess_v2(...)  — generator yielding HTML strings
    stop_subprocess_v2(...) — cross-platform terminator

Internals (exposed for unit tests):
    _decode(bytes) -> str
    _strip_ansi(str) -> str
    _truncate_line(str, max_len) -> str
    LineSplitter — accumulates bytes, yields complete decoded lines
"""
from __future__ import annotations

import os
import re
from typing import List

MAX_LINE = 16 * 1024  # bytes; lines longer than this are hard-split

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _decode(b: bytes) -> str:
    for enc in ("utf-8", "cp1252"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _truncate_line(s: str, max_len: int = MAX_LINE) -> str:
    if len(s) <= max_len:
        return s
    head = s[:max_len]
    return f"{head} … [line truncated, full text in disk log]"


class LineSplitter:
    """Accumulate bytes, yield complete decoded lines.

    Handles:
      - partial UTF-8 sequences at chunk boundaries (only decodes up to last \\n)
      - CRLF and bare LF
      - bare CR for in-place progress bars: collapses to the latest
        \\r-segment before the next \\n
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> None:
        self._buf.extend(data)

    def lines(self) -> List[str]:
        out: List[str] = []
        while True:
            nl = self._buf.find(b"\n")
            if nl == -1:
                return out
            chunk = bytes(self._buf[:nl])
            del self._buf[: nl + 1]
            if chunk.endswith(b"\r"):
                chunk = chunk[:-1]
            decoded = _decode(chunk)
            # collapse \r progress segments: keep the part after the last \r
            if "\r" in decoded:
                decoded = decoded.split("\r")[-1]
            out.append(decoded)

    def flush(self) -> List[str]:
        if not self._buf:
            return []
        chunk = bytes(self._buf)
        self._buf.clear()
        decoded = _decode(chunk)
        if "\r" in decoded:
            decoded = decoded.split("\r")[-1]
        return [decoded]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/mouad/ALL/SE-Tools && python -m pytest tests/test_console_runner.py -v`
Expected: all tests pass (~13 tests).

- [ ] **Step 5: Commit**

```bash
git add gradio_tools/console/runner.py tests/test_console_runner.py
git commit -m "feat(console): add _decode, _strip_ansi, _truncate_line, LineSplitter"
```

---

### Task 7: Runner — `_BatchedYielder` (time + count batching)

**Files:**
- Modify: `gradio_tools/console/runner.py`
- Modify: `tests/test_console_runner.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_console_runner.py`:

```python
import time
from gradio_tools.console.runner import _BatchedYielder


def test_batched_yielder_emits_after_count_threshold():
    by = _BatchedYielder(every_lines=3, every_seconds=10.0)
    assert by.add() is False
    assert by.add() is False
    assert by.add() is True   # 3rd line triggers
    assert by.add() is False
    by.reset()
    assert by.add() is False


def test_batched_yielder_emits_after_time_threshold(monkeypatch):
    fake_time = [1000.0]
    monkeypatch.setattr(
        "gradio_tools.console.runner.time.monotonic",
        lambda: fake_time[0],
    )
    by = _BatchedYielder(every_lines=999, every_seconds=0.1)
    assert by.add() is False
    fake_time[0] += 0.05
    assert by.add() is False
    fake_time[0] += 0.06
    assert by.add() is True


def test_batched_yielder_reset_clears_pending():
    by = _BatchedYielder(every_lines=2, every_seconds=10.0)
    by.add()
    by.reset()
    assert by.add() is False  # would be the 1st post-reset, not the 2nd of original
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/mouad/ALL/SE-Tools && python -m pytest tests/test_console_runner.py -v -k batched`
Expected: ImportError.

- [ ] **Step 3: Implement `_BatchedYielder`**

Add to `gradio_tools/console/runner.py`:

```python
import time

YIELD_EVERY_LINES = 50
YIELD_EVERY_SECONDS = 0.1


class _BatchedYielder:
    """Returns True from add() when caller should yield.

    True is returned when at least every_lines have arrived since the last
    yield, OR at least every_seconds have elapsed.
    """

    def __init__(
        self,
        every_lines: int = YIELD_EVERY_LINES,
        every_seconds: float = YIELD_EVERY_SECONDS,
    ) -> None:
        self.every_lines = every_lines
        self.every_seconds = every_seconds
        self._pending = 0
        self._last_yield = time.monotonic()

    def add(self) -> bool:
        self._pending += 1
        if self._pending >= self.every_lines:
            return True
        if time.monotonic() - self._last_yield >= self.every_seconds:
            return True
        return False

    def reset(self) -> None:
        self._pending = 0
        self._last_yield = time.monotonic()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/mouad/ALL/SE-Tools && python -m pytest tests/test_console_runner.py -v -k batched`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add gradio_tools/console/runner.py tests/test_console_runner.py
git commit -m "feat(console): add _BatchedYielder for time/count yield throttling"
```

---

### Task 8: Runner — `run_subprocess_v2` + `stop_subprocess_v2` integration

**Files:**
- Modify: `gradio_tools/console/runner.py`
- Modify: `tests/test_console_runner.py`

- [ ] **Step 1: Write failing integration tests**

Append to `tests/test_console_runner.py`:

```python
import sys
import time
from pathlib import Path

from gradio_tools.console import disk_log as dl
from gradio_tools.console.runner import run_subprocess_v2, stop_subprocess_v2


def test_run_subprocess_v2_streams_output(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "LOGS_DIR", tmp_path)
    script = tmp_path / "noisy.py"
    script.write_text(
        "import sys\n"
        "for i in range(20):\n"
        "    print(f'line {i} INFO ready')\n"
    )
    chunks = list(run_subprocess_v2(
        script, tmp_path,
        tool_id="test", log_id="test-log",
    ))
    assert chunks, "should yield at least once"
    last = chunks[-1]
    assert "line 19" in last
    assert "[DONE]" in last or "[FAIL]" in last


def test_run_subprocess_v2_writes_disk_log(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "LOGS_DIR", tmp_path)
    script = tmp_path / "echo.py"
    script.write_text("print('hello disk log')\n")
    list(run_subprocess_v2(script, tmp_path, tool_id="test", log_id="test-log"))
    log_files = list(tmp_path.glob("test-*.log"))
    assert len(log_files) == 1
    text = log_files[0].read_text(encoding="utf-8", errors="replace")
    assert "hello disk log" in text
    assert "exit_code=0" in text


def test_run_subprocess_v2_survives_non_utf8_output(tmp_path, monkeypatch):
    """The user-reported crash: subprocess emits CP1252 byte 0x8f."""
    monkeypatch.setattr(dl, "LOGS_DIR", tmp_path)
    script = tmp_path / "bad_bytes.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.buffer.write(b'before\\n')\n"
        "sys.stdout.buffer.write(b'\\x8fbad-byte-here\\n')\n"
        "sys.stdout.buffer.write(b'after\\n')\n"
        "sys.stdout.flush()\n"
    )
    chunks = list(run_subprocess_v2(
        script, tmp_path, tool_id="test", log_id="test-log",
    ))
    last = chunks[-1]
    assert "before" in last
    assert "after" in last
    # critically: did NOT crash with UnicodeDecodeError


def test_run_subprocess_v2_failure_exit_code(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "LOGS_DIR", tmp_path)
    script = tmp_path / "fail.py"
    script.write_text("import sys\nsys.exit(2)\n")
    chunks = list(run_subprocess_v2(script, tmp_path, tool_id="test", log_id="test-log"))
    last = chunks[-1]
    assert "[FAIL]" in last
    assert "2" in last  # exit code surfaced


def test_run_subprocess_v2_summary_chip_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "LOGS_DIR", tmp_path)
    script = tmp_path / "mixed.py"
    script.write_text(
        "print('INFO start')\n"
        "print('WARNING low disk')\n"
        "print('ERROR boom')\n"
    )
    chunks = list(run_subprocess_v2(script, tmp_path, tool_id="test", log_id="test-log"))
    last = chunks[-1]
    # summary chip should reflect 1 warn, 1 error
    assert "⚠ 1" in last
    assert "✖ 1" in last


def test_run_subprocess_v2_yield_count_is_batched(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "LOGS_DIR", tmp_path)
    script = tmp_path / "burst.py"
    script.write_text(
        "for i in range(5000):\n"
        "    print(f'line {i}')\n"
    )
    chunks = list(run_subprocess_v2(script, tmp_path, tool_id="test", log_id="test-log"))
    # 5000 lines should not produce 5000 yields. Loose upper bound:
    assert len(chunks) < 200, f"too many yields: {len(chunks)}"


def test_run_subprocess_v2_warns_when_already_running(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "LOGS_DIR", tmp_path)
    # Print one line then sleep — guarantees gen1 produces output before
    # gen2 attempts the double-launch check, avoiding a race.
    script = tmp_path / "loop.py"
    script.write_text(
        "import time\n"
        "print('alive', flush=True)\n"
        "while True:\n    time.sleep(0.5)\n"
    )
    holder = [None]
    gen1 = run_subprocess_v2(script, tmp_path, tool_id="t", log_id="l", process_holder=holder)
    # consume up to 1 second to make sure the subprocess has started and
    # process_holder[0] is populated
    start = time.monotonic()
    for _ in gen1:
        if holder[0] is not None and holder[0].poll() is None:
            break
        if time.monotonic() - start > 1.0:
            break
    # second call must refuse
    gen2 = run_subprocess_v2(script, tmp_path, tool_id="t", log_id="l", process_holder=holder)
    out = next(gen2)
    assert "already running" in out.lower()
    # cleanup
    stop_subprocess_v2(holder)
    for _ in gen1:
        pass  # drain
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/mouad/ALL/SE-Tools && python -m pytest tests/test_console_runner.py -v -k "v2 or stop"`
Expected: ImportError on `run_subprocess_v2` / `stop_subprocess_v2`.

- [ ] **Step 3: Implement `run_subprocess_v2` and `stop_subprocess_v2`**

Append to `gradio_tools/console/runner.py` (note: `os`, `re`, `time` are already imported at the top of the file from earlier tasks):

```python
import signal
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Generator, Set

from .disk_log import DiskLog
from .parser import Kind, Level, Parser, ParsedLine
from .renderer import Summary, render

WINDOWS = sys.platform.startswith("win")
BUFFER_SIZE = 500       # ParsedLine ring buffer cap
READ_CHUNK = 65536      # bytes per os.read()

_all_processes: Set[subprocess.Popen] = set()


def _make_summary(
    started: float,
    counters: dict,
    is_running: bool,
    download_path: Path | None,
) -> Summary:
    return Summary(
        elapsed_seconds=time.monotonic() - started,
        line_count=counters["seq"],
        warn_count=counters["warn"],
        error_count=counters["err"],
        decode_error_count=counters["decode"],
        is_running=is_running,
        download_path=download_path,
    )


def _bump_counters(p: ParsedLine, counters: dict) -> None:
    counters["seq"] = p.seq + 1
    if p.level is Level.WARNING:
        counters["warn"] += 1
    elif p.level in (Level.ERROR, Level.CRITICAL, Level.FAIL):
        counters["err"] += 1
    if p.is_decode_error:
        counters["decode"] += 1


def run_subprocess_v2(
    script: str | Path,
    cwd: str | Path,
    *,
    tool_id: str,
    log_id: str,
    env_extra: dict[str, str] | None = None,
    extra_args: list[str] | None = None,
    process_holder: list | None = None,
) -> Generator[str, None, None]:
    """Run a Python script and yield HTML log snapshots.

    Same usage as the old run_subprocess but yields HTML (renderer output)
    instead of plain text. Always writes a complete disk log; the path is
    surfaced via the 'Download log' chip in the rendered HTML.
    """
    cmd = [sys.executable, str(script)]
    if extra_args:
        cmd.extend(extra_args)

    # Reject double-launch
    if process_holder is not None and process_holder[0] is not None:
        proc = process_holder[0]
        if proc.poll() is None:
            buf: deque[ParsedLine] = deque(maxlen=BUFFER_SIZE)
            buf.append(ParsedLine(
                text="[WARNING] A process is already running. Stop it first.",
                level=Level.WARNING, kind=Kind.NORMAL, seq=0, is_decode_error=False,
            ))
            yield render(buf, Summary(0.0, 1, 1, 0, 0, False, None), log_id)
            return

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if env_extra:
        env.update(env_extra)

    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0
    preexec_fn = None if WINDOWS else os.setsid

    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        env=env,
        creationflags=creationflags,
        preexec_fn=preexec_fn,
    )
    _all_processes.add(process)
    if process_holder is not None:
        process_holder[0] = process

    disk = DiskLog(tool_id, cmd, Path(str(cwd)))
    parser = Parser()
    splitter = LineSplitter()
    parsed: deque[ParsedLine] = deque(maxlen=BUFFER_SIZE)
    counters = {"seq": 0, "warn": 0, "err": 0, "decode": 0}
    yielder = _BatchedYielder()
    started = time.monotonic()

    fd = process.stdout.fileno()  # type: ignore[union-attr]

    def _process_lines(new_lines: list[str]) -> bool:
        """Parse + buffer + accumulate. Returns True if a yield should happen."""
        any_yield = False
        for raw in new_lines:
            disk.write(raw + "\n")
            line = _strip_ansi(raw)
            line = _truncate_line(line)
            p = parser.parse(line)
            parsed.append(p)
            _bump_counters(p, counters)
            if yielder.add():
                any_yield = True
        return any_yield

    try:
        while True:
            try:
                chunk = os.read(fd, READ_CHUNK)
            except OSError:
                chunk = b""
            if not chunk:
                if process.poll() is not None:
                    break
                continue
            splitter.feed(chunk)
            if _process_lines(splitter.lines()):
                yield render(
                    parsed,
                    _make_summary(started, counters, True, disk.path),
                    log_id,
                )
                yielder.reset()

        # Flush any trailing bytes (no terminal newline)
        _process_lines(splitter.flush())

        process.wait()
        exit_code = process.returncode
        status_text = (
            f"[DONE] Complete (exit code {exit_code})"
            if exit_code == 0
            else f"[FAIL] Failed (exit code {exit_code})"
        )
        status = parser.parse(status_text)
        parsed.append(status)
        _bump_counters(status, counters)
        disk.write(status_text + "\n")
        disk.close(exit_code, time.monotonic() - started, counters["seq"])

        yield render(
            parsed,
            _make_summary(started, counters, False, disk.path),
            log_id,
        )
    finally:
        # If generator was interrupted, terminate cleanly
        if process.poll() is None:
            _terminate(process)
            disk.interrupt()
        _all_processes.discard(process)
        if process_holder is not None and process_holder[0] is process:
            process_holder[0] = None


def _terminate(proc: subprocess.Popen) -> None:
    if WINDOWS:
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        except (OSError, ValueError):
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def stop_subprocess_v2(process_holder: list) -> str:
    """Terminate process_holder[0]. Returns status string."""
    proc = process_holder[0] if process_holder else None
    if proc is None or proc.poll() is not None:
        return "No process running."
    _terminate(proc)
    _all_processes.discard(proc)
    return "Process stopped."


def cleanup_all_v2() -> None:
    """Called from atexit / signal handlers in Launcher.py."""
    for proc in list(_all_processes):
        if proc.poll() is None:
            _terminate(proc)
    _all_processes.clear()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/mouad/ALL/SE-Tools && python -m pytest tests/test_console_runner.py -v`
Expected: all tests pass (~20+ tests). The non-UTF-8 test specifically validates the user's reported crash is fixed.

- [ ] **Step 5: Commit**

```bash
git add gradio_tools/console/runner.py tests/test_console_runner.py
git commit -m "feat(console): add run_subprocess_v2 with binary IO and HTML streaming"
```

---

### Task 9: `log_viewer()` helper + package exports

**Files:**
- Modify: `gradio_tools/console/__init__.py`

- [ ] **Step 1: Implement `log_viewer()` and re-exports**

In `gradio_tools/console/__init__.py`:

```python
"""SE-Tools console — robust subprocess log viewer.

Public API:
    log_viewer(log_id, lines=20)   — returns a gr.HTML for tool pages
    run_subprocess_v2(...)          — generator yielding HTML chunks
    stop_subprocess_v2(holder)      — cross-platform terminator
    cleanup_all_v2()                — atexit hook for Launcher.py
"""
from __future__ import annotations

import gradio as gr  # type: ignore

from .runner import (
    cleanup_all_v2,
    run_subprocess_v2,
    stop_subprocess_v2,
)

__all__ = [
    "log_viewer",
    "run_subprocess_v2",
    "stop_subprocess_v2",
    "cleanup_all_v2",
]


def log_viewer(log_id: str, *, lines: int = 20) -> gr.HTML:
    """Empty-state HTML viewer. The handler generator drives its updates."""
    initial = (
        f'<div class="se-console" id="{log_id}" style="--console-h: {lines * 18}px;">'
        f'<div class="se-console-empty">No output yet.</div>'
        f"</div>"
    )
    return gr.HTML(value=initial, elem_id=f"{log_id}-wrap")
```

- [ ] **Step 2: Verify imports**

Run:
```bash
cd /home/mouad/ALL/SE-Tools && python -c "from gradio_tools.console import log_viewer, run_subprocess_v2, stop_subprocess_v2, cleanup_all_v2; print('ok')"
```
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add gradio_tools/console/__init__.py
git commit -m "feat(console): add log_viewer helper and package exports"
```

---

### Task 10: CSS additions — `.se-console` ruleset

**Files:**
- Modify: `assets/main.css`

- [ ] **Step 1: Append the new ruleset to `assets/main.css`**

Append at the end of `assets/main.css` (do **not** touch existing `.se-terminal` rules — those stay until phase 4 cleanup):

```css
/* ──────────────────────────────────────────────────────────────────
   se-console — robust HTML log viewer (phase 1+ of console redesign).
   Coexists with the legacy .se-terminal until phase 4 removal.
   ────────────────────────────────────────────────────────────────── */
.se-console {
  background: #1c1c1e;
  border-radius: 12px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12.5px;
  color: #d1d5db;
  overflow: hidden;
  margin-top: 8px;
}
.se-console-header {
  display: flex; align-items: center; gap: 12px;
  padding: 8px 12px; border-bottom: 1px solid #2c2c2e;
}
.se-console-title {
  display: flex; align-items: center; gap: 6px;
  font-weight: 600; color: #f5f5f7;
}
.se-console-title .dot {
  width: 8px; height: 8px; border-radius: 50%; background: #48484a;
}
.se-console-title .dot.running {
  background: #22c55e; animation: pulseDot 1.5s infinite;
}
.se-console-meta { color: #86868b; font-size: 11.5px; margin-right: auto; }
.se-console-chips { display: flex; gap: 6px; }
.chip {
  padding: 2px 8px; border-radius: 10px; font-size: 11px;
  cursor: pointer; border: 1px solid transparent;
  background: #2c2c2e; color: #d1d5db;
}
.chip-warn { color: #f59e0b; }
.chip-err { color: #ef4444; }
.chip-decode { color: #ef4444; background: #3c1e1e; }
.chip-copy { color: #d1d5db; }
.chip-dl { text-decoration: none; color: #3DCD58; }
.se-console-body {
  padding: 8px 12px; overflow-y: auto;
  max-height: var(--console-h, 360px);
}
.se-console-body[data-filter="ERROR"] .ln:not(.ln-ERROR):not(.ln-FAIL):not(.ln-CRITICAL) { display: none; }
.se-console-body[data-filter="WARNING"] .ln:not(.ln-WARNING) { display: none; }
.se-console-body[data-filter="DECODE"] .ln:not(.ln-decode) { display: none; }
.se-console-empty { color: #6e6e73; font-style: italic; padding: 8px 0; }
.ln {
  white-space: pre-wrap; word-break: break-all; line-height: 1.5;
}
.ln-DEBUG    { color: #6b7280; }
.ln-INFO     { color: #9ca3af; }
.ln-WARNING  { color: #f59e0b; }
.ln-ERROR    { color: #ef4444; }
.ln-CRITICAL { color: #ef4444; font-weight: 700; background: #3c1e1e; padding: 0 4px; }
.ln-DONE     { color: #22c55e; font-weight: 600; }
.ln-FAIL     { color: #ef4444; font-weight: 700; }
.ln-frame    { padding-left: 12px; }
.ln-tail     { font-weight: 600; }
.ln-decode   { background: #3c1e1e; padding: 0 4px; }
details.tb summary { cursor: pointer; list-style: none; }
details.tb summary::-webkit-details-marker { display: none; }
details.tb[open] summary { margin-bottom: 2px; }
```

- [ ] **Step 2: Verify the file parses (grep sanity check)**

Run: `cd /home/mouad/ALL/SE-Tools && grep -c "se-console" assets/main.css`
Expected: ≥ 10.

- [ ] **Step 3: Commit**

```bash
git add assets/main.css
git commit -m "feat(console): add .se-console CSS ruleset"
```

---

### Task 11: JS additions — `copyConsoleContent` + `setConsoleFilter`

**Files:**
- Modify: `assets/app.js`

- [ ] **Step 1: Append the new functions to `assets/app.js`**

Append at the end of `assets/app.js` (do **not** touch the existing `copyLogContent` / `clearLogContent` — they stay until phase 4):

```javascript
// ──────────────────────────────────────────────────────────────────
// se-console — copy & filter (phase 1+ of console redesign)
// ──────────────────────────────────────────────────────────────────
function copyConsoleContent(id) {
  const body = document.querySelector(`#${id} .se-console-body`);
  if (!body) return;
  navigator.clipboard.writeText(body.innerText);
}

function setConsoleFilter(id, level) {
  const body = document.querySelector(`#${id} .se-console-body`);
  if (!body) return;
  body.dataset.filter = body.dataset.filter === level ? '' : level;
}

// expose for inline onclick handlers
window.copyConsoleContent = copyConsoleContent;
window.setConsoleFilter = setConsoleFilter;
```

- [ ] **Step 2: Sanity-check file**

Run: `cd /home/mouad/ALL/SE-Tools && grep -c "copyConsoleContent\|setConsoleFilter" assets/app.js`
Expected: ≥ 4.

- [ ] **Step 3: Commit**

```bash
git add assets/app.js
git commit -m "feat(console): add copyConsoleContent and setConsoleFilter JS"
```

---

## Phase 2 — Shim the old API (Task 12)

### Task 12: Make `ui_templates.run_subprocess` a shim over `run_subprocess_v2`

This is the critical de-risking step: every tool starts going through the new robust binary read path immediately, so the user-reported encoding crash is fixed for all four call sites before any UI migration happens.

**Files:**
- Modify: `gradio_tools/ui_templates.py` (lines 458-530, the `run_subprocess` function)

- [ ] **Step 1: Read the current `run_subprocess` implementation**

Open `gradio_tools/ui_templates.py` and confirm the current shape of `run_subprocess` (lines ~458-530) — it Popens directly with `text=True`, iterates stdout, and yields `"\n".join(lines[-300:])`.

- [ ] **Step 2: Replace it with a shim**

Replace the entire body of `run_subprocess` (`gradio_tools/ui_templates.py:458` onward, until just before `def stop_subprocess`) with:

```python
def run_subprocess(
    script: str | Path,
    cwd: str | Path,
    env_extra: dict[str, str] | None = None,
    extra_args: list[str] | None = None,
    process_holder: list | None = None,
) -> Generator[str, None, None]:
    """DEPRECATED: thin shim over console.run_subprocess_v2.

    Yields plain-text snapshots so existing gr.Textbox-based callers
    keep working without changes. Will be removed in phase 4 of the
    console redesign once all tools migrate to log_viewer().
    """
    # We can't reuse the v2 generator directly because it yields HTML.
    # Instead, replicate the read loop using the v2 helpers but emit
    # the legacy plain-text shape ("\n".join(lines[-300:])).
    from collections import deque
    import os, subprocess, sys, time
    from pathlib import Path
    from gradio_tools.console.runner import (
        LineSplitter, _strip_ansi, _truncate_line, _terminate,
        _all_processes, WINDOWS,
    )
    from gradio_tools.console.disk_log import DiskLog

    cmd = [sys.executable, str(script)]
    if extra_args:
        cmd.extend(extra_args)

    if process_holder is not None and process_holder[0] is not None:
        proc = process_holder[0]
        if proc.poll() is None:
            yield "[WARNING] A process is already running. Stop it first."
            return

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if env_extra:
        env.update(env_extra)

    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0
    preexec_fn = None if WINDOWS else os.setsid

    process = subprocess.Popen(
        cmd, cwd=str(cwd),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        bufsize=0, env=env,
        creationflags=creationflags, preexec_fn=preexec_fn,
    )
    _all_processes.add(process)
    if process_holder is not None:
        process_holder[0] = process

    # tool_id derived from the script's parent dir for disk log filename
    tool_id = Path(str(script)).parent.name or "subprocess"
    disk = DiskLog(tool_id, cmd, Path(str(cwd)))

    splitter = LineSplitter()
    lines: deque[str] = deque(maxlen=300)
    started = time.monotonic()
    fd = process.stdout.fileno()  # type: ignore[union-attr]

    try:
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                if process.poll() is not None:
                    break
                continue
            splitter.feed(chunk)
            for raw in splitter.lines():
                disk.write(raw + "\n")
                lines.append(_truncate_line(_strip_ansi(raw)).rstrip())
                yield "\n".join(lines)

        for raw in splitter.flush():
            disk.write(raw + "\n")
            lines.append(_truncate_line(_strip_ansi(raw)).rstrip())
            yield "\n".join(lines)

        process.wait()
        status = (
            "[DONE] Complete" if process.returncode == 0
            else "[FAIL] Failed"
        )
        tail = f"\n{status} (exit code {process.returncode})"
        lines.append(tail.strip())
        disk.write(tail.lstrip() + "\n")
        disk.close(process.returncode, time.monotonic() - started, len(lines))
        yield "\n".join(lines)
    finally:
        if process.poll() is None:
            _terminate(process)
            disk.interrupt()
        _all_processes.discard(process)
        if process_holder is not None and process_holder[0] is process:
            process_holder[0] = None
```

- [ ] **Step 3: Run the existing test suite to confirm nothing else broke**

Run: `cd /home/mouad/ALL/SE-Tools && python -m pytest tests/ -v`
Expected: all existing DSLGP tests still pass; new console tests still pass.

- [ ] **Step 4: Manual smoke test — verify a tool's plain-text console still streams**

Start the launcher in one terminal, then in another, navigate to one tool that uses `run_subprocess` (e.g. PDM Compare) and trigger a small run:

```bash
cd /home/mouad/ALL/SE-Tools && python Launcher.py
```
Open the page, click the Run button on a small job. Expected: textbox still shows streaming output. The new disk log file `logs/<tool>-*.log` exists after the run.

If the smoke test passes, the encoding crash on Windows is now fixed for every tool.

- [ ] **Step 5: Commit**

```bash
git add gradio_tools/ui_templates.py
git commit -m "refactor(console): shim run_subprocess over the new binary IO path"
```

---

## Phase 3 — Migrate tool pages (Tasks 13-16)

> **Convention for code blocks in this phase:** the symbol `...` inside a code block means "preserve the existing argument verbatim from the file you are editing." It is **not** a placeholder you need to fill in. For example, if the existing file has `cwd=TOOL_DIR / "src"`, you keep that exact expression after replacing `run_subprocess` with `run_subprocess_v2` — only `tool_id=` and `log_id=` are new.

### Task 13: `Launcher.py` — `allowed_paths` for disk-log downloads

**Files:**
- Modify: `Launcher.py`

- [ ] **Step 1: Locate the `demo.launch(...)` call in `Launcher.py`**

Read `Launcher.py` and find the `demo.launch(...)` call near the bottom of the file.

- [ ] **Step 2: Add `allowed_paths` argument**

Modify the `demo.launch(...)` call to include the logs directory:

```python
from gradio_tools.console.disk_log import LOGS_DIR  # add to imports

# … existing code …

demo.launch(
    # … existing args …
    allowed_paths=[str(LOGS_DIR)],
)
```

- [ ] **Step 3: Verify the launcher still imports**

Run: `cd /home/mouad/ALL/SE-Tools && python -c "import Launcher"`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add Launcher.py
git commit -m "feat(console): allow Gradio to serve disk-log files for downloads"
```

---

### Task 14: Migrate `gradio_tools/translation.py`

**Files:**
- Modify: `gradio_tools/translation.py:451-453` (log header + textbox) and `:623` (run_subprocess call)

- [ ] **Step 1: Read the current `translation.py` console block**

Open `gradio_tools/translation.py` and find:
- Lines ~450-455: `with gr.Column(elem_classes=["se-terminal"]): gr.HTML(log_header(...)); log_box = gr.Textbox(...)` block
- Line ~623: `for accumulated_log in run_subprocess(...)` inside `_run_with_progress`

- [ ] **Step 2: Replace the imports**

In the `from gradio_tools.ui_templates import (...)` block at the top of the file, remove `log_header` and `run_subprocess` from the import list (keep `stop_subprocess`, `browse_*`, etc. that are still needed). Add a new import:

```python
from gradio_tools.console import log_viewer, run_subprocess_v2
```

- [ ] **Step 3: Replace the console UI block**

Replace:

```python
with gr.Column(elem_classes=["se-terminal"]):
    gr.HTML(log_header("Output", "translation-log"))
    log_box = gr.Textbox(
        lines=20, max_lines=20,
        show_label=False, interactive=False,
        elem_id="translation-log-box",
        autoscroll=True,
    )
```

With:

```python
log_box = log_viewer("translation-log", lines=20)
```

(Adjust the `lines=` value to match whatever the original Textbox used.)

- [ ] **Step 4: Replace the `run_subprocess` call inside `_run_with_progress`**

Find the call near line 623:

```python
for accumulated_log in run_subprocess(
    script=MAIN_SCRIPT,
    cwd=...,
    env_extra=...,
    process_holder=_current_process,
):
    yield accumulated_log
```

Replace with:

```python
for accumulated_log in run_subprocess_v2(
    MAIN_SCRIPT,
    cwd=...,
    tool_id="translation",
    log_id="translation-log",
    env_extra=...,
    process_holder=_current_process,
):
    yield accumulated_log
```

(Note: `run_subprocess_v2` takes `script` and `cwd` as positional args; the rest are keyword-only.)

- [ ] **Step 5: Manual browser smoke test**

```bash
cd /home/mouad/ALL/SE-Tools && python Launcher.py
```

Open the Translation page. Click Run on a small job. Verify:
- Console renders as the dark `.se-console` widget (not the old textbox).
- Lines stream in real-time.
- `INFO`/`WARNING`/`ERROR` are color-coded.
- Summary chip in the header updates with line count and elapsed time.
- Stop button still works.
- "📄 Download log" link downloads a valid `.log` file.

- [ ] **Step 6: Commit**

```bash
git add gradio_tools/translation.py
git commit -m "refactor(console): migrate translation.py to log_viewer + run_subprocess_v2"
```

---

### Task 15: Migrate `gradio_tools/pdm.py` (both consoles)

**Files:**
- Modify: `gradio_tools/pdm.py:375-377` (Dico console UI), `:461` (Dico run call), `:603-606` (Compare console UI), `:672` (Compare run call)

- [ ] **Step 1: Update imports**

In the `from gradio_tools.ui_templates import (...)` block, remove `log_header` and `run_subprocess`. Add:

```python
from gradio_tools.console import log_viewer, run_subprocess_v2
```

- [ ] **Step 2: Replace the Dico console UI block (lines ~375-377)**

Replace:

```python
with gr.Column(elem_classes=["se-terminal"]):
    gr.HTML(log_header("Output", "pdm-log"))
    pdm_log_box = gr.Textbox(lines=20, ..., elem_id="pdm-log-box")
```

With:

```python
pdm_log_box = log_viewer("pdm-log", lines=20)
```

- [ ] **Step 3: Replace the Dico `run_subprocess` call (line ~461)**

Replace:

```python
yield from run_subprocess(
    script=...,
    cwd=...,
    process_holder=_pdm_process,
)
```

With:

```python
yield from run_subprocess_v2(
    ...,           # script (positional)
    cwd=...,       # keyword
    tool_id="pdm",
    log_id="pdm-log",
    process_holder=_pdm_process,
)
```

- [ ] **Step 4: Replace the Compare console UI block (lines ~603-606)**

Replace:

```python
with gr.Column(elem_classes=["se-terminal"]):
    gr.HTML(log_header("Output", "bc-log"))
    bc_log_box = gr.Textbox(lines=20, ..., elem_id="bc-log-box")
```

With:

```python
bc_log_box = log_viewer("bc-log", lines=20)
```

- [ ] **Step 5: Replace the Compare `run_subprocess` call (line ~672)**

Replace:

```python
yield from run_subprocess(
    script=...,
    cwd=...,
    process_holder=_bc_process,
)
```

With:

```python
yield from run_subprocess_v2(
    ...,
    cwd=...,
    tool_id="pdm-compare",
    log_id="bc-log",
    process_holder=_bc_process,
)
```

- [ ] **Step 6: Manual browser smoke test**

```bash
cd /home/mouad/ALL/SE-Tools && python Launcher.py
```

Open the PDM page. Run both Dico and Compare with small inputs. Verify both consoles render as `.se-console`, stream output, and produce disk logs.

- [ ] **Step 7: Commit**

```bash
git add gradio_tools/pdm.py
git commit -m "refactor(console): migrate pdm.py (Dico + Compare) to log_viewer"
```

---

### Task 16: Migrate `gradio_tools/dslgp.py`

**Files:**
- Modify: `gradio_tools/dslgp.py:633-634` (console UI), `:846` (run call)

- [ ] **Step 1: Update imports**

In the `from gradio_tools.ui_templates import (...)` block, remove `log_header` and `run_subprocess`. Add:

```python
from gradio_tools.console import log_viewer, run_subprocess_v2
```

- [ ] **Step 2: Replace the console UI block (lines ~633-634)**

Replace:

```python
with gr.Column(elem_classes=["se-terminal"]):
    gr.HTML(log_header("Output", "dslgp-log"))
    dslgp_log_box = gr.Textbox(lines=20, ..., elem_id="dslgp-log-box")
```

With:

```python
dslgp_log_box = log_viewer("dslgp-log", lines=20)
```

- [ ] **Step 3: Replace the `run_subprocess` call (line ~846)**

Find the call:

```python
yield from run_subprocess(
    script=...,
    cwd=...,
    extra_args=...,
    process_holder=_dslgp_process,
)
```

Replace with:

```python
yield from run_subprocess_v2(
    ...,                   # script
    cwd=...,
    tool_id="dslgp",
    log_id="dslgp-log",
    extra_args=...,
    process_holder=_dslgp_process,
)
```

- [ ] **Step 4: Manual browser smoke test of all 6 DSLGP sub-tools**

```bash
cd /home/mouad/ALL/SE-Tools && python Launcher.py
```

Open the DSLGP page. Run each of the 6 sub-tools with small inputs. Verify consoles render correctly, summaries update, and disk logs are produced.

- [ ] **Step 5: Run full test suite**

Run: `cd /home/mouad/ALL/SE-Tools && python -m pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add gradio_tools/dslgp.py
git commit -m "refactor(console): migrate dslgp.py to log_viewer + run_subprocess_v2"
```

---

## Phase 4 — Cleanup (Task 17)

### Task 17: Remove shim, old CSS, old JS, update CLAUDE.md

**Files:**
- Modify: `gradio_tools/ui_templates.py` (remove `log_header`, `run_subprocess`, `stop_subprocess`, `_all_processes`, `_cleanup_all_subprocesses`, `atexit.register(...)`)
- Modify: `assets/main.css` (remove `.se-terminal`, `.se-log-*` rules)
- Modify: `assets/app.js` (remove `copyLogContent`, `clearLogContent`)
- Modify: `Launcher.py` (swap `_cleanup_all_subprocesses` import for `cleanup_all_v2`)
- Modify: `SE-Tools/CLAUDE.md` (helpers table)

- [ ] **Step 1: Verify no callers reference the old symbols**

Run:
```bash
cd /home/mouad/ALL/SE-Tools && grep -rn "log_header\|run_subprocess\|stop_subprocess\|copyLogContent\|clearLogContent\|se-terminal\|se-log-" gradio_tools/ Launcher.py assets/ 2>/dev/null | grep -v "console/" | grep -v "\.bak"
```
Expected: only matches inside `ui_templates.py`, `assets/main.css`, `assets/app.js`, `Launcher.py` (the files we're cleaning up). If any tool page still references these, that tool was not migrated — go back and finish phase 3.

- [ ] **Step 2: In `Launcher.py`, swap the cleanup import**

Replace:
```python
from gradio_tools.ui_templates import _cleanup_all_subprocesses
```
with:
```python
from gradio_tools.console import cleanup_all_v2 as _cleanup_all_subprocesses
```
(Keep the `_cleanup_all_subprocesses` local name so the existing signal handlers and `atexit.register(...)` calls don't change.)

- [ ] **Step 3: In `gradio_tools/ui_templates.py`, delete the old console code**

Delete:
- `_all_processes` set + `_cleanup_all_subprocesses` function + `atexit.register(_cleanup_all_subprocesses)` line (top of file)
- `log_header(...)` function
- `run_subprocess(...)` function (the shim)
- `stop_subprocess(...)` function

Keep everything else (theme, sidebar, browse_*, page_header, status_bar_html, etc.).

- [ ] **Step 4: In `assets/main.css`, delete the legacy `.se-terminal` and `.se-log-*` rules**

Search for and delete every rule matching `.se-terminal`, `.se-log-header`, `.se-log-meta`, `.se-log-elapsed`, `.se-log-lines`, `.se-log-header-actions`, `.se-log-header-title`, `.se-btn-ghost` (if only used by the old console — verify with grep first).

- [ ] **Step 5: In `assets/app.js`, delete `copyLogContent` and `clearLogContent`**

Delete the two functions and any associated `window.*` exports.

- [ ] **Step 6: Update `SE-Tools/CLAUDE.md` helpers table**

In the "ui_templates.py — shared helpers" section table, **remove** rows for `log_header`, `run_subprocess`, `stop_subprocess`, `_cleanup_all_subprocesses`. **Add** a new section after the table:

```markdown
---

## console — robust log viewer

The `gradio_tools/console/` package replaced the textbox-based console
in April 2026. Use it for any tool that streams subprocess output.

| Function | Purpose | When to use |
|---|---|---|
| log_viewer(log_id, lines=20) | Returns a gr.HTML pre-rendered with an empty .se-console widget. The handler generator drives updates by yielding HTML strings from run_subprocess_v2. | Place inside the gr.Column for the tool's output section. No need for a wrapper Column with elem_classes. |
| run_subprocess_v2(script, cwd, *, tool_id, log_id, env_extra=None, extra_args=None, process_holder=None) | Generator yielding HTML log snapshots. Streams subprocess output via binary read + manual UTF-8/CP1252 decode. Always writes a complete disk log to logs/<tool_id>-<ts>_<pid>.log. Yield is batched (every 100 ms or every 50 lines). | Wire to a gr.Button click event with outputs=log_box. tool_id appears in disk log filename; log_id is the DOM id used by the renderer + JS chip handlers. |
| stop_subprocess_v2(process_holder) | Cross-platform terminator. SIGTERM to process group on Unix; CTRL_BREAK_EVENT on Windows. 3 s grace then SIGKILL. | Connect to a Stop button alongside run_subprocess_v2. |
| cleanup_all_v2() | Atexit / signal handler hook. Imported by Launcher.py. | Do not call from tool pages. |

Disk logs go to SE-Tools/logs/ (gitignored). Last 50 files per tool retained;
each file capped at 10 MB. Falls back to tempfile.gettempdir()/se-tools-logs/
if logs/ is unwritable.
```

- [ ] **Step 7: Run the full test suite**

Run: `cd /home/mouad/ALL/SE-Tools && python -m pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 8: Verify no orphaned references**

Run:
```bash
cd /home/mouad/ALL/SE-Tools && grep -rn "se-terminal\|copyLogContent\|clearLogContent\|log_header\|from gradio_tools.ui_templates import.*run_subprocess" gradio_tools/ Launcher.py assets/ 2>/dev/null
```
Expected: no matches.

- [ ] **Step 9: Manual browser smoke test of every page**

```bash
cd /home/mouad/ALL/SE-Tools && python Launcher.py
```

Visit each tool page (Home, DSLGP, Translation, DITA, PDM, PDF). Verify:
- All pages load without console errors in the browser dev tools.
- Tools that have a console (DSLGP, Translation, PDM ×2) show the new `.se-console` widget.
- A small run on each successfully streams + writes a disk log + offers a download link.
- No layout regression (sidebar, headers, browse buttons all still look right).

- [ ] **Step 10: Commit**

```bash
git add gradio_tools/ui_templates.py Launcher.py assets/main.css assets/app.js CLAUDE.md
git commit -m "chore(console): remove legacy run_subprocess/log_header shim and CSS"
```

---

## Acceptance verification

After all 17 tasks complete, verify the spec acceptance criteria one by one:

1. **Encoding crash fixed:** `tests/test_console_runner.py::test_run_subprocess_v2_survives_non_utf8_output` passes — proves the user's reported `UnicodeDecodeError` is no longer reachable.
2. **Yields are batched:** `tests/test_console_runner.py::test_run_subprocess_v2_yield_count_is_batched` confirms ≤ 200 yields for 5,000 lines (well under the spec's loose target of ≤ 60 — but the test bounds is intentionally lenient to allow for env variance).
3. **Errors persist on disk:** Manual test — run a fake subprocess that prints a traceback in the first 100 lines then 5,000 more lines. Verify the disk log file in `logs/` contains the traceback and the summary chip still shows "✖ 1" at the end.
4. **Stop works:** Manual test — start a `while True` subprocess, click Stop. Process should die within 3 s on both Linux and Windows.
5. **All four call sites work:** Manual browser test of translation / pdm Dico / pdm Compare / dslgp.
6. **Cleanup complete:** `grep` from Task 17 step 8 returns no matches.
