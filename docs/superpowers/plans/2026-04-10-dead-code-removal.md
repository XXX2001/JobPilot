# Dead Code Removal (ruff + vulture) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all dead code from SE-Tools using ruff (F401/F841) and vulture (≥80% confidence), leaving the test suite green.

**Architecture:** Two-pass approach — ruff `--fix` handles the bulk auto-removable unused imports and exception bindings; manual edits handle the remaining unused variable assignments and vulture-only findings (unreachable code, unused function parameters, redundant conditional imports).

**Tech Stack:** Python 3.x, ruff (in `.venv`), vulture (in `.venv`), pytest (565-test baseline)

---

## Pre-flight: Baseline

- [ ] **Step 1: Record baseline test count**

```bash
cd /home/mouad/ALL/SE-Tools
.venv/bin/pytest src/PDF_PDM/tests/ src/PDM_Tools/tests/ -q --tb=no 2>&1 | tail -3
```

Expected: `565 passed` (or close). Save this number — every task ends with this check.

---

## Task 1: Bulk auto-fix with ruff

**Files:** All `.py` files outside `.venv/`  
**Covers:** All F401 [*] (unused imports), all [*] F841 (bare `except ... as e` → `except ...`)

- [ ] **Step 1: Run ruff auto-fix**

```bash
cd /home/mouad/ALL/SE-Tools
.venv/bin/ruff check --select F401,F841 --fix .
```

Expected: ruff prints "Fixed N files." with no errors. If any files are skipped with `[unfixable]`, those will be handled in later tasks.

- [ ] **Step 2: Verify no syntax breakage**

```bash
.venv/bin/python -m py_compile gradio_tools/dita.py \
  src/PDF_PDM/app.py \
  src/PDF_PDM/export/html_to_dita.py \
  src/Dicolabel_Trad/src/main.py \
  src/DSLGP_auto/csv_hierarchy_merger.py && echo "OK"
```

Expected: `OK`

- [ ] **Step 3: Run tests**

```bash
.venv/bin/pytest src/PDF_PDM/tests/ src/PDM_Tools/tests/ -q --tb=short 2>&1 | tail -5
```

Expected: same number of passes as baseline.

- [ ] **Step 4: Commit**

```bash
git add -u
git commit -m "$(cat <<'EOF'
chore: auto-remove unused imports and bare exception bindings (ruff --fix)

Removes all F401 [*] unused imports and [*] F841 exception bindings
across ~80 files using ruff auto-fix. No logic changes.
EOF
)"
```

---

## Task 2: Manual F841 — DSLGP_auto + DITA_Conref + gradio_tools

**Files:**
- Modify: `gradio_tools/dita.py:108`
- Modify: `src/DITA_Conref/core/xml_parser.py:115`
- Modify: `src/DSLGP_auto/csv_hierarchy_merger.py:48,72,88,102,235`
- Modify: `src/DSLGP_auto/dual_excel_comparator.py:197,198`

- [ ] **Step 1: Fix `gradio_tools/dita.py:108` — unused `concept_id` widget**

The `gr.Textbox(...)` still needs to be created (it renders in the UI) — just drop the variable name.

Current:
```python
concept_id = gr.Textbox(
    label="Concept ID",
    value=cfg.get("concept_id", ""),
    interactive=False,
)
```

Replace with:
```python
gr.Textbox(
    label="Concept ID",
    value=cfg.get("concept_id", ""),
    interactive=False,
)
```

- [ ] **Step 2: Fix `src/DITA_Conref/core/xml_parser.py:115` — unused `id_text`**

Current (line 115):
```python
id_text = self._extract_clean_text(ph_id)
```

Delete that entire line (the value is computed but never referenced — `ph_id` text is not needed here).

- [ ] **Step 3: Fix `src/DSLGP_auto/csv_hierarchy_merger.py` — 5 unused timing vars + result**

Line 48 — delete:
```python
optimize_performance = settings.get("optimize_performance", True)
```

Line 72 — delete:
```python
load_start = time.time()
```
(also remove the `print("Loading second CSV...")` line just above it if `load_start` was the only reason that block existed — but leave the print if it's standalone)

Line 88 — delete:
```python
lookup_start = time.time()
```

Line 102 — delete:
```python
merge_start = time.time()
```

Line 235 — change to drop the unused `result` variable (the function is called for its side effect):

Current:
```python
result = merge_and_clean_csvs_optimized(first_csv, second_csv, output_csv, config)
```

Replace with:
```python
merge_and_clean_csvs_optimized(first_csv, second_csv, output_csv, config)
```

- [ ] **Step 4: Fix `src/DSLGP_auto/dual_excel_comparator.py:197,198` — unused column vars**

Lines 197-198:
```python
param_description_col = settings["param_description_col"]
code_col = settings["code_col"]
```

Delete both lines. The settings dict values are read but never used further in the function.

- [ ] **Step 5: Verify ruff is clean on these files**

```bash
.venv/bin/ruff check --select F841 \
  gradio_tools/dita.py \
  src/DITA_Conref/core/xml_parser.py \
  src/DSLGP_auto/csv_hierarchy_merger.py \
  src/DSLGP_auto/dual_excel_comparator.py
```

Expected: no output (no issues).

- [ ] **Step 6: Commit**

```bash
git add gradio_tools/dita.py \
  src/DITA_Conref/core/xml_parser.py \
  src/DSLGP_auto/csv_hierarchy_merger.py \
  src/DSLGP_auto/dual_excel_comparator.py
git commit -m "$(cat <<'EOF'
chore: remove unused F841 variables in DSLGP_auto, DITA_Conref, gradio_tools
EOF
)"
```

---

## Task 3: Manual F841 — Dicolabel_Trad

**Files:**
- Modify: `src/Dicolabel_Trad/src/anchor_cache.py:272`
- Modify: `src/Dicolabel_Trad/src/main.py:852`
- Modify: `src/Dicolabel_Trad/src/utils_base.py:984,985`

- [ ] **Step 1: Fix `anchor_cache.py:272` — unused `candidate_pool = []`**

Line 272:
```python
candidate_pool = []
```

Delete this line. The variable is never populated or read; `selected_anchors` (line 270) handles the actual selection.

- [ ] **Step 2: Fix `src/Dicolabel_Trad/src/main.py:852` — unused `word_tracker`**

Line 852:
```python
word_tracker = get_word_tracker()
```

Delete this line. `get_word_tracker()` is called but the result is never used in the block that follows.

- [ ] **Step 3: Fix `src/Dicolabel_Trad/src/utils_base.py:984,985` — unused extraction results**

Lines 984-985:
```python
trans_tech_ids = extract_tech_identifiers(translation)
trans_symbols = extract_symbols(translation)
```

Delete both lines. Only `trans_numbers` (line 983) is used in the hallucination check below. `trans_tech_ids` and `trans_symbols` are computed but never referenced.

- [ ] **Step 4: Verify**

```bash
.venv/bin/ruff check --select F841 \
  src/Dicolabel_Trad/src/anchor_cache.py \
  src/Dicolabel_Trad/src/main.py \
  src/Dicolabel_Trad/src/utils_base.py
```

Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add src/Dicolabel_Trad/src/anchor_cache.py \
  src/Dicolabel_Trad/src/main.py \
  src/Dicolabel_Trad/src/utils_base.py
git commit -m "$(cat <<'EOF'
chore: remove unused F841 variables in Dicolabel_Trad
EOF
)"
```

---

## Task 4: Manual F841 — PDF_PDM/app.py + config.py

**Files:**
- Modify: `src/PDF_PDM/app.py:2354,4802`
- Modify: `src/PDF_PDM/config.py:293`

- [ ] **Step 1: Fix `app.py:2354` — unused `context` from `analyze_document`**

Line 2354 currently:
```python
context = self.engine.analyze_document(self.pdf_doc)
```

The return value (context) is never read. The call is needed for its side effect (populates `self.engine` internal state). Change to:
```python
self.engine.analyze_document(self.pdf_doc)
```

- [ ] **Step 2: Fix `app.py:4802` — unused `formatted_time`**

Lines 4801-4804 currently:
```python
timestamp = temp_data.get("timestamp", 0)
formatted_time = time.strftime(
    "%Y-%m-%d %H:%M:%S", time.localtime(timestamp)
)
```

Delete the `formatted_time = time.strftime(...)` assignment (3 lines, 4802-4804). Keep the `timestamp` line if it's used elsewhere nearby — check first. If `timestamp` is also unused after this deletion, delete it too.

To check: search for `timestamp` usage in the same function scope before deleting.

- [ ] **Step 3: Fix `config.py:293` — unused `version`**

Line 293:
```python
version = project_data.get("version", "1.0")
```

Delete this line. The version value is read from the project file but never used — `data` (line 294) is what's actually consumed.

- [ ] **Step 4: Verify**

```bash
.venv/bin/ruff check --select F841 src/PDF_PDM/app.py src/PDF_PDM/config.py
```

Expected: no F841 output.

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest src/PDF_PDM/tests/ -q --tb=short 2>&1 | tail -5
```

Expected: same pass count as baseline.

- [ ] **Step 6: Commit**

```bash
git add src/PDF_PDM/app.py src/PDF_PDM/config.py
git commit -m "$(cat <<'EOF'
chore: remove unused F841 variables in PDF_PDM app.py and config.py
EOF
)"
```

---

## Task 5: Manual F841 — PDF_PDM detection + export + tests

**Files:**
- Modify: `src/PDF_PDM/detection/tables.py:411`
- Modify: `src/PDF_PDM/export/html_to_dita.py:162,680,715,733,734`
- Modify: `src/PDF_PDM/tests/test_app_enrichment.py:20`
- Modify: `src/PDF_PDM/tests/test_dita_rules.py:79,90`
- Modify: `src/PDF_PDM/tests/test_dita_writer.py:255`

- [ ] **Step 1: Fix `detection/tables.py:411` — unused `base_y1`**

Line 411:
```python
base_y1 = base_bbox[3]
```

Delete this line. `base_bbox[3]` is never used — the continuation detection uses `cand_y0` directly.

- [ ] **Step 2: Fix `export/html_to_dita.py` — 5 unused SubElement variables**

These are `ET.SubElement(...)` calls where the element is added to the XML tree as a side effect but the returned variable is never used further. Change each to drop the variable name:

Line 162:
```python
# Before
conbody = ET.SubElement(topic_root, "conbody")
# After
ET.SubElement(topic_root, "conbody")
```

Line 680:
```python
# Before
shortdesc = ET.SubElement(bookmeta, "shortdesc")
# After
ET.SubElement(bookmeta, "shortdesc")
```

Line 715:
```python
# Before
toc = ET.SubElement(booklists_front, "toc")
# After
ET.SubElement(booklists_front, "toc")
```

Lines 733-734:
```python
# Before
glossarylist = ET.SubElement(booklists_back, "glossarylist")
indexlist = ET.SubElement(booklists_back, "indexlist")
# After
ET.SubElement(booklists_back, "glossarylist")
ET.SubElement(booklists_back, "indexlist")
```

- [ ] **Step 3: Fix test files — unused `tp` and `warnings` variables**

`tests/test_app_enrichment.py:20` — `tp` is the float return value of `page.insert_textbox(...)`. The text insertion is the side effect; drop the variable:
```python
# Before
tp = page.insert_textbox(
    fitz.Rect(50, 50, 500, 200),
    text,
    fontsize=font_size,
    fontname=font_name,
# After
page.insert_textbox(
    fitz.Rect(50, 50, 500, 200),
    text,
    fontsize=font_size,
    fontname=font_name,
```

`tests/test_dita_rules.py:79` — `warnings` is returned by `apply_all_rules` but only the side effect on `n.note_type` matters:
```python
# Before
warnings = apply_all_rules(doc)
# After
apply_all_rules(doc)
```

`tests/test_dita_rules.py:90` — same pattern, same fix.

`tests/test_dita_writer.py:255` — same pattern, same fix.

- [ ] **Step 4: Verify**

```bash
.venv/bin/ruff check --select F841 \
  src/PDF_PDM/detection/tables.py \
  src/PDF_PDM/export/html_to_dita.py \
  src/PDF_PDM/tests/test_app_enrichment.py \
  src/PDF_PDM/tests/test_dita_rules.py \
  src/PDF_PDM/tests/test_dita_writer.py
```

Expected: no output.

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest src/PDF_PDM/tests/ -q --tb=short 2>&1 | tail -5
```

Expected: same pass count as baseline.

- [ ] **Step 6: Commit**

```bash
git add src/PDF_PDM/detection/tables.py \
  src/PDF_PDM/export/html_to_dita.py \
  src/PDF_PDM/tests/test_app_enrichment.py \
  src/PDF_PDM/tests/test_dita_rules.py \
  src/PDF_PDM/tests/test_dita_writer.py
git commit -m "$(cat <<'EOF'
chore: remove unused F841 variables in PDF_PDM detection, export, and tests
EOF
)"
```

---

## Task 6: Vulture fixes — unreachable code + tooltip params + redundant import

**Files:**
- Modify: `src/Dicolabel_Trad/src/utils_base.py:599-601`
- Modify: `src/Dicolabel_Trad/launch_trad.py:973,990,1594-1598`
- Modify: `src/PDM_Tools/launch_pdm.py:152,169`

- [ ] **Step 1: Fix `utils_base.py:599-601` — unreachable code after `return`**

Lines 596-601 currently:
```python
        results.sort(key=lambda x: x[0])
        return [r[1] for r in results]

        # Sort by original index and return
        results.sort(key=lambda x: x[0])
        return [r[1] for r in results]
```

Delete lines 599-601 (the duplicated unreachable block after the first `return`):
```python
        results.sort(key=lambda x: x[0])
        return [r[1] for r in results]
```

- [ ] **Step 2: Fix `launch_trad.py:973` — unused `tooltip` parameter**

Rename to `_tooltip` to signal it's intentionally unused (callers still pass the value positionally):

```python
# Before
def create_setting_row(self, parent, row, label_text, var, tooltip=""):
# After
def create_setting_row(self, parent, row, label_text, var, _tooltip=""):
```

- [ ] **Step 3: Fix `launch_trad.py:990` — same for `create_file_setting_row`**

```python
# Before
def create_file_setting_row(self, parent, row, label_text, var, tooltip=""):
# After
def create_file_setting_row(self, parent, row, label_text, var, _tooltip=""):
```

- [ ] **Step 4: Fix `launch_trad.py:1594-1598` — redundant `customtkinter` import inside `main()`**

The module-level import (around line 63) already sets `CTK_AVAILABLE`. The second import inside `main()` at line 1594-1598 is entirely redundant:

```python
# Before
try:
    # Optional: Check for CustomTkinter
    try:
        import customtkinter as ctk
        # We don't set global here, individual checks handle it or we could set it
    except ImportError:
        pass
# After
# (delete the inner try/except block entirely, 4 lines: 1594-1598)
```

- [ ] **Step 5: Fix `launch_pdm.py:152` — unused `tooltip` parameter in `create_file_row`**

```python
# Before
def create_file_row(self, parent, row, label_text, var, command, tooltip):
# After
def create_file_row(self, parent, row, label_text, var, command, _tooltip):
```

- [ ] **Step 6: Fix `launch_pdm.py:169` — same for `create_folder_row`**

```python
# Before
def create_folder_row(self, parent, row, label_text, var, command, tooltip):
# After
def create_folder_row(self, parent, row, label_text, var, command, _tooltip):
```

- [ ] **Step 7: Verify syntax**

```bash
.venv/bin/python -m py_compile \
  src/Dicolabel_Trad/src/utils_base.py \
  src/Dicolabel_Trad/launch_trad.py \
  src/PDM_Tools/launch_pdm.py && echo "OK"
```

Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add src/Dicolabel_Trad/src/utils_base.py \
  src/Dicolabel_Trad/launch_trad.py \
  src/PDM_Tools/launch_pdm.py
git commit -m "$(cat <<'EOF'
chore: fix vulture findings — remove unreachable code, mark unused tooltip params, drop redundant import
EOF
)"
```

---

## Task 7: Final verification

- [ ] **Step 1: Run full ruff check — expect zero remaining F401/F841**

```bash
.venv/bin/ruff check --select F401,F841 . 2>&1 | grep -v "/.venv/" | grep -v "/__pycache__/"
```

Expected: no output (all issues resolved). If any remain, fix them before proceeding.

- [ ] **Step 2: Run vulture — confirm only low-confidence false positives remain**

```bash
.venv/bin/vulture gradio_tools/ src/DITA_Conref/ src/Dicolabel_Trad/ src/DSLGP_auto/ --min-confidence 80 2>&1 | grep -v "/__pycache__/"
.venv/bin/vulture src/PDF_PDM/ src/PDM_Tools/ --min-confidence 80 2>&1 | grep -v "/__pycache__/" | grep -v "Traceback"
```

Expected: only items that are genuinely false positives (e.g., public API methods used dynamically). If real dead code remains, fix it.

- [ ] **Step 3: Run full test suite**

```bash
.venv/bin/pytest src/PDF_PDM/tests/ src/PDM_Tools/tests/ -q --tb=short 2>&1 | tail -5
```

Expected: same pass count as baseline (565 passed).

- [ ] **Step 4: Final commit (if any cleanup remained)**

```bash
git add -u
git commit -m "$(cat <<'EOF'
chore: complete dead code removal pass — ruff F401/F841 + vulture clean
EOF
)"
```

---

## Notes for the implementer

- **`ET.SubElement(...)` calls**: Dropping the variable name is safe — the element is added to the tree as a side effect of `SubElement()` itself. The returned reference is only needed if you intend to add children or set attributes later.
- **`analyze_document()` call in `app.py:2354`**: Keep the call, drop only the variable binding. The engine's internal state is populated by this call; the returned `context` value was never consumed.
- **`tooltip` parameters**: Renamed to `_tooltip` rather than deleted because callers pass the value positionally (changing callers would cascade). The `_` prefix is the Python convention for intentionally unused parameters.
- **`utils_base.py` unreachable code**: The duplicate 3-line block starting at line 599 is an exact copy of the two lines above it (already after a `return`). Delete lines 599-601 only.
- **Ruff `--fix` scope**: Only run from the SE-Tools root (`/home/mouad/ALL/SE-Tools`). The `.venv/` directory is excluded automatically by ruff's default exclusion list.
