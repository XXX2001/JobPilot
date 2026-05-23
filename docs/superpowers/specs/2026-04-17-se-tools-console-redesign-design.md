# SE-Tools Console Redesign

**Status:** approved (brainstorming complete, awaiting implementation plan)
**Date:** 2026-04-17
**Scope:** SE-Tools Gradio app — replace `run_subprocess` + `log_header` + `gr.Textbox` console with a robust, observable, performant log viewer used by every tool that streams subprocess output.

---

## Why

The current console (`gradio_tools/ui_templates.py:371` `log_header`, `:458` `run_subprocess`, paired with a `gr.Textbox` in each tool page) has three concrete problems:

1. **It crashes.** A real Windows traceback reported by the user — `UnicodeDecodeError: 'charmap' codec can't decode byte 0x8f` at `ui_templates.py:515` inside `for line in iter(process.stdout.readline, "")`. Root cause: the child process emits non-UTF-8 bytes (the C++ `vf_ipc_mon_proxy.cpp` proxy writes CP1252 directly to stdout), and Python's `TextIOWrapper` around `process.stdout` decodes them with whatever the host locale is. The current `encoding="utf-8", errors="replace"` arguments to `Popen` are not enough on Windows when the child writes raw bytes that violate UTF-8.
2. **It hides problems.** Errors, warnings, and tracebacks render as undifferentiated grey monospace text. A 5,000-line run with one error in the first 200 lines loses that error to the rolling 300-line buffer, with no summary, no filter, and no retention.
3. **It is slow.** `run_subprocess` yields `"\n".join(lines[-300:])` on every line — O(n²) string-building, full textbox re-render per line. A burst of 5,000 lines in 2 seconds creates 5,000 yields and ~150 MB of redundant string allocation.

This design replaces all three at once.

---

## High-level design

A new `gradio_tools/console/` package owns parsing, rendering, subprocess streaming, and disk persistence. Tool pages swap two lines: `gr.HTML(log_header(...)) + gr.Textbox(...)` becomes `log_viewer(...)` (a `gr.HTML` component). The streaming generator (`run_subprocess_v2`) keeps the same string-yield interface, so handler code in tool pages does not change.

```
gradio_tools/
├── ui_templates.py         # existing — log_header() and run_subprocess()
│                           # become thin shims over the new package during
│                           # phased migration, then deleted in cleanup
└── console/                # NEW
    ├── __init__.py         # re-exports: log_viewer, run_subprocess_v2
    ├── parser.py           # bytes/str → ParsedLine (level, kind, text, seq)
    ├── renderer.py         # ParsedLine[] → HTML (with summary chip + filters)
    ├── runner.py           # run_subprocess_v2 generator
    └── disk_log.py         # rotating logs/<tool>-<ts>_<pid>.log writer
```

```
child stdout (bytes)
        │
        ▼
   os.read(fd, 64KB)         ← unbuffered binary; we own decoding
        │
        ▼
   _decode (utf-8 → cp1252 → utf-8/replace fallback chain)
        │
        ▼
   line splitter (handles partial UTF-8 across reads, splits on \n)
        │              │
        │              └────────────────► disk_log.write(raw decoded)
        ▼
   parser.parse(line)        ← regex first-match-wins, traceback state machine
        │
        ▼
   ParsedLine ring buffer (collections.deque, maxlen=500)
        │
        ▼
   _BatchedYielder (every 100 ms OR every 50 lines, whichever first)
        │
        ▼
   renderer.render(buffer, summary)  → HTML string
        │
        ▼
   yield to gr.HTML
```

---

## Components

### `console/parser.py`

```python
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
    TRACEBACK_HEADER = "TRACEBACK_HEADER"     # the "Traceback ..." line
    TRACEBACK_FRAME = "TRACEBACK_FRAME"       # File "...", indented continuation
    TRACEBACK_TAIL = "TRACEBACK_TAIL"         # "SomeError: ..." closes the block
    DECODE_ERROR = "DECODE_ERROR"             # special highlight for encoding crashes

@dataclass(frozen=True)
class ParsedLine:
    text: str             # decoded, ANSI-stripped, HTML-unescaped
    level: Level
    kind: Kind
    seq: int              # monotonic per-run line number
    is_decode_error: bool # additive flag — set when text matches the decode-error pattern,
                          # regardless of the line's level/kind. Used by the renderer for
                          # the 🔤 chip and CSS class without disturbing the traceback
                          # state machine.

class Parser:
    def __init__(self) -> None:
        self._in_traceback: bool = False
        self._seq: int = 0
    def parse(self, raw_line: str) -> ParsedLine: ...
```

Detection is one regex pass per line, ordered first-match-wins. The traceback state machine flips `_in_traceback = True` on `Traceback (most recent call last):` and stays open until a non-indented line that matches `^[\w.]+(Error|Exception)\b.*$` (the tail) — that line is `TRACEBACK_TAIL`, after which `_in_traceback` goes back to False. Any line received while `_in_traceback` is True and matched `^\s` is a `TRACEBACK_FRAME`.

`UnicodeDecodeError`, `UnicodeEncodeError`, or `'charmap' codec` anywhere in the text sets `is_decode_error = True` *additively* — independent of the line's `level` and `kind`. This matters because the user-reported crash arrived as the *tail* of a traceback (`UnicodeDecodeError: 'charmap' ...`), so it is classified `kind=TRACEBACK_TAIL`, `level=ERROR`, but **also** carries `is_decode_error = True`. The renderer reads this flag to add the `ln-decode` CSS class and to bump `Summary.decode_error_count`.

### `console/renderer.py`

```python
@dataclass
class Summary:
    elapsed_seconds: float
    line_count: int
    warn_count: int
    error_count: int
    decode_error_count: int
    is_running: bool
    download_path: Path | None

def render(buffer: deque[ParsedLine], summary: Summary, log_id: str, filter_level: str | None = None) -> str: ...
```

Emits a single HTML string:

```html
<div class="se-console" id="{log_id}">
  <div class="se-console-header">
    <span class="se-console-title">Output<span class="dot {running?}"></span></span>
    <span class="se-console-meta">12.4s · 1,204 lines</span>
    <span class="se-console-chips">
      <button class="chip chip-warn" onclick="setConsoleFilter('{log_id}','WARNING')">⚠ 3</button>
      <button class="chip chip-err" onclick="setConsoleFilter('{log_id}','ERROR')">✖ 1</button>
      <button class="chip chip-decode" onclick="setConsoleFilter('{log_id}','DECODE')">🔤 1</button>   <!-- only if decode_error_count > 0 -->
      <button class="chip chip-copy" onclick="copyConsoleContent('{log_id}')">⧉ Copy</button>
      <a class="chip chip-dl" href="/file={download_path}">📄 Download log</a>
    </span>
  </div>
  <div class="se-console-body" data-filter="{filter_level or ''}">
    <div class="ln ln-INFO">…</div>
    <details class="tb" open>
      <summary class="ln ln-ERROR">Traceback (most recent call last):</summary>
      <div class="ln ln-ERROR ln-frame">  File "...", line 515, in run_subprocess</div>
      ...
      <div class="ln ln-ERROR ln-tail ln-decode">UnicodeDecodeError: 'charmap' codec can't decode byte 0x8f...</div>
    </details>
    <div class="ln ln-FAIL">[FAIL] Failed (exit code 1)</div>
  </div>
</div>
```

Filters use CSS only: `.se-console-body[data-filter="ERROR"] .ln:not(.ln-ERROR){display:none}` — clicking a chip toggles the data attribute via inline JS, no Python round-trip.

Tracebacks longer than 5 frames open as `<details>` with `open` attribute removed (collapsed by default once they get noisy). Closed `<details>` body does not participate in browser layout — keeps repaint cheap.

The renderer caches the previous output (`@functools.lru_cache(maxsize=1)`) keyed on `(buffer_revision, summary, filter)` so repeated calls during the same yield window don't re-render. The buffer carries a `revision` counter incremented on every parse.

### `console/runner.py`

```python
def run_subprocess_v2(
    script: str | Path,
    cwd: str | Path,
    *,
    tool_id: str,                    # used for disk log filename
    log_id: str,                     # used for renderer / DOM id
    env_extra: dict[str, str] | None = None,
    extra_args: list[str] | None = None,
    process_holder: list | None = None,
) -> Generator[str, None, None]:
    ...
```

Yields HTML strings (renderer output). Same calling shape as old `run_subprocess` — replaces `script + cwd + …` positional/keyword args; adds `tool_id` and `log_id` (both required for disk log filename and renderer DOM id).

Internals:

```python
process = subprocess.Popen(
    cmd,
    cwd=str(cwd),
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=False,                      # binary — we decode
    bufsize=0,
    env=env,
    creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0),
)

def _decode(b: bytes) -> str:
    for enc in ("utf-8", "cp1252"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")

env["PYTHONIOENCODING"] = "utf-8"
env["PYTHONUTF8"] = "1"
env["PYTHONUNBUFFERED"] = "1"
```

Read loop uses `os.read(process.stdout.fileno(), 65536)`, a `bytearray` accumulator, and a `\n` splitter. Trailing partial bytes (incomplete UTF-8 sequence at chunk boundary, or a line without a newline yet) stay in the accumulator until the next read.

Lines longer than `MAX_LINE = 16 * 1024` bytes get hard-split with a marker `… [line truncated, full text in disk log]`. Disk log keeps the full untruncated text.

ANSI escape codes (regex `\x1b\[[0-9;]*[A-Za-z]`) are stripped before parsing. Disk log keeps the raw codes.

Carriage returns not followed by newline (`\r` for in-place progress bars) replace the last `ParsedLine` in the ring buffer rather than appending. Disk log keeps raw `\r`.

`_BatchedYielder` collects new `ParsedLine`s and emits a fresh HTML render every 100 ms OR every 50 new lines, whichever comes first. On generator completion (`finally:` block) one final flush fires so trailing-edge lines are not lost.

Termination: stop button calls `stop_subprocess_v2(process_holder)` which sends `SIGTERM` to the *process group* on Unix (`os.killpg(os.getpgid(proc.pid), SIGTERM)`) and `CTRL_BREAK_EVENT` on Windows (`proc.send_signal(signal.CTRL_BREAK_EVENT)`), waits 3 seconds, then escalates to `SIGKILL` / `proc.kill()`. The cleanup runs in a `try/finally` block in the generator so it fires even if Gradio interrupts the generator (user navigates away mid-run).

### `console/disk_log.py`

```python
class DiskLog:
    def __init__(self, tool_id: str, cmd: list[str], cwd: Path) -> None: ...
    @property
    def path(self) -> Path: ...
    def write(self, raw: str) -> None: ...   # appends, flushes
    def close(self, exit_code: int, duration: float, line_count: int) -> None: ...
    def interrupt(self) -> None: ...         # writes "# interrupted" footer
```

- File path: `LOGS_DIR / f"{tool_id}-{datetime.now():%Y-%m-%d_%H%M%S}_{os.getpid()}.log"`
- `LOGS_DIR` resolves to `PROJECT_ROOT / "logs"`; if `mkdir` fails or the dir is unwritable, falls back to `Path(tempfile.gettempdir()) / "se-tools-logs"` and emits a single in-UI WARNING with the fallback path.
- Header (4 commented lines) written on construction. Footer written on `close()` or `interrupt()`.
- `write()` uses `mode="ab"` (binary append, encodes with utf-8 errors=replace) — avoids Windows exclusive-lock issues with `mode="a"`.
- All file ops wrapped in `try/except OSError`. First failure downgrades to in-UI WARNING and switches to no-op writes for the rest of the run; stream continues uninterrupted.
- Per-file cap `MAX_LOG_BYTES = 10 * 1024 * 1024` — once exceeded, append `# … truncated, log too long` and stop writing.
- Rotation: on construction, list all files matching `{tool_id}-*.log`, sort by mtime, delete oldest if count > 50.

### `log_viewer()` helper (in `console/__init__.py`)

```python
def log_viewer(log_id: str, *, lines: int = 20) -> gr.HTML: ...
```

Returns a `gr.HTML` component pre-rendered with an empty state (`<div class="se-console" id="{log_id}"><div class="se-console-empty">No output yet.</div></div>`). The `lines` argument controls CSS `max-height` (each line ≈ 18 px). Tool pages put this inside an existing `gr.Column` — no need for `elem_classes=["se-terminal"]` wrapper anymore; the `.se-console` class owns its own background, padding, and border.

---

## Detection rules (parser regex table)

Ordered first-match-wins. All matches are case-sensitive on the level keyword (so `informational` does not match `INFO`).

| Order | Pattern | level | kind | Notes |
|---|---|---|---|---|
| 1 | `^Traceback \(most recent call last\):` | ERROR | TRACEBACK_HEADER | Opens traceback state |
| 2 | (state machine) line received while `_in_traceback`, matches `^\s` | ERROR | TRACEBACK_FRAME | |
| 3 | (state machine) line received while `_in_traceback`, matches `^[\w.]+(Error\|Exception)\b` | ERROR | TRACEBACK_TAIL | Closes traceback state |
| (additive) | `(UnicodeDecodeError\|UnicodeEncodeError\|'charmap' codec)` anywhere | (unchanged) | (unchanged) | Sets `is_decode_error=True` in addition to whatever level/kind the line received from the rules below. Not part of the first-match-wins chain. |
| 4 | `\b(CRITICAL\|FATAL)\b` | CRITICAL | NORMAL | |
| 5 | `\b(ERROR\|ERR)\b` | ERROR | NORMAL | |
| 6 | `\b(WARNING\|WARN)\b` | WARNING | NORMAL | |
| 7 | `\bINFO\b` | INFO | NORMAL | |
| 8 | `\bDEBUG\b` | DEBUG | NORMAL | |
| 9 | `^\[DONE\]` | DONE | NORMAL | Emitted by `run_subprocess_v2` itself on success |
| 10 | `^\[FAIL\]` | FAIL | NORMAL | Emitted by `run_subprocess_v2` itself on failure |
| 11 | (default) | PLAIN | NORMAL | |

---

## Robustness — failure modes and mitigations

| Failure mode | Mitigation |
|---|---|
| Child writes non-UTF-8 bytes (CP1252, latin-1, raw binary) | Binary `Popen` + manual decode chain `utf-8 → cp1252 → utf-8/replace` |
| Child writes a 50 MB line with no `\n` | Hard-split at 16 KB with `[line truncated]` marker; full line in disk log |
| Child writes ANSI color codes | Regex strip before parsing; raw codes preserved on disk |
| Child writes `\r` for progress bars | Detect `\r` not followed by `\n`, replace last buffer line; raw on disk |
| Child closes stdout but stays alive | `os.read` returns `b""` → break loop → `wait(timeout)` → `kill()` if needed |
| Stop hit mid-traceback | SIGTERM to process group / `CTRL_BREAK_EVENT` on Windows; 3 s grace then SIGKILL |
| Generator interrupted by Gradio (navigate away) | `try/finally` in generator always calls `_terminate_clean()` |
| Stale `PYTHONIOENCODING` from parent shell | Force `PYTHONIOENCODING=utf-8` and `PYTHONUTF8=1` in child env so most apps emit UTF-8 in the first place |
| Partial UTF-8 sequence at chunk boundary | `bytearray` accumulator; only decode complete lines (split on `\n` first) |
| Disk log write fails (full disk, permission) | Catch `OSError` once, downgrade to in-UI WARNING, switch to no-op writes; stream continues |
| Two runs of same tool at the same instant | Filename includes PID + timestamp |
| Process killed externally (Task Manager) | `os.read` returns `b""`; exit code non-zero; renderer shows `[FAIL] Process terminated externally (exit -9)` |
| `logs/` not gitignored | Add `/logs/` to repo `.gitignore` as part of migration |
| User on read-only install dir | Fallback to `tempfile.gettempdir() / "se-tools-logs"`, log fallback path once |
| User downloads file mid-run | `flush()` after every write keeps file consistent; OS read of a being-written file is fine |
| Concurrent writes to same file | Each subprocess gets its own file (PID + timestamp), no contention by construction |
| Process killed before footer written | `interrupt()` writes `# interrupted at <ts>` footer in `finally:` block |
| Renderer receives lines containing literal `<script>` or other HTML | `html.escape()` every text segment before wrapping in `<div class="ln">`; never trust subprocess output |

---

## Performance

**Per-yield cost (target):**

- Buffer maintenance: `deque.append()` O(1)
- Parser: one regex pass per line, ~100 ns/line on a modern CPU
- Renderer: walk last 500 lines, emit ~50 KB HTML; cached on `(revision, summary, filter)` so repeated calls during the same window are free
- Yield: every 100 ms OR every 50 lines

**Measured targets (5,000-line burst over 2 s):**

| Metric | Current | Target | Mechanism |
|---|---|---|---|
| Yields per run | ~5,000 | ~50 | Time/line batching |
| Peak Python RAM (console alone) | ~300 KB string copies | <250 KB total | Ring buffer + cached HTML |
| Browser jank during burst | visible | imperceptible | Bounded HTML size + CSS-only filter |
| Time to first paint after click | ~50 ms | ≤100 ms (one batch) | First batch fires on time tick or first 50 lines |

**Slow-stream behavior** (1 line every 5 s): batch yields immediately on the time tick → user sees each line within 100 ms of arrival. Real-time feel preserved.

**Burst-end behavior:** `finally:` block does one final flush so the last sub-batch (e.g. last 13 lines arriving in the dying milliseconds) is rendered.

---

## Disk log

**Path:** `SE-Tools/logs/<tool-id>-<YYYY-MM-DD_HHMMSS>_<pid>.log` (gitignored).

**Header:**

```
# SE-Tools log — translation — started 2026-04-17 14:30:52
# cmd: python /home/.../src/Dicolabel_Trad/src/main.py --foo bar
# cwd: /home/.../src/Dicolabel_Trad
# python: 3.13.1 (CPython) on win32
```

**Footer (success / failure):**

```
# exit_code=0  duration=42.18s  lines=1204
```

**Footer (interrupted):**

```
# interrupted at 2026-04-17 14:31:34  duration=22.04s  lines=412
```

**Retention:** 50 most recent files per tool. Per-file cap 10 MB.

**Download:** the "📄 Download log" chip is a plain `<a href="/file={absolute_path}">` rendered inside the console HTML — no separate `gr.DownloadButton` component required. Gradio's built-in `/file=` route serves the file. Flushes after every write so a download triggered mid-run yields a valid (truncated-at-the-moment) file.

For Gradio to allow serving a file path it has not seen before, `LOGS_DIR` is added to `demo.launch(allowed_paths=[str(LOGS_DIR)])` in `Launcher.py` as part of the migration. (One small Launcher change beyond the per-tool console swap.)

---

## Migration plan

**Phase 1 — build the package.**
- Create `gradio_tools/console/` with all four modules.
- Add `tests/console_demo.py` — a tiny standalone Gradio page that drives a fake noisy subprocess (mixed levels, a traceback, a decode-error injection) for visual smoke testing.
- No tool page changes yet.

**Phase 2 — shim the old API.**
- Rewrite `ui_templates.run_subprocess` to call `run_subprocess_v2` internally and yield the old plain-text shape (`"\n".join(lines[-300:])`). Drop HTML, recreate textbox-friendly output.
- Rewrite `ui_templates.log_header` to emit the same static HTML it does today.
- **Effect: every tool now goes through the new robust binary read path; the encoding crash is fixed for all four call sites immediately, even though the UI hasn't changed.** This is a critical de-risking step — the user-reported bug is gone before any UI migration starts.

**Phase 3 — migrate one tool at a time, in this order:**

Before the first migration, make a one-time `Launcher.py` change: add `allowed_paths=[str(LOGS_DIR)]` to `demo.launch(...)` so Gradio's `/file=` route can serve disk log files.

1. `gradio_tools/translation.py:452` and `:623` — the file the user crash hit. Biggest visible win.
2. `gradio_tools/pdm.py:376/461` and `:605/672` — two consoles, mechanical changes.
3. `gradio_tools/dslgp.py:634, :846` — biggest file, riskiest.

For each tool the diff is roughly:

```diff
-with gr.Column(elem_classes=["se-terminal"]):
-    gr.HTML(log_header("Output", "translation-log"))
-    log_box = gr.Textbox(lines=20, ...)
+log_box = log_viewer("translation-log", lines=20)
```

Handler signatures unchanged (still `Generator[str, None, None]`). Replace `run_subprocess(...)` calls with `run_subprocess_v2(..., tool_id="translation", log_id="translation-log")`. Test in browser. Commit per tool.

**Phase 4 — cleanup.**
- Delete shimmed `run_subprocess` and `log_header` from `ui_templates.py`.
- Delete `.se-terminal`, `.se-log-header`, `.se-log-meta`, `.se-log-elapsed`, `.se-log-lines`, `.se-log-header-actions`, `.se-log-header-title` rules from `assets/main.css`.
- Delete `copyLogContent` / `clearLogContent` from `assets/app.js`.
- Update `SE-Tools/CLAUDE.md`'s helpers table (remove `log_header`, `run_subprocess`, `stop_subprocess`; add `log_viewer`, `run_subprocess_v2`, `stop_subprocess_v2`).

---

## CSS (assets/main.css additions)

New ruleset under a `.se-console` block. Reuses existing `--ok`, `--warn`, `--err` color tokens where they exist; adds new `--debug`, `--decode` tokens scoped to the console.

```css
.se-console {
  background: #1c1c1e;
  border-radius: 12px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12.5px;
  color: #d1d5db;
  overflow: hidden;
}
.se-console-header { display:flex; align-items:center; gap:12px; padding:8px 12px; border-bottom:1px solid #2c2c2e; }
.se-console-title { display:flex; align-items:center; gap:6px; font-weight:600; color:#f5f5f7; }
.se-console-title .dot { width:8px; height:8px; border-radius:50%; background:#48484a; }
.se-console-title .dot.running { background: var(--ok); animation: pulseDot 1.5s infinite; }
.se-console-meta { color:#86868b; font-size:11.5px; margin-right:auto; }
.se-console-chips { display:flex; gap:6px; }
.chip { padding:2px 8px; border-radius:10px; font-size:11px; cursor:pointer; border:1px solid transparent; background:#2c2c2e; color:#d1d5db; }
.chip-warn { color:#f59e0b; } .chip-err { color:#ef4444; } .chip-decode { color:#ef4444; background:#3c1e1e; }
.chip-copy { color:#d1d5db; }
.chip-dl { text-decoration:none; color:#3DCD58; }
.se-console-body { padding:8px 12px; overflow-y:auto; max-height: var(--console-h, 360px); }
.se-console-body[data-filter="ERROR"] .ln:not(.ln-ERROR):not(.ln-FAIL):not(.ln-CRITICAL) { display:none; }
.se-console-body[data-filter="WARNING"] .ln:not(.ln-WARNING) { display:none; }
.se-console-body[data-filter="DECODE"] .ln:not(.ln-decode) { display:none; }
.ln { white-space: pre-wrap; word-break: break-all; line-height: 1.5; }
.ln-DEBUG    { color:#6b7280; }
.ln-INFO     { color:#9ca3af; }
.ln-WARNING  { color:#f59e0b; }
.ln-ERROR    { color:#ef4444; }
.ln-CRITICAL { color:#ef4444; font-weight:700; background:#3c1e1e; padding:0 4px; }
.ln-DONE     { color:#22c55e; font-weight:600; }
.ln-FAIL     { color:#ef4444; font-weight:700; }
.ln-frame    { padding-left:12px; }
.ln-tail     { font-weight:600; }
.ln-decode   { background:#3c1e1e; padding:0 4px; }       /* 🔤 highlight, additive */
details.tb summary { cursor: pointer; }
details.tb[open] summary { margin-bottom: 2px; }
```

---

## JS (assets/app.js additions)

```js
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
```

Chip clicks call `setConsoleFilter(id, 'ERROR')` etc. The Copy chip calls `copyConsoleContent(id)`. No clear button — clearing is done by re-running.

---

## Out of scope

- DITA Conref and PDF Segmentation pages — they use `launch_desktop_tool`, not `run_subprocess`. No console.
- Home page — no console.
- Cross-page log search / unified log viewer.
- Streaming logs to a remote service.
- Log compression / archival beyond 50-file rotation.

---

## Open questions

None — all six design sections approved during brainstorming.

## Acceptance criteria

1. The Windows traceback at `ui_templates.py:515` is reproducible on a test injection of CP1252 bytes from a child, and the new console **does not crash** — it shows the line with the bad byte replaced and an in-UI warning chip "🔤 1".
2. A 5,000-line burst from a fake subprocess (`for i in range(5000): print(...)`) yields ≤ 60 times to Gradio (measured via instrumentation in the generator).
3. A run that produces 1 traceback in the first 200 lines retains that traceback in the disk log file even after 4,800 more lines scroll the buffer; the summary chip continues to show "✖ 1" for the entire run.
4. Stop button on a CPU-bound child terminates the child within 3 seconds on both Linux and Windows (verified manually).
5. All four migrated call sites work in the browser: real-time output, summary chip updates live, filter chips toggle correctly, download link produces a valid file.
6. After Phase 4 cleanup, the old `log_header`, `run_subprocess`, and `stop_subprocess` symbols are gone from `ui_templates.py` and unreferenced anywhere in `gradio_tools/`.
