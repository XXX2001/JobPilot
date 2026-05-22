# Table Editor Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `PDF_PDM/tables/editor.py` (2,746 lines, widget-per-cell) with a new Canvas-based DITA-aware editor embedded in the Table Editor tab, with first-class header row designation.

**Architecture:** Single `tk.Canvas` draws the full grid. Floating `tk.Entry` for cell editing. Embedded in the existing "Table Editor" tab (no `Toplevel`). `EditorGrid` dataclass owns grid state; `Selection` tracks current selection; `DitaTableAttrs` tracks table-level DITA attributes. Undo/redo via existing `TableEditorHistory`. Existing `Table`/`TableCell` persistence model extended with new fields (`is_header`, `rotate`, `align`, `bold`, `italic`, `dita_attrs`, `col_widths`).

**Tech Stack:** Python 3.x, tkinter (stdlib), ttk (stdlib), existing `pdf_models.Table/TableCell`, existing `TableEditorHistory` class.

**Spec:** `docs/superpowers/specs/2026-04-05-table-editor-rebuild-design.md`

---

## File Plan

**Create:**
- `src/PDF_PDM/tables/editor_v2.py` — new editor (~900 lines target)
- `src/PDF_PDM/tables/editor_grid.py` — data model + pure grid operations (~250 lines)
- `src/PDF_PDM/tables/validation.py` — DITA validation rules (~120 lines)
- `src/PDF_PDM/tests/test_editor_grid.py` — unit tests for grid ops
- `src/PDF_PDM/tests/test_validation.py` — unit tests for validation rules
- `src/PDF_PDM/tests/test_dita_table_export.py` — integration tests for DITA output

**Modify:**
- `src/PDF_PDM/pdf_models.py` — add fields to `Table` and `TableCell`
- `src/PDF_PDM/app.py` — swap Table Editor tab to use embedded editor_v2
- `src/PDF_PDM/export/dita.py` — emit `<title>`, `<thead>`, `<colspec>` with `colnum`/`colwidth`, `<p>` wrappers, `rotate`, per-cell formatting

**Delete (final task):**
- `src/PDF_PDM/tables/editor.py` — old editor

---

### Task 1: Extend data model (Table, TableCell)

**Files:**
- Modify: `src/PDF_PDM/pdf_models.py`
- Test: `src/PDF_PDM/tests/test_pdf_models_table.py` (create)

- [ ] **Step 1: Write failing tests for new fields**

Create `src/PDF_PDM/tests/test_pdf_models_table.py`:

```python
import pytest
from pdf_models import Table, TableCell, create_table_cell

def test_tablecell_has_new_fields():
    c = create_table_cell(row=0, col=0, text="hi")
    assert c.is_header is False
    assert c.rotate == 0
    assert c.align == "left"
    assert c.bold is False
    assert c.italic is False

def test_tablecell_roundtrip_preserves_new_fields():
    c = create_table_cell(row=0, col=0, text="H1")
    c.is_header = True; c.rotate = 1; c.align = "center"
    c.bold = True; c.italic = True
    d = c.to_dict()
    c2 = TableCell.from_dict(d)
    assert c2.is_header and c2.rotate == 1 and c2.align == "center"
    assert c2.bold and c2.italic

def test_table_has_dita_attrs_and_col_widths():
    from pdf_models import ElementType, create_element
    el = create_element(ElementType.TABLE, (0,0,100,100), 0)
    t = Table(element=el)
    assert t.dita_attrs.title == ""
    assert t.dita_attrs.pgwide is True
    assert t.dita_attrs.font_class == "font:10"
    assert t.dita_attrs.orient == "port"
    assert t.dita_attrs.frame == "all"
    assert t.col_widths == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/PDF_PDM && python -m pytest tests/test_pdf_models_table.py -v`
Expected: FAIL — fields don't exist yet.

- [ ] **Step 3: Add `DitaTableAttrs` dataclass + new fields**

In `src/PDF_PDM/pdf_models.py`, locate the `TableCell` dataclass and add fields:

```python
@dataclass
class TableCell:
    # ... existing fields ...
    confidence_category: str = "high"
    # NEW fields for editor_v2:
    is_header: bool = False
    rotate: int = 0                 # 0, 1, 2, 3 (0°, 90°, 180°, 270°)
    align: str = "left"             # "left" | "center" | "right"
    bold: bool = False
    italic: bool = False
```

Update `TableCell.to_dict()` / `from_dict()` to include the new fields.

Add `DitaTableAttrs` dataclass near the `Table` class:

```python
@dataclass
class DitaTableAttrs:
    title: str = ""
    pgwide: bool = True
    font_class: str = "font:10"
    orient: str = "port"
    frame: str = "all"
    colsep: bool = True
    rowsep: bool = True

    def to_dict(self) -> dict:
        return {"title": self.title, "pgwide": self.pgwide,
                "font_class": self.font_class, "orient": self.orient,
                "frame": self.frame, "colsep": self.colsep, "rowsep": self.rowsep}

    @staticmethod
    def from_dict(d: dict) -> "DitaTableAttrs":
        return DitaTableAttrs(
            title=d.get("title", ""), pgwide=d.get("pgwide", True),
            font_class=d.get("font_class", "font:10"), orient=d.get("orient", "port"),
            frame=d.get("frame", "all"),
            colsep=d.get("colsep", True), rowsep=d.get("rowsep", True),
        )
```

Extend `Table` dataclass:

```python
@dataclass
class Table:
    # ... existing fields ...
    dita_attrs: DitaTableAttrs = field(default_factory=DitaTableAttrs)
    col_widths: List[str] = field(default_factory=list)  # e.g. ["1*", "2*", "1*"]
```

Update `Table.to_dict()` / `from_dict()` to include `dita_attrs` and `col_widths`.

- [ ] **Step 4: Run tests — they should pass**

Run: `cd src/PDF_PDM && python -m pytest tests/test_pdf_models_table.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/PDF_PDM/pdf_models.py src/PDF_PDM/tests/test_pdf_models_table.py
git commit -m "feat(pdf_pdm): extend Table/TableCell with DITA editor fields"
```

---

### Task 2: EditorGrid data model + grid operations

**Files:**
- Create: `src/PDF_PDM/tables/editor_grid.py`
- Test: `src/PDF_PDM/tests/test_editor_grid.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_editor_grid.py
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tables.editor_grid import EditorGrid, CellData

def test_new_grid_default_size():
    g = EditorGrid.new(rows=3, cols=4)
    assert g.rows == 3 and g.cols == 4
    assert len(g.cells) == 3 and len(g.cells[0]) == 4
    assert all(isinstance(c, CellData) for r in g.cells for c in r)
    assert g.header_rows == set()
    assert g.col_widths == ["1*", "1*", "1*", "1*"]

def test_insert_row_above():
    g = EditorGrid.new(rows=3, cols=2)
    g.cells[0][0].text = "A"; g.cells[1][0].text = "B"; g.cells[2][0].text = "C"
    g.insert_row(at=1, above=True)
    assert g.rows == 4
    assert g.cells[0][0].text == "A"
    assert g.cells[1][0].text == ""       # new empty row
    assert g.cells[2][0].text == "B"
    assert g.cells[3][0].text == "C"

def test_insert_row_below():
    g = EditorGrid.new(rows=2, cols=2)
    g.cells[0][0].text = "A"; g.cells[1][0].text = "B"
    g.insert_row(at=0, above=False)       # insert below row 0
    assert g.rows == 3
    assert g.cells[0][0].text == "A"
    assert g.cells[1][0].text == ""
    assert g.cells[2][0].text == "B"

def test_insert_row_shifts_header_rows():
    g = EditorGrid.new(rows=3, cols=2)
    g.header_rows = {0}
    g.insert_row(at=0, above=True)
    assert g.header_rows == {1}           # index shifted

def test_delete_row_shifts_headers():
    g = EditorGrid.new(rows=4, cols=2)
    g.header_rows = {0, 1}
    g.delete_row(2)
    assert g.header_rows == {0, 1}        # unaffected
    g.delete_row(0)
    assert g.header_rows == {0}           # {1} → {0}

def test_mark_header_toggle():
    g = EditorGrid.new(rows=3, cols=2)
    g.mark_header({0, 1})
    assert g.header_rows == {0, 1}
    g.clear_header({0})
    assert g.header_rows == {1}

def test_insert_col_updates_widths():
    g = EditorGrid.new(rows=2, cols=2)
    g.insert_col(at=0, left=True)
    assert g.cols == 3
    assert len(g.col_widths) == 3
    assert all(len(row) == 3 for row in g.cells)

def test_merge_range():
    g = EditorGrid.new(rows=3, cols=3)
    g.cells[0][0].text = "anchor"
    g.merge(anchor=(0, 0), rowspan=2, colspan=2)
    assert (0, 0) in g.merges
    assert g.merges[(0, 0)] == (2, 2)

def test_unmerge():
    g = EditorGrid.new(rows=3, cols=3)
    g.merge(anchor=(0, 0), rowspan=2, colspan=2)
    g.unmerge((0, 0))
    assert (0, 0) not in g.merges
```

Run: `cd src/PDF_PDM && python -m pytest tests/test_editor_grid.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 2: Create editor_grid.py**

```python
# tables/editor_grid.py
"""Pure-Python grid operations for the Canvas table editor.
No tkinter imports — fully unit-testable.
"""
from dataclasses import dataclass, field
from typing import List, Set, Tuple, Dict


@dataclass
class CellData:
    text: str = ""
    align: str = "left"                  # "left" | "center" | "right"
    bold: bool = False
    italic: bool = False
    rotate: int = 0                      # 0, 1, 2, 3
    confidence_category: str = "high"


@dataclass
class EditorGrid:
    rows: int
    cols: int
    cells: List[List[CellData]]
    header_rows: Set[int] = field(default_factory=set)
    col_widths: List[str] = field(default_factory=list)
    merges: Dict[Tuple[int, int], Tuple[int, int]] = field(default_factory=dict)
    # merges: {(anchor_row, anchor_col): (rowspan, colspan)}

    @classmethod
    def new(cls, rows: int, cols: int) -> "EditorGrid":
        return cls(
            rows=rows, cols=cols,
            cells=[[CellData() for _ in range(cols)] for _ in range(rows)],
            col_widths=["1*"] * cols,
        )

    def insert_row(self, at: int, above: bool) -> None:
        idx = at if above else at + 1
        self.cells.insert(idx, [CellData() for _ in range(self.cols)])
        self.rows += 1
        self.header_rows = {r if r < idx else r + 1 for r in self.header_rows}
        # Adjust merges anchored at or after idx, and spans crossing idx
        new_merges: Dict[Tuple[int, int], Tuple[int, int]] = {}
        for (ar, ac), (rs, cs) in self.merges.items():
            if ar >= idx:
                new_merges[(ar + 1, ac)] = (rs, cs)
            elif ar + rs > idx:
                new_merges[(ar, ac)] = (rs + 1, cs)   # span grows
            else:
                new_merges[(ar, ac)] = (rs, cs)
        self.merges = new_merges

    def delete_row(self, at: int) -> None:
        if self.rows <= 1:
            return
        del self.cells[at]
        self.rows -= 1
        self.header_rows = {r - 1 if r > at else r for r in self.header_rows if r != at}
        # Drop/adjust merges
        new_merges: Dict[Tuple[int, int], Tuple[int, int]] = {}
        for (ar, ac), (rs, cs) in self.merges.items():
            if ar == at:
                continue  # anchor deleted
            if ar > at:
                ar -= 1
            elif ar + rs > at:
                rs -= 1
                if rs < 1: continue
            new_merges[(ar, ac)] = (rs, cs)
        self.merges = new_merges

    def insert_col(self, at: int, left: bool) -> None:
        idx = at if left else at + 1
        for row in self.cells:
            row.insert(idx, CellData())
        self.cols += 1
        self.col_widths.insert(idx, "1*")
        new_merges: Dict[Tuple[int, int], Tuple[int, int]] = {}
        for (ar, ac), (rs, cs) in self.merges.items():
            if ac >= idx:
                new_merges[(ar, ac + 1)] = (rs, cs)
            elif ac + cs > idx:
                new_merges[(ar, ac)] = (rs, cs + 1)
            else:
                new_merges[(ar, ac)] = (rs, cs)
        self.merges = new_merges

    def delete_col(self, at: int) -> None:
        if self.cols <= 1:
            return
        for row in self.cells:
            del row[at]
        self.cols -= 1
        del self.col_widths[at]
        new_merges: Dict[Tuple[int, int], Tuple[int, int]] = {}
        for (ar, ac), (rs, cs) in self.merges.items():
            if ac == at:
                continue
            if ac > at:
                ac -= 1
            elif ac + cs > at:
                cs -= 1
                if cs < 1: continue
            new_merges[(ar, ac)] = (rs, cs)
        self.merges = new_merges

    def mark_header(self, rows: Set[int]) -> None:
        self.header_rows |= rows

    def clear_header(self, rows: Set[int]) -> None:
        self.header_rows -= rows

    def merge(self, anchor: Tuple[int, int], rowspan: int, colspan: int) -> None:
        assert rowspan >= 1 and colspan >= 1
        self.merges[anchor] = (rowspan, colspan)

    def unmerge(self, anchor: Tuple[int, int]) -> None:
        self.merges.pop(anchor, None)
```

- [ ] **Step 3: Run tests — all should pass**

Run: `cd src/PDF_PDM && python -m pytest tests/test_editor_grid.py -v`
Expected: PASS (all 10 tests).

- [ ] **Step 4: Commit**

```bash
git add src/PDF_PDM/tables/editor_grid.py src/PDF_PDM/tests/test_editor_grid.py
git commit -m "feat(pdf_pdm): add EditorGrid with pure-Python grid operations"
```

---

### Task 3: Validation rules module

**Files:**
- Create: `src/PDF_PDM/tables/validation.py`
- Test: `src/PDF_PDM/tests/test_validation.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_validation.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tables.editor_grid import EditorGrid
from tables.validation import validate_grid, ValidationIssue, Severity

def test_valid_grid_no_issues():
    g = EditorGrid.new(rows=3, cols=3)
    g.header_rows = {0}
    for c in range(3): g.cells[0][c].text = "H"
    for r in range(1, 3):
        for c in range(3): g.cells[r][c].text = "x"
    assert validate_grid(g) == []

def test_empty_header_cell_warning():
    g = EditorGrid.new(rows=2, cols=3)
    g.header_rows = {0}
    g.cells[0][0].text = "A"; g.cells[0][2].text = "C"  # col 1 empty
    issues = validate_grid(g)
    assert any("empty" in i.message.lower() and i.severity == Severity.WARNING for i in issues)

def test_no_proportional_width_error():
    g = EditorGrid.new(rows=2, cols=2)
    g.col_widths = ["60", "40"]  # no *, all fixed
    issues = validate_grid(g)
    assert any(i.severity == Severity.ERROR and "proportional" in i.message.lower() for i in issues)

def test_rotated_cell_multi_paragraph_error():
    g = EditorGrid.new(rows=2, cols=2)
    g.cells[0][0].text = "line1\n\nline2"  # blank line = multi-paragraph
    g.cells[0][0].rotate = 1
    issues = validate_grid(g)
    assert any(i.severity == Severity.ERROR and "rotated" in i.message.lower() for i in issues)

def test_empty_grid_error():
    g = EditorGrid(rows=0, cols=0, cells=[])
    issues = validate_grid(g)
    assert any(i.severity == Severity.ERROR for i in issues)

def test_row_1_looks_like_header_but_not_marked():
    g = EditorGrid.new(rows=3, cols=2)
    g.cells[0][0].text = "Name"; g.cells[0][0].bold = True
    g.cells[0][1].text = "Value"; g.cells[0][1].bold = True
    g.cells[1][0].text = "x"; g.cells[2][0].text = "y"
    # header_rows empty
    issues = validate_grid(g)
    assert any("header-like" in i.message.lower() and i.severity == Severity.WARNING for i in issues)
```

Run: `cd src/PDF_PDM && python -m pytest tests/test_validation.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 2: Create validation.py**

```python
# tables/validation.py
"""DITA export validation rules for EditorGrid state."""
from dataclasses import dataclass
from enum import Enum
from typing import List
from tables.editor_grid import EditorGrid


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class ValidationIssue:
    severity: Severity
    message: str
    row: int = -1
    col: int = -1


def validate_grid(g: EditorGrid) -> List[ValidationIssue]:
    out: List[ValidationIssue] = []

    # Empty grid
    if g.rows == 0 or g.cols == 0:
        out.append(ValidationIssue(Severity.ERROR, "Empty table — add at least one row and column"))
        return out

    # Proportional width required
    if g.col_widths and not any(w.endswith("*") for w in g.col_widths):
        out.append(ValidationIssue(
            Severity.ERROR,
            "At least one column must have proportional width (1*, 2*, etc.)",
        ))

    # Empty header cells
    for r in g.header_rows:
        for c in range(g.cols):
            if r < g.rows and not g.cells[r][c].text.strip():
                out.append(ValidationIssue(
                    Severity.WARNING,
                    f"Header cell is empty at row {r+1}, column {c+1}",
                    row=r, col=c,
                ))

    # Rotated cells with multi-paragraph text
    for r in range(g.rows):
        for c in range(g.cols):
            cell = g.cells[r][c]
            if cell.rotate != 0 and "\n\n" in cell.text:
                out.append(ValidationIssue(
                    Severity.ERROR,
                    f"Rotated cell at row {r+1}, col {c+1} cannot have multi-paragraph text",
                    row=r, col=c,
                ))

    # Row 1 looks like header but isn't marked
    if 0 not in g.header_rows and g.rows >= 2:
        row0 = g.cells[0]
        if all(cell.bold and cell.text.strip() for cell in row0):
            body_bold = sum(1 for r in range(1, g.rows) for cell in g.cells[r] if cell.bold)
            if body_bold == 0:
                out.append(ValidationIssue(
                    Severity.WARNING,
                    "Row 1 looks header-like (all bold, filled). Mark it as header?",
                    row=0,
                ))

    return out
```

- [ ] **Step 3: Run tests — all should pass**

Run: `cd src/PDF_PDM && python -m pytest tests/test_validation.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/PDF_PDM/tables/validation.py src/PDF_PDM/tests/test_validation.py
git commit -m "feat(pdf_pdm): add DITA validation rules for editor grid"
```

---

### Task 4: Editor skeleton — module, constants, embedded frame

**Files:**
- Create: `src/PDF_PDM/tables/editor_v2.py`

- [ ] **Step 1: Create editor_v2.py skeleton**

```python
# tables/editor_v2.py
"""Canvas-based DITA-aware table editor. Replaces tables/editor.py."""
import tkinter as tk
from tkinter import ttk
from dataclasses import dataclass, field
from typing import Optional, Tuple, Set, List, Callable
import logging

from tables.editor_grid import EditorGrid, CellData
from tables.validation import validate_grid, ValidationIssue, Severity

logger = logging.getLogger(__name__)


# ============================================================================
# Visual constants (SE-Tools brand, tkinter-realistic)
# ============================================================================
CANVAS_BG = "#ffffff"
GRID_LINE = "#e1e3e8"
GRID_LINE_HEAVY = "#d5d8dd"
COL_HEADER_BG = "#f5f6f8"
COL_HEADER_FG = "#888888"
ROW_NUM_BG = "#f5f6f8"
ROW_NUM_FG = "#888888"
HEADER_ROW_BG = "#D6EAF8"
HEADER_ROW_FG = "#1565C0"
HEADER_NUM_BG = "#3DCD58"
HEADER_NUM_FG = "#ffffff"
SELECTED_BG = "#e8fae9"
SELECTED_BORDER = "#3DCD58"
SELECTED_ROW_NUM_BG = "#e8fae9"
SELECTED_ROW_NUM_FG = "#2db348"
MERGED_BG = "#f0f8ff"
LOW_CONF_BORDER = "#F5A623"
DIVIDER_COLOR = "#3DCD58"
DITA_PILL_BG = "#3DCD58"
DITA_STRIP_BG = "#f8f9fb"
TOOLBAR_BG = "#ffffff"
TITLEBAR_BG = "#ffffff"
STATUSBAR_BG = "#fafbfc"
BORDER_COLOR = "#d5d8dd"

ROW_NUM_WIDTH = 36
COL_HEADER_HEIGHT = 26      # taller to fit colspec label
CELL_HEIGHT = 28
CELL_MIN_WIDTH = 80
DIVIDER_HEIGHT = 3

FONT_UI = ("Segoe UI", 10)
FONT_UI_SMALL = ("Segoe UI", 9)
FONT_UI_TINY = ("Segoe UI", 8)
FONT_UI_BOLD = ("Segoe UI", 10, "bold")
FONT_CELL = ("Segoe UI", 10)


@dataclass
class Selection:
    kind: str = "none"                           # "none"|"cell"|"row"|"col"|"range"|"multi"
    anchor: Tuple[int, int] = (0, 0)
    cells: Set[Tuple[int, int]] = field(default_factory=set)

    def is_empty(self) -> bool:
        return self.kind == "none" or not self.cells

    def contains_row(self, r: int) -> bool:
        return any(rr == r for (rr, _) in self.cells)

    def rows(self) -> Set[int]:
        return {r for (r, _) in self.cells}

    def cols(self) -> Set[int]:
        return {c for (_, c) in self.cells}


class CanvasTableEditor:
    """Canvas-based embedded table editor.

    Usage:
        editor = CanvasTableEditor(parent_frame, on_apply=callback)
        editor.load_table(table_obj)
        # ... user edits ...
        # callback(updated_table_obj) fires on Apply
    """

    def __init__(self, parent: tk.Widget,
                 on_apply: Optional[Callable] = None,
                 on_cancel: Optional[Callable] = None,
                 temp_system=None):
        self.parent = parent
        self.on_apply = on_apply
        self.on_cancel = on_cancel
        self.temp_system = temp_system
        self.grid: EditorGrid = EditorGrid.new(rows=2, cols=2)
        self.selection = Selection()
        self.col_widths_px: List[int] = []        # pixel widths computed from ratios
        self._edit_entry: Optional[tk.Entry] = None
        self._edit_cell: Optional[Tuple[int, int]] = None
        self._current_table = None                # original Table object for round-trip

        self.frame = ttk.Frame(parent)
        self.frame.pack(fill=tk.BOTH, expand=True)

        self._build_ui()

    def _build_ui(self) -> None:
        """Assemble: titlebar, DITA strip, toolbar, canvas, statusbar."""
        # Placeholder — filled in subsequent tasks
        placeholder = ttk.Label(self.frame, text="CanvasTableEditor (scaffolding)")
        placeholder.pack(padx=20, pady=20)

    def load_table(self, table) -> None:
        """Load a Table object into the grid."""
        # Placeholder — filled in Task 20
        pass

    def to_table(self):
        """Serialize current grid back to a Table object."""
        # Placeholder — filled in Task 20
        pass
```

- [ ] **Step 2: Smoke-test import**

Run: `cd src/PDF_PDM && python -c "from tables.editor_v2 import CanvasTableEditor; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/PDF_PDM/tables/editor_v2.py
git commit -m "feat(pdf_pdm): editor_v2 scaffolding with Selection + constants"
```

---

### Task 5: Title bar + DITA strip widget

**Files:** Modify `src/PDF_PDM/tables/editor_v2.py`

- [ ] **Step 1: Implement `_build_titlebar` and `_build_dita_strip`**

Replace the `_build_ui` placeholder with real title bar and DITA strip:

```python
def _build_ui(self) -> None:
    # Title bar
    self.titlebar = tk.Frame(self.frame, bg=TITLEBAR_BG, height=52,
                             highlightbackground=BORDER_COLOR, highlightthickness=0)
    self.titlebar.pack(side=tk.TOP, fill=tk.X)
    self.titlebar.pack_propagate(False)
    self._build_titlebar(self.titlebar)

    # DITA strip
    self.dita_bar = tk.Frame(self.frame, bg=DITA_STRIP_BG, height=36,
                             highlightbackground=BORDER_COLOR, highlightthickness=0)
    self.dita_bar.pack(side=tk.TOP, fill=tk.X)
    self.dita_bar.pack_propagate(False)
    self._build_dita_strip(self.dita_bar)

    # Separator lines
    tk.Frame(self.frame, height=1, bg=BORDER_COLOR).pack(side=tk.TOP, fill=tk.X)

def _build_titlebar(self, parent: tk.Frame) -> None:
    left = tk.Frame(parent, bg=TITLEBAR_BG)
    left.pack(side=tk.LEFT, padx=14, pady=6)
    self.title_label = tk.Label(left, text="Table Editor",
                                font=("Segoe UI", 12, "bold"), bg=TITLEBAR_BG, fg="#222")
    self.title_label.pack(anchor="w")
    self.meta_label = tk.Label(left, text="",
                               font=FONT_UI_SMALL, bg=TITLEBAR_BG, fg="#666")
    self.meta_label.pack(anchor="w")

    right = tk.Frame(parent, bg=TITLEBAR_BG)
    right.pack(side=tk.RIGHT, padx=14, pady=10)
    self.help_btn = ttk.Button(right, text="? Shortcuts", command=self._show_shortcuts,
                               style="TB.TButton")
    self.help_btn.pack()

def _build_dita_strip(self, parent: tk.Frame) -> None:
    # Pill label
    pill = tk.Label(parent, text="DITA", bg=DITA_PILL_BG, fg="white",
                    font=("Segoe UI", 8, "bold"), padx=8, pady=2)
    pill.pack(side=tk.LEFT, padx=(14, 14), pady=8)

    # Title input
    tk.Label(parent, text="Title", bg=DITA_STRIP_BG, font=FONT_UI_SMALL, fg="#444").pack(side=tk.LEFT)
    self.title_var = tk.StringVar()
    title_entry = ttk.Entry(parent, textvariable=self.title_var, width=28,
                            font=FONT_UI_SMALL)
    title_entry.pack(side=tk.LEFT, padx=(5, 14))
    self.title_var.trace_add("write", lambda *a: self._on_title_change())

    # Full width checkbox
    self.pgwide_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(parent, text="Full width", variable=self.pgwide_var,
                    command=self._on_pgwide_change).pack(side=tk.LEFT, padx=(0, 14))

    # Font dropdown
    tk.Label(parent, text="Font", bg=DITA_STRIP_BG, font=FONT_UI_SMALL, fg="#444").pack(side=tk.LEFT)
    self.font_var = tk.StringVar(value="10pt")
    font_combo = ttk.Combobox(parent, textvariable=self.font_var, width=5, state="readonly",
                              values=["8pt", "10pt", "12pt"], font=FONT_UI_SMALL)
    font_combo.pack(side=tk.LEFT, padx=(5, 14))
    font_combo.bind("<<ComboboxSelected>>", lambda e: self._on_font_change())

    # Orient dropdown
    tk.Label(parent, text="Orient", bg=DITA_STRIP_BG, font=FONT_UI_SMALL, fg="#444").pack(side=tk.LEFT)
    self.orient_var = tk.StringVar(value="Portrait")
    orient_combo = ttk.Combobox(parent, textvariable=self.orient_var, width=10,
                                state="readonly", values=["Portrait", "Landscape"],
                                font=FONT_UI_SMALL)
    orient_combo.pack(side=tk.LEFT, padx=(5, 14))
    orient_combo.bind("<<ComboboxSelected>>", lambda e: self._on_orient_change())

    # Frame (borders) dropdown
    tk.Label(parent, text="Borders", bg=DITA_STRIP_BG, font=FONT_UI_SMALL, fg="#444").pack(side=tk.LEFT)
    self.frame_var = tk.StringVar(value="All")
    frame_combo = ttk.Combobox(parent, textvariable=self.frame_var, width=10,
                               state="readonly", values=["All", "Top only", "Sides", "None"],
                               font=FONT_UI_SMALL)
    frame_combo.pack(side=tk.LEFT, padx=(5, 14))
    frame_combo.bind("<<ComboboxSelected>>", lambda e: self._on_frame_change())

def _show_shortcuts(self) -> None:
    # Placeholder — filled in Task 18
    pass

def _on_title_change(self) -> None: pass
def _on_pgwide_change(self) -> None: pass
def _on_font_change(self) -> None: pass
def _on_orient_change(self) -> None: pass
def _on_frame_change(self) -> None: pass
```

- [ ] **Step 2: Visual verification**

Create temporary test script `src/PDF_PDM/tmp_preview.py`:

```python
import tkinter as tk
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tables.editor_v2 import CanvasTableEditor
root = tk.Tk()
root.geometry("1100x700")
root.title("editor_v2 preview")
editor = CanvasTableEditor(root)
root.mainloop()
```

Run: `cd src/PDF_PDM && python tmp_preview.py`
Expected: Window opens with title bar showing "Table Editor", "? Shortcuts" button top-right, green "DITA" pill + Title/Full width/Font/Orient/Borders row below.

- [ ] **Step 3: Commit**

```bash
git add src/PDF_PDM/tables/editor_v2.py
git commit -m "feat(pdf_pdm): editor_v2 title bar and DITA strip"
```

---

### Task 6: Toolbar (all buttons, no logic yet)

**Files:** Modify `src/PDF_PDM/tables/editor_v2.py`

- [ ] **Step 1: Add `_build_toolbar`**

Append to `_build_ui` after the DITA strip:

```python
# Toolbar
self.toolbar_frame = tk.Frame(self.frame, bg=TOOLBAR_BG, height=38)
self.toolbar_frame.pack(side=tk.TOP, fill=tk.X)
self.toolbar_frame.pack_propagate(False)
self._build_toolbar(self.toolbar_frame)
tk.Frame(self.frame, height=1, bg=BORDER_COLOR).pack(side=tk.TOP, fill=tk.X)
```

Add the method:

```python
def _build_toolbar(self, parent: tk.Frame) -> None:
    # Configure ttk button styles once
    style = ttk.Style()
    style.configure("TB.TButton", font=FONT_UI_SMALL, padding=(10, 5), relief="flat",
                    background="white", borderwidth=1)
    style.map("TB.TButton",
              background=[("active", "#f0f2f6")],
              bordercolor=[("active", "#b5b8bd")])
    style.configure("TBPrimary.TButton", font=("Segoe UI", 9, "bold"),
                    padding=(10, 5), background=DITA_PILL_BG, foreground="white",
                    borderwidth=1)
    style.map("TBPrimary.TButton",
              background=[("active", "#2db348")])

    def group(parent):
        f = tk.Frame(parent, bg=TOOLBAR_BG)
        f.pack(side=tk.LEFT, padx=(10, 10), pady=4)
        sep = tk.Frame(parent, width=1, bg="#e8eaef")
        sep.pack(side=tk.LEFT, fill=tk.Y, pady=6)
        return f

    # Undo/Redo
    g = group(parent)
    ttk.Button(g, text="Undo", style="TB.TButton", command=self.undo).pack(side=tk.LEFT, padx=1)
    ttk.Button(g, text="Redo", style="TB.TButton", command=self.redo).pack(side=tk.LEFT, padx=1)

    # Header
    g = group(parent)
    ttk.Button(g, text="Mark Header", style="TBPrimary.TButton",
               command=self.mark_header).pack(side=tk.LEFT, padx=1)
    ttk.Button(g, text="Clear Header", style="TB.TButton",
               command=self.clear_header).pack(side=tk.LEFT, padx=1)

    # Merge/Split/Rotate
    g = group(parent)
    ttk.Button(g, text="Merge", style="TB.TButton", command=self.merge_selection).pack(side=tk.LEFT, padx=1)
    ttk.Button(g, text="Unmerge", style="TB.TButton", command=self.unmerge_selection).pack(side=tk.LEFT, padx=1)
    ttk.Button(g, text="Rotate", style="TB.TButton", command=self.rotate_cell).pack(side=tk.LEFT, padx=1)

    # Insert/Delete
    g = group(parent)
    self.insert_row_btn = ttk.Menubutton(g, text="Insert Row ▾", style="TB.TButton")
    m = tk.Menu(self.insert_row_btn, tearoff=0)
    m.add_command(label="Insert Above", accelerator="Ctrl++",
                  command=lambda: self.insert_row(above=True))
    m.add_command(label="Insert Below", accelerator="Ctrl+Shift++",
                  command=lambda: self.insert_row(above=False))
    self.insert_row_btn["menu"] = m
    self.insert_row_btn.pack(side=tk.LEFT, padx=1)

    self.insert_col_btn = ttk.Menubutton(g, text="Insert Col ▾", style="TB.TButton")
    m = tk.Menu(self.insert_col_btn, tearoff=0)
    m.add_command(label="Insert Left", command=lambda: self.insert_col(left=True))
    m.add_command(label="Insert Right", command=lambda: self.insert_col(left=False))
    self.insert_col_btn["menu"] = m
    self.insert_col_btn.pack(side=tk.LEFT, padx=1)

    ttk.Button(g, text="Delete", style="TB.TButton",
               command=self.delete_selection).pack(side=tk.LEFT, padx=1)

    # Formatting
    g = group(parent)
    ttk.Button(g, text="B", style="TB.TButton", width=3, command=self.toggle_bold).pack(side=tk.LEFT, padx=1)
    ttk.Button(g, text="I", style="TB.TButton", width=3, command=self.toggle_italic).pack(side=tk.LEFT, padx=1)
    self.align_btn = ttk.Menubutton(g, text="Align ▾", style="TB.TButton")
    m = tk.Menu(self.align_btn, tearoff=0)
    m.add_command(label="Left", accelerator="Ctrl+L", command=lambda: self.set_align("left"))
    m.add_command(label="Center", accelerator="Ctrl+E", command=lambda: self.set_align("center"))
    m.add_command(label="Right", accelerator="Ctrl+R", command=lambda: self.set_align("right"))
    self.align_btn["menu"] = m
    self.align_btn.pack(side=tk.LEFT, padx=1)

    # Validate
    g = group(parent)
    ttk.Button(g, text="Validate", style="TB.TButton", command=self.run_validation).pack(side=tk.LEFT, padx=1)

# Stub methods (filled in subsequent tasks)
def undo(self): pass
def redo(self): pass
def mark_header(self): pass
def clear_header(self): pass
def merge_selection(self): pass
def unmerge_selection(self): pass
def rotate_cell(self): pass
def insert_row(self, above: bool): pass
def insert_col(self, left: bool): pass
def delete_selection(self): pass
def toggle_bold(self): pass
def toggle_italic(self): pass
def set_align(self, align: str): pass
def run_validation(self): pass
```

- [ ] **Step 2: Visual verification**

Run: `cd src/PDF_PDM && python tmp_preview.py`
Expected: Window shows title bar + DITA strip + toolbar with all buttons (Undo/Redo/Mark Header/Clear Header/Merge/Unmerge/Rotate/Insert Row▾/Insert Col▾/Delete/B/I/Align▾/Validate). Dropdowns should open with correct options.

- [ ] **Step 3: Commit**

```bash
git add src/PDF_PDM/tables/editor_v2.py
git commit -m "feat(pdf_pdm): editor_v2 full toolbar with dropdowns"
```

---

### Task 7: Canvas grid rendering (no interaction yet)

**Files:** Modify `src/PDF_PDM/tables/editor_v2.py`

- [ ] **Step 1: Add canvas area to `_build_ui`**

Append to `_build_ui`:

```python
# Canvas area
canvas_area = tk.Frame(self.frame, bg=CANVAS_BG)
canvas_area.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
self._build_canvas(canvas_area)
```

Add `_build_canvas`:

```python
def _build_canvas(self, parent: tk.Frame) -> None:
    self.canvas = tk.Canvas(parent, bg=CANVAS_BG, highlightthickness=0)
    vsb = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.canvas.yview)
    hsb = ttk.Scrollbar(parent, orient=tk.HORIZONTAL, command=self.canvas.xview)
    self.canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

    self.canvas.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="ew")
    parent.grid_rowconfigure(0, weight=1)
    parent.grid_columnconfigure(0, weight=1)

    self.canvas.bind("<Configure>", lambda e: self.redraw())
```

Add rendering methods:

```python
def _compute_col_px_widths(self) -> None:
    """Compute pixel widths from colspec ratios based on canvas width."""
    canvas_w = max(self.canvas.winfo_width(), 400) - ROW_NUM_WIDTH - 2
    # Parse ratios from col_widths (e.g. "1*" -> 1.0, "2*" -> 2.0, "60" -> fixed)
    ratios = []
    fixed_total = 0
    for w in self.grid.col_widths:
        if w.endswith("*"):
            ratios.append(("prop", float(w[:-1]) if w[:-1] else 1.0))
        else:
            try:
                v = float(w)
                ratios.append(("fixed", v)); fixed_total += v
            except ValueError:
                ratios.append(("prop", 1.0))
    prop_total = sum(v for k, v in ratios if k == "prop")
    remain = max(canvas_w - fixed_total, CELL_MIN_WIDTH * len([r for r in ratios if r[0] == "prop"]))
    out = []
    for kind, v in ratios:
        if kind == "prop":
            out.append(max(int(remain * v / prop_total), CELL_MIN_WIDTH) if prop_total > 0 else CELL_MIN_WIDTH)
        else:
            out.append(int(v))
    self.col_widths_px = out

def redraw(self) -> None:
    self.canvas.delete("all")
    if self.grid.rows == 0 or self.grid.cols == 0:
        return
    self._compute_col_px_widths()
    total_w = ROW_NUM_WIDTH + sum(self.col_widths_px)
    total_h = COL_HEADER_HEIGHT + self.grid.rows * CELL_HEIGHT + DIVIDER_HEIGHT
    self.canvas.configure(scrollregion=(0, 0, total_w, total_h))
    self._draw_col_headers()
    self._draw_cells()
    self._draw_row_numbers()
    self._draw_header_divider()

def _draw_col_headers(self) -> None:
    # Top-left corner
    self.canvas.create_rectangle(0, 0, ROW_NUM_WIDTH, COL_HEADER_HEIGHT,
                                  fill=COL_HEADER_BG, outline=GRID_LINE_HEAVY, width=1)
    x = ROW_NUM_WIDTH
    for c in range(self.grid.cols):
        w = self.col_widths_px[c]
        self.canvas.create_rectangle(x, 0, x + w, COL_HEADER_HEIGHT,
                                      fill=COL_HEADER_BG, outline=GRID_LINE, width=1)
        letter = self._col_letter(c)
        self.canvas.create_text(x + w/2, 6, text=letter, anchor="n",
                                font=FONT_UI_TINY, fill=COL_HEADER_FG)
        # Colspec ratio below
        ratio = self.grid.col_widths[c] if c < len(self.grid.col_widths) else "1*"
        self.canvas.create_text(x + w/2, COL_HEADER_HEIGHT - 3, text=ratio,
                                anchor="s", font=("Segoe UI", 7, "bold"),
                                fill=DITA_PILL_BG)
        x += w
    # Bottom heavy border
    self.canvas.create_line(0, COL_HEADER_HEIGHT, total_w if False else x,
                             COL_HEADER_HEIGHT, fill=GRID_LINE_HEAVY, width=2)

def _draw_row_numbers(self) -> None:
    y = COL_HEADER_HEIGHT
    for r in range(self.grid.rows):
        is_header = r in self.grid.header_rows
        bg = HEADER_NUM_BG if is_header else ROW_NUM_BG
        fg = HEADER_NUM_FG if is_header else ROW_NUM_FG
        if self.selection.kind == "row" and r in self.selection.rows():
            bg = SELECTED_ROW_NUM_BG; fg = SELECTED_ROW_NUM_FG
        self.canvas.create_rectangle(0, y, ROW_NUM_WIDTH, y + CELL_HEIGHT,
                                      fill=bg, outline=GRID_LINE, width=1)
        label = "HDR" if is_header else str(r + 1)
        font = ("Segoe UI", 8, "bold") if is_header else FONT_UI_TINY
        self.canvas.create_text(ROW_NUM_WIDTH/2, y + CELL_HEIGHT/2,
                                text=label, font=font, fill=fg)
        y += CELL_HEIGHT
    # Heavy right border
    self.canvas.create_line(ROW_NUM_WIDTH, COL_HEADER_HEIGHT, ROW_NUM_WIDTH, y,
                             fill=GRID_LINE_HEAVY, width=2)

def _draw_cells(self) -> None:
    y = COL_HEADER_HEIGHT
    for r in range(self.grid.rows):
        x = ROW_NUM_WIDTH
        is_header = r in self.grid.header_rows
        for c in range(self.grid.cols):
            w = self.col_widths_px[c]
            cell = self.grid.cells[r][c]
            bg = HEADER_ROW_BG if is_header else CANVAS_BG
            fg = HEADER_ROW_FG if is_header else "#222"
            if (r, c) in self.selection.cells and self.selection.kind in ("cell", "range", "multi"):
                bg = SELECTED_BG
            self.canvas.create_rectangle(x, y, x + w, y + CELL_HEIGHT,
                                          fill=bg, outline=GRID_LINE, width=1)
            if cell.confidence_category == "low":
                self.canvas.create_rectangle(x+1, y+1, x + w-1, y + CELL_HEIGHT-1,
                                              outline=LOW_CONF_BORDER, width=2, dash=(3, 2))
            # Text
            anchor = {"left": "w", "center": "center", "right": "e"}.get(cell.align, "w")
            tx = x + 8 if cell.align == "left" else (x + w - 8 if cell.align == "right" else x + w/2)
            font_spec = ("Segoe UI", 10, "bold" if (cell.bold or is_header) else "normal")
            if cell.italic:
                font_spec = ("Segoe UI", 10, "italic" + (" bold" if (cell.bold or is_header) else ""))
            self.canvas.create_text(tx, y + CELL_HEIGHT/2, text=cell.text[:100],
                                    anchor=anchor, font=font_spec, fill=fg,
                                    width=w - 16)
            x += w
        y += CELL_HEIGHT
    # Selection border overlay
    if self.selection.kind in ("cell", "range") and self.selection.cells:
        rs = sorted(self.selection.cells)
        r0, c0 = rs[0]; r1, c1 = rs[-1]
        x0 = ROW_NUM_WIDTH + sum(self.col_widths_px[:c0])
        x1 = ROW_NUM_WIDTH + sum(self.col_widths_px[:c1+1])
        y0 = COL_HEADER_HEIGHT + r0 * CELL_HEIGHT
        y1 = COL_HEADER_HEIGHT + (r1+1) * CELL_HEIGHT
        self.canvas.create_rectangle(x0, y0, x1, y1, outline=SELECTED_BORDER, width=2)

def _draw_header_divider(self) -> None:
    """Green divider line below the last header row."""
    if not self.grid.header_rows:
        return
    last_header = max(self.grid.header_rows)
    y = COL_HEADER_HEIGHT + (last_header + 1) * CELL_HEIGHT
    total_w = ROW_NUM_WIDTH + sum(self.col_widths_px)
    self.canvas.create_rectangle(0, y, total_w, y + DIVIDER_HEIGHT,
                                  fill=DIVIDER_COLOR, outline="")

def _col_letter(self, idx: int) -> str:
    # 0->A, 1->B, ..., 25->Z, 26->AA, etc.
    s = ""
    n = idx
    while True:
        s = chr(ord("A") + n % 26) + s
        n = n // 26 - 1
        if n < 0: break
    return s
```

- [ ] **Step 2: Seed test data to verify rendering**

Temporarily add at end of `__init__` (will be removed after Task 20):

```python
# DEV: seed grid for visual test
self.grid = EditorGrid.new(rows=5, cols=5)
self.grid.header_rows = {0}
for c, h in enumerate(["Model", "Power", "Voltage", "Type", "ID"]):
    self.grid.cells[0][c].text = h; self.grid.cells[0][c].bold = True
for r, row_data in enumerate([
    ["ATH600", "12W", "24V", "AC/DC", "001"],
    ["ATH601", "18W", "24V", "AC", "002"],
    ["ATH602", "24W", "48V", "DC", "003"],
    ["ATH603", "30W", "48V", "AC/DC", "004"],
]):
    for c, v in enumerate(row_data):
        self.grid.cells[r+1][c].text = v
self.grid.col_widths = ["1*", "1*", "1*", "2*", "1*"]
self.grid.cells[1][4].confidence_category = "low"
self.frame.after(100, self.redraw)  # redraw after canvas sized
```

- [ ] **Step 3: Visual verification**

Run: `cd src/PDF_PDM && python tmp_preview.py`
Expected: Grid shows A/B/C/D/E column headers with 1*/1*/1*/2*/1* ratio labels, row 1 highlighted blue with "HDR" green row number, body rows 2-5 show ATH600-603 data. Green divider line below row 1. Row 2 col E has dashed orange border (low-conf).

- [ ] **Step 4: Commit**

```bash
git add src/PDF_PDM/tables/editor_v2.py
git commit -m "feat(pdf_pdm): editor_v2 canvas grid rendering with header rows"
```

---

### Task 8: Click selection (cell, row, column)

**Files:** Modify `src/PDF_PDM/tables/editor_v2.py`

- [ ] **Step 1: Add hit-testing + click handler**

Bind in `_build_canvas`:

```python
self.canvas.bind("<Button-1>", self._on_canvas_click)
self.canvas.bind("<Shift-Button-1>", self._on_canvas_shift_click)
self.canvas.bind("<Control-Button-1>", self._on_canvas_ctrl_click)
```

Add methods:

```python
def _hit_test(self, x: int, y: int):
    """Return ('cell', r, c), ('row_num', r), ('col_letter', c), or None."""
    cx = self.canvas.canvasx(x); cy = self.canvas.canvasy(y)
    if cy < COL_HEADER_HEIGHT:
        if cx < ROW_NUM_WIDTH: return None
        col = self._x_to_col(cx)
        return ("col_letter", col) if col is not None else None
    r = int((cy - COL_HEADER_HEIGHT) // CELL_HEIGHT)
    if r >= self.grid.rows: return None
    if cx < ROW_NUM_WIDTH:
        return ("row_num", r)
    c = self._x_to_col(cx)
    return ("cell", r, c) if c is not None else None

def _x_to_col(self, cx: float):
    x = ROW_NUM_WIDTH
    for c in range(self.grid.cols):
        w = self.col_widths_px[c]
        if x <= cx < x + w: return c
        x += w
    return None

def _on_canvas_click(self, event):
    self._commit_edit()
    hit = self._hit_test(event.x, event.y)
    if hit is None: return
    if hit[0] == "cell":
        r, c = hit[1], hit[2]
        self.selection = Selection(kind="cell", anchor=(r, c), cells={(r, c)})
    elif hit[0] == "row_num":
        r = hit[1]
        self.selection = Selection(kind="row", anchor=(r, 0),
                                   cells={(r, c) for c in range(self.grid.cols)})
    elif hit[0] == "col_letter":
        c = hit[1]
        self.selection = Selection(kind="col", anchor=(0, c),
                                   cells={(r, c) for r in range(self.grid.rows)})
    self.redraw()
    self._update_statusbar()

def _on_canvas_shift_click(self, event):
    hit = self._hit_test(event.x, event.y)
    if hit is None or hit[0] != "cell": return
    r1, c1 = hit[1], hit[2]
    r0, c0 = self.selection.anchor
    rmin, rmax = min(r0, r1), max(r0, r1)
    cmin, cmax = min(c0, c1), max(c0, c1)
    cells = {(r, c) for r in range(rmin, rmax+1) for c in range(cmin, cmax+1)}
    self.selection = Selection(kind="range", anchor=(r0, c0), cells=cells)
    self.redraw()
    self._update_statusbar()

def _on_canvas_ctrl_click(self, event):
    hit = self._hit_test(event.x, event.y)
    if hit is None or hit[0] != "cell": return
    r, c = hit[1], hit[2]
    if (r, c) in self.selection.cells:
        self.selection.cells.discard((r, c))
    else:
        self.selection.cells.add((r, c))
    self.selection.kind = "multi"
    self.redraw()
    self._update_statusbar()

def _update_statusbar(self) -> None:
    """Placeholder — filled in Task 17."""
    pass

def _commit_edit(self) -> None:
    """Placeholder — filled in Task 10."""
    pass
```

- [ ] **Step 2: Visual verification**

Run: `cd src/PDF_PDM && python tmp_preview.py`
Expected:
- Click a cell → single cell highlighted green with green border
- Click row number → whole row light-green, row number green
- Click column letter → whole column light-green
- Shift+click another cell → range selected
- Ctrl+click → multi-cell selection (non-contiguous)

- [ ] **Step 3: Commit**

```bash
git add src/PDF_PDM/tables/editor_v2.py
git commit -m "feat(pdf_pdm): editor_v2 click/shift/ctrl selection"
```

---

### Task 9: Cell editing (double-click, F2, Entry overlay)

**Files:** Modify `src/PDF_PDM/tables/editor_v2.py`

- [ ] **Step 1: Implement edit overlay**

Bind in `_build_canvas`:

```python
self.canvas.bind("<Double-Button-1>", self._on_canvas_double_click)
```

Add methods:

```python
def _on_canvas_double_click(self, event):
    hit = self._hit_test(event.x, event.y)
    if hit and hit[0] == "cell":
        self._begin_edit(hit[1], hit[2])

def _begin_edit(self, r: int, c: int) -> None:
    self._commit_edit()
    if r >= self.grid.rows or c >= self.grid.cols: return
    self._edit_cell = (r, c)
    x = ROW_NUM_WIDTH + sum(self.col_widths_px[:c])
    y = COL_HEADER_HEIGHT + r * CELL_HEIGHT
    w = self.col_widths_px[c]
    cell = self.grid.cells[r][c]
    self._edit_entry = tk.Entry(self.canvas, font=FONT_CELL, bd=2,
                                 relief="solid", highlightthickness=0)
    self._edit_entry.insert(0, cell.text)
    self._edit_entry.select_range(0, tk.END)
    self.canvas.create_window(x, y, anchor="nw", width=w, height=CELL_HEIGHT,
                               window=self._edit_entry, tags="edit_entry")
    self._edit_entry.focus_set()
    self._edit_entry.bind("<Return>", lambda e: self._commit_edit_and_next("down"))
    self._edit_entry.bind("<Tab>", lambda e: self._commit_edit_and_next("right"))
    self._edit_entry.bind("<Shift-Tab>", lambda e: self._commit_edit_and_next("left"))
    self._edit_entry.bind("<Escape>", lambda e: self._cancel_edit())

def _commit_edit(self) -> None:
    if self._edit_entry is None: return
    r, c = self._edit_cell
    new_text = self._edit_entry.get()
    if self.grid.cells[r][c].text != new_text:
        self._push_history()
        self.grid.cells[r][c].text = new_text
    self._cancel_edit()
    self.redraw()

def _cancel_edit(self) -> None:
    if self._edit_entry is not None:
        self._edit_entry.destroy()
        self.canvas.delete("edit_entry")
        self._edit_entry = None
        self._edit_cell = None

def _commit_edit_and_next(self, direction: str):
    r, c = self._edit_cell
    self._commit_edit()
    if direction == "down" and r + 1 < self.grid.rows:
        self._begin_edit(r + 1, c)
    elif direction == "right" and c + 1 < self.grid.cols:
        self._begin_edit(r, c + 1)
    elif direction == "left" and c > 0:
        self._begin_edit(r, c - 1)
    return "break"

def _push_history(self) -> None:
    """Placeholder — filled in Task 15."""
    pass
```

- [ ] **Step 2: Visual verification**

Run: `cd src/PDF_PDM && python tmp_preview.py`
Expected:
- Double-click a cell → Entry overlays the cell with existing text selected
- Type new text, press Enter → commits, moves to cell below
- Tab → commits, moves right; Shift+Tab → commits, moves left
- Esc → cancels without saving

- [ ] **Step 3: Commit**

```bash
git add src/PDF_PDM/tables/editor_v2.py
git commit -m "feat(pdf_pdm): editor_v2 cell editing via Entry overlay"
```

---

### Task 10: Keyboard navigation (arrow keys, Delete, F2, H)

**Files:** Modify `src/PDF_PDM/tables/editor_v2.py`

- [ ] **Step 1: Bind keyboard shortcuts**

In `_build_canvas`, make canvas focusable:

```python
self.canvas.config(takefocus=True)
self.canvas.bind("<Button-1>", self._on_canvas_click, add="+")
# Ensure focus on click
self.canvas.bind("<Button-1>", lambda e: self.canvas.focus_set(), add="+")
```

Add key bindings after canvas creation:

```python
self.canvas.bind("<Up>",    lambda e: self._move_selection(-1, 0))
self.canvas.bind("<Down>",  lambda e: self._move_selection( 1, 0))
self.canvas.bind("<Left>",  lambda e: self._move_selection( 0,-1))
self.canvas.bind("<Right>", lambda e: self._move_selection( 0, 1))
self.canvas.bind("<F2>",    lambda e: self._edit_current())
self.canvas.bind("<Return>", lambda e: self._edit_current())
self.canvas.bind("<Delete>", lambda e: self._clear_selection())
self.canvas.bind("<KeyPress-h>", lambda e: self.mark_header())
self.canvas.bind("<KeyPress-H>", lambda e: self.mark_header())
self.canvas.bind("<KeyPress-r>", lambda e: self.rotate_cell())
self.canvas.bind("<KeyPress-R>", lambda e: self.rotate_cell())
self.canvas.bind("<Control-z>", lambda e: self.undo())
self.canvas.bind("<Control-y>", lambda e: self.redo())
self.canvas.bind("<Control-m>", lambda e: self.merge_selection())
self.canvas.bind("<Control-Shift-M>", lambda e: self.unmerge_selection())
self.canvas.bind("<Control-b>", lambda e: self.toggle_bold())
self.canvas.bind("<Control-i>", lambda e: self.toggle_italic())
self.canvas.bind("<Control-l>", lambda e: self.set_align("left"))
self.canvas.bind("<Control-e>", lambda e: self.set_align("center"))
self.canvas.bind("<Control-r>", lambda e: self.set_align("right"))
```

Add methods:

```python
def _move_selection(self, dr: int, dc: int):
    if self.selection.kind == "none":
        self.selection = Selection(kind="cell", anchor=(0, 0), cells={(0, 0)})
    else:
        r, c = self.selection.anchor
        r = max(0, min(r + dr, self.grid.rows - 1))
        c = max(0, min(c + dc, self.grid.cols - 1))
        self.selection = Selection(kind="cell", anchor=(r, c), cells={(r, c)})
    self.redraw()
    self._update_statusbar()
    return "break"

def _edit_current(self):
    if self.selection.kind == "cell":
        r, c = self.selection.anchor
        self._begin_edit(r, c)
    return "break"

def _clear_selection(self):
    if self.selection.is_empty(): return
    self._push_history()
    for (r, c) in self.selection.cells:
        self.grid.cells[r][c].text = ""
    self.redraw()
    return "break"
```

- [ ] **Step 2: Visual verification**

Run: `cd src/PDF_PDM && python tmp_preview.py`
Expected:
- Click canvas → focus grabs
- Arrow keys move single-cell selection
- F2 or Enter on a selection → opens Entry edit overlay
- Delete key → clears selected cell text
- (Other shortcuts are stubs — verify they don't error)

- [ ] **Step 3: Commit**

```bash
git add src/PDF_PDM/tables/editor_v2.py
git commit -m "feat(pdf_pdm): editor_v2 keyboard navigation + shortcuts"
```

---

### Task 11: Mark Header row (core feature)

**Files:** Modify `src/PDF_PDM/tables/editor_v2.py`

- [ ] **Step 1: Implement mark_header / clear_header**

Replace the stubs:

```python
def mark_header(self) -> None:
    if self.selection.is_empty(): return
    rows = self.selection.rows()
    self._push_history()
    self.grid.mark_header(rows)
    self.redraw()
    self._update_statusbar()

def clear_header(self) -> None:
    if self.selection.is_empty(): return
    rows = self.selection.rows()
    self._push_history()
    self.grid.clear_header(rows)
    self.redraw()
    self._update_statusbar()
```

- [ ] **Step 2: Visual verification**

Run: `cd src/PDF_PDM && python tmp_preview.py`
Expected:
- Click row number 3 → row highlighted green
- Click "Mark Header" button OR press H → row 3 now blue with "HDR" label + green row number
- Green divider line moves to below row 3
- Click "Clear Header" → row reverts to body styling

- [ ] **Step 3: Commit**

```bash
git add src/PDF_PDM/tables/editor_v2.py
git commit -m "feat(pdf_pdm): editor_v2 mark/clear header row feature"
```

---

### Task 12: Insert/Delete rows and columns

**Files:** Modify `src/PDF_PDM/tables/editor_v2.py`

- [ ] **Step 1: Implement insert_row, insert_col, delete_selection**

```python
def insert_row(self, above: bool) -> None:
    if self.selection.is_empty():
        at = self.grid.rows - 1; above = False
    else:
        at = min(self.selection.rows())
    self._push_history()
    self.grid.insert_row(at=at, above=above)
    self.redraw()
    self._update_statusbar()

def insert_col(self, left: bool) -> None:
    if self.selection.is_empty():
        at = self.grid.cols - 1; left = False
    else:
        at = min(self.selection.cols())
    self._push_history()
    self.grid.insert_col(at=at, left=left)
    self.redraw()
    self._update_statusbar()

def delete_selection(self) -> None:
    if self.selection.is_empty(): return
    self._push_history()
    if self.selection.kind == "row":
        # Delete full rows, highest index first
        for r in sorted(self.selection.rows(), reverse=True):
            self.grid.delete_row(r)
    elif self.selection.kind == "col":
        for c in sorted(self.selection.cols(), reverse=True):
            self.grid.delete_col(c)
    else:
        # Clear content of selected cells
        for (r, c) in self.selection.cells:
            self.grid.cells[r][c].text = ""
    self.selection = Selection()
    self.redraw()
    self._update_statusbar()
```

Also bind Ctrl++ / Ctrl+Shift++:

```python
self.canvas.bind("<Control-plus>", lambda e: self.insert_row(above=True))
self.canvas.bind("<Control-Shift-plus>", lambda e: self.insert_row(above=False))
```

- [ ] **Step 2: Visual verification**

Run: `cd src/PDF_PDM && python tmp_preview.py`
Expected:
- Click row 3 → select row
- Click "Insert Row ▾" → "Insert Above" → new empty row at position 3, old rows pushed down
- Click "Insert Below" → new row between 3 and 4
- Click column C → "Insert Col ▾" → "Insert Left" → new column to the left
- Select row 2 → click "Delete" → row removed, grid shrinks

- [ ] **Step 3: Commit**

```bash
git add src/PDF_PDM/tables/editor_v2.py
git commit -m "feat(pdf_pdm): editor_v2 insert/delete rows and columns"
```

---

### Task 13: Merge / Unmerge

**Files:** Modify `src/PDF_PDM/tables/editor_v2.py`

- [ ] **Step 1: Implement merge/unmerge + render merged cells**

```python
def merge_selection(self) -> None:
    if self.selection.kind not in ("range", "cell"): return
    if len(self.selection.cells) < 2: return
    # Validate rectangular
    rs = sorted({r for (r, _) in self.selection.cells})
    cs = sorted({c for (_, c) in self.selection.cells})
    if rs != list(range(rs[0], rs[-1]+1)) or cs != list(range(cs[0], cs[-1]+1)):
        return
    # Validate doesn't cross header/body boundary
    has_header = any(r in self.grid.header_rows for r in rs)
    has_body = any(r not in self.grid.header_rows for r in rs)
    if has_header and has_body:
        return
    anchor = (rs[0], cs[0])
    # Combine text into anchor, clear others
    texts = [self.grid.cells[r][c].text for r in rs for c in cs
             if self.grid.cells[r][c].text.strip()]
    self._push_history()
    if texts:
        self.grid.cells[rs[0]][cs[0]].text = " ".join(texts)
    for r in rs:
        for c in cs:
            if (r, c) != anchor:
                self.grid.cells[r][c].text = ""
    self.grid.merge(anchor=anchor, rowspan=len(rs), colspan=len(cs))
    self.redraw()

def unmerge_selection(self) -> None:
    if self.selection.is_empty(): return
    self._push_history()
    for (r, c) in list(self.selection.cells):
        if (r, c) in self.grid.merges:
            self.grid.unmerge((r, c))
    self.redraw()
```

Update `_draw_cells` to paint merged cells (draw them AFTER normal cells):

Add after the main cell drawing loop in `_draw_cells`:

```python
# Overlay merged-cell anchors (paint a single rectangle covering the span)
for (ar, ac), (rs, cs) in self.grid.merges.items():
    if ar >= self.grid.rows or ac >= self.grid.cols: continue
    x0 = ROW_NUM_WIDTH + sum(self.col_widths_px[:ac])
    x1 = ROW_NUM_WIDTH + sum(self.col_widths_px[:ac+cs])
    y0 = COL_HEADER_HEIGHT + ar * CELL_HEIGHT
    y1 = COL_HEADER_HEIGHT + (ar+rs) * CELL_HEIGHT
    self.canvas.create_rectangle(x0, y0, x1, y1, fill=MERGED_BG, outline=GRID_LINE, width=1)
    cell = self.grid.cells[ar][ac]
    self.canvas.create_text(x0 + 8, (y0+y1)/2, text=cell.text, anchor="w",
                             font=FONT_CELL, width=(x1-x0)-16, fill="#1565C0")
```

- [ ] **Step 2: Visual verification**

Run: `cd src/PDF_PDM && python tmp_preview.py`
Expected:
- Click cell (1,0) → Shift+click (2,2) → range of 6 cells selected
- Click "Merge" → cells combine into one light-blue region
- Click the merged region → "Unmerge" → splits back

- [ ] **Step 3: Commit**

```bash
git add src/PDF_PDM/tables/editor_v2.py
git commit -m "feat(pdf_pdm): editor_v2 merge/unmerge with rectangular validation"
```

---

### Task 14: Formatting (align, bold, italic, rotate)

**Files:** Modify `src/PDF_PDM/tables/editor_v2.py`

- [ ] **Step 1: Implement set_align, toggle_bold, toggle_italic, rotate_cell**

```python
def _for_each_selected(self, fn: Callable[[CellData], None]):
    if self.selection.is_empty(): return
    self._push_history()
    for (r, c) in self.selection.cells:
        fn(self.grid.cells[r][c])
    self.redraw()

def set_align(self, align: str) -> None:
    self._for_each_selected(lambda c: setattr(c, "align", align))

def toggle_bold(self) -> None:
    self._for_each_selected(lambda c: setattr(c, "bold", not c.bold))

def toggle_italic(self) -> None:
    self._for_each_selected(lambda c: setattr(c, "italic", not c.italic))

def rotate_cell(self) -> None:
    self._for_each_selected(lambda c: setattr(c, "rotate", (c.rotate + 1) % 4))
```

- [ ] **Step 2: Visual verification**

Run: `cd src/PDF_PDM && python tmp_preview.py`
Expected:
- Select a cell, click B → text becomes bold
- Click I → italic
- Align ▾ → Center → text center-aligned
- Rotate button cycles through 4 states (display: rotated text can be shown as a small indicator badge if full rotation is too complex; simplest is to just track the value and show "↻90" badge in the corner)

**Note on rotate visual:** For v1, showing actual rotated text on canvas is complex. Simply render a small badge "↻1" / "↻2" / "↻3" in the top-right corner of the cell when `rotate != 0`. The actual rotation only matters for DITA export.

Update `_draw_cells` to add rotation badge:

```python
if cell.rotate != 0:
    self.canvas.create_text(x + w - 4, y + 2, text=f"↻{cell.rotate}",
                             anchor="ne", font=("Segoe UI", 7, "bold"), fill="#1565C0")
```

- [ ] **Step 3: Commit**

```bash
git add src/PDF_PDM/tables/editor_v2.py
git commit -m "feat(pdf_pdm): editor_v2 formatting (align/bold/italic/rotate)"
```

---

### Task 15: Undo/Redo via extended history

**Files:** Modify `src/PDF_PDM/tables/editor_v2.py`

- [ ] **Step 1: Add history implementation**

```python
import copy

class _GridHistory:
    def __init__(self, max_size: int = 50):
        self.undo_stack: list = []
        self.redo_stack: list = []
        self.max_size = max_size

    def push(self, grid: EditorGrid):
        self.undo_stack.append(copy.deepcopy(grid))
        if len(self.undo_stack) > self.max_size: self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self, current: EditorGrid):
        if not self.undo_stack: return None
        self.redo_stack.append(copy.deepcopy(current))
        return self.undo_stack.pop()

    def redo(self, current: EditorGrid):
        if not self.redo_stack: return None
        self.undo_stack.append(copy.deepcopy(current))
        return self.redo_stack.pop()
```

In `CanvasTableEditor.__init__`:

```python
self.history = _GridHistory()
```

Implement push_history, undo, redo:

```python
def _push_history(self) -> None:
    self.history.push(self.grid)

def undo(self) -> None:
    restored = self.history.undo(self.grid)
    if restored is not None:
        self.grid = restored
        self.selection = Selection()
        self.redraw()
        self._update_statusbar()

def redo(self) -> None:
    restored = self.history.redo(self.grid)
    if restored is not None:
        self.grid = restored
        self.selection = Selection()
        self.redraw()
        self._update_statusbar()
```

- [ ] **Step 2: Visual verification**

Run: `cd src/PDF_PDM && python tmp_preview.py`
Expected:
- Edit a cell → Ctrl+Z reverts it
- Ctrl+Y re-applies the edit
- Insert row → Ctrl+Z removes it
- All state (content, merges, headers) correctly restored

- [ ] **Step 3: Commit**

```bash
git add src/PDF_PDM/tables/editor_v2.py
git commit -m "feat(pdf_pdm): editor_v2 undo/redo"
```

---

### Task 16: DITA strip state wiring

**Files:** Modify `src/PDF_PDM/tables/editor_v2.py`

- [ ] **Step 1: Add DitaTableAttrs state + wire change handlers**

Add to `CanvasTableEditor.__init__`:

```python
self.dita_attrs_state = {
    "title": "", "pgwide": True, "font_class": "font:10",
    "orient": "port", "frame": "all",
}
```

Implement the change handlers:

```python
def _on_title_change(self):
    self.dita_attrs_state["title"] = self.title_var.get()

def _on_pgwide_change(self):
    self.dita_attrs_state["pgwide"] = self.pgwide_var.get()

def _on_font_change(self):
    label_to_class = {"8pt": "font:8", "10pt": "font:10", "12pt": "font:12"}
    self.dita_attrs_state["font_class"] = label_to_class.get(self.font_var.get(), "font:10")

def _on_orient_change(self):
    self.dita_attrs_state["orient"] = "land" if self.orient_var.get() == "Landscape" else "port"

def _on_frame_change(self):
    label_to_frame = {"All": "all", "Top only": "top", "Sides": "sides", "None": "none"}
    self.dita_attrs_state["frame"] = label_to_frame.get(self.frame_var.get(), "all")
```

- [ ] **Step 2: Visual verification**

Run: `cd src/PDF_PDM && python tmp_preview.py`

Temporarily add a debug button (remove later) that prints `self.dita_attrs_state`. Verify all 5 DITA strip widgets update state when changed.

- [ ] **Step 3: Commit**

```bash
git add src/PDF_PDM/tables/editor_v2.py
git commit -m "feat(pdf_pdm): editor_v2 DITA strip state wiring"
```

---

### Task 17: Status bar + Validate panel

**Files:** Modify `src/PDF_PDM/tables/editor_v2.py`

- [ ] **Step 1: Build statusbar**

Append to `_build_ui`:

```python
# Status bar
self.statusbar_frame = tk.Frame(self.frame, bg=STATUSBAR_BG, height=26,
                                highlightbackground=BORDER_COLOR, highlightthickness=0)
self.statusbar_frame.pack(side=tk.BOTTOM, fill=tk.X)
self.statusbar_frame.pack_propagate(False)
self._build_statusbar(self.statusbar_frame)
tk.Frame(self.frame, height=1, bg=BORDER_COLOR).pack(side=tk.BOTTOM, fill=tk.X)
```

Add method:

```python
def _build_statusbar(self, parent: tk.Frame):
    left = tk.Frame(parent, bg=STATUSBAR_BG)
    left.pack(side=tk.LEFT, padx=14, pady=4)
    self.status_selection = tk.Label(left, text="No selection",
                                     font=FONT_UI_TINY, bg=STATUSBAR_BG, fg="#555")
    self.status_selection.pack(side=tk.LEFT, padx=(0, 16))
    self.status_size = tk.Label(left, text="", font=FONT_UI_TINY, bg=STATUSBAR_BG, fg="#555")
    self.status_size.pack(side=tk.LEFT, padx=(0, 16))
    self.status_headers = tk.Label(left, text="", font=FONT_UI_TINY, bg=STATUSBAR_BG, fg="#555")
    self.status_headers.pack(side=tk.LEFT, padx=(0, 16))

    right = tk.Frame(parent, bg=STATUSBAR_BG)
    right.pack(side=tk.RIGHT, padx=14, pady=4)
    self.validation_dot = tk.Canvas(right, width=10, height=10, bg=STATUSBAR_BG,
                                     highlightthickness=0)
    self.validation_dot.create_oval(1, 1, 9, 9, fill="#2db348", outline="", tags="dot")
    self.validation_dot.pack(side=tk.LEFT, padx=(0, 6))
    self.validation_label = tk.Label(right, text="DITA ready",
                                      font=("Segoe UI", 8, "bold"), bg=STATUSBAR_BG, fg="#2db348")
    self.validation_label.pack(side=tk.LEFT)
```

Implement the update methods:

```python
def _update_statusbar(self) -> None:
    if self.selection.is_empty():
        self.status_selection.config(text="No selection")
    elif self.selection.kind == "cell":
        r, c = self.selection.anchor
        self.status_selection.config(text=f"Cell {self._col_letter(c)}{r+1}")
    elif self.selection.kind == "row":
        rs = sorted(self.selection.rows())
        self.status_selection.config(text=f"Row{'s' if len(rs)>1 else ''} {', '.join(str(r+1) for r in rs)}")
    elif self.selection.kind == "col":
        cs = sorted(self.selection.cols())
        self.status_selection.config(text=f"Column{'s' if len(cs)>1 else ''} {', '.join(self._col_letter(c) for c in cs)}")
    elif self.selection.kind == "range":
        self.status_selection.config(text=f"Range: {len(self.selection.cells)} cells")
    elif self.selection.kind == "multi":
        self.status_selection.config(text=f"{len(self.selection.cells)} cells")

    self.status_size.config(text=f"{self.grid.rows} rows × {self.grid.cols} cols")
    n_hdr = len(self.grid.header_rows)
    self.status_headers.config(text=f"Headers: {n_hdr} row{'s' if n_hdr != 1 else ''} marked"
                                    if n_hdr > 0 else "No header rows")
    self._update_validation_dot()

def _update_validation_dot(self) -> None:
    issues = validate_grid(self.grid)
    errors = [i for i in issues if i.severity == Severity.ERROR]
    warnings = [i for i in issues if i.severity == Severity.WARNING]
    if errors:
        color, text, fg = "#d13438", f"{len(errors)} error(s)", "#d13438"
    elif warnings:
        color, text, fg = "#F5A623", f"{len(warnings)} warning(s)", "#F5A623"
    else:
        color, text, fg = "#2db348", "DITA ready", "#2db348"
    self.validation_dot.itemconfig("dot", fill=color)
    self.validation_label.config(text=text, fg=fg)

def run_validation(self) -> None:
    issues = validate_grid(self.grid)
    dlg = tk.Toplevel(self.frame); dlg.title("Table Validation"); dlg.geometry("500x400")
    dlg.transient(self.frame)
    if not issues:
        tk.Label(dlg, text="No issues found — DITA export ready.",
                 font=("Segoe UI", 11, "bold"), fg="#2db348", pady=30).pack()
    else:
        for issue in issues:
            color = {"error": "#d13438", "warning": "#F5A623", "info": "#0078d4"}[issue.severity.value]
            f = tk.Frame(dlg, pady=6, padx=14); f.pack(fill=tk.X)
            tk.Label(f, text=issue.severity.value.upper(), fg=color,
                     font=("Segoe UI", 9, "bold"), width=10, anchor="w").pack(side=tk.LEFT)
            loc = f" (row {issue.row+1}, col {issue.col+1})" if issue.row >= 0 and issue.col >= 0 \
                  else (f" (row {issue.row+1})" if issue.row >= 0 else "")
            tk.Label(f, text=issue.message + loc, font=FONT_UI_SMALL, anchor="w",
                     justify="left", wraplength=380).pack(side=tk.LEFT, fill=tk.X)
    ttk.Button(dlg, text="Close", command=dlg.destroy).pack(pady=8)
```

- [ ] **Step 2: Visual verification**

Run: `cd src/PDF_PDM && python tmp_preview.py`
Expected:
- Status bar shows cell ref, size, header count, DITA ready dot
- Selecting cells updates the status
- Click Validate → opens dialog with issues (or "DITA export ready")
- Changing col_widths to all fixed (manually in code) → dot turns red

- [ ] **Step 3: Commit**

```bash
git add src/PDF_PDM/tables/editor_v2.py
git commit -m "feat(pdf_pdm): editor_v2 status bar + validation panel"
```

---

### Task 18: Shortcuts popup

**Files:** Modify `src/PDF_PDM/tables/editor_v2.py`

- [ ] **Step 1: Implement `_show_shortcuts`**

```python
SHORTCUTS = [
    ("Navigation", [
        ("↑ ↓ ← →", "Navigate cells"),
        ("Tab / Shift+Tab", "Move right / left"),
        ("Enter", "Move down"),
    ]),
    ("Editing", [
        ("F2", "Edit current cell"),
        ("Esc", "Cancel edit"),
        ("Delete", "Clear content"),
    ]),
    ("Structure", [
        ("H", "Toggle header row"),
        ("Ctrl+M / Ctrl+Shift+M", "Merge / Unmerge"),
        ("R", "Rotate cell"),
        ("Ctrl++", "Insert row above"),
        ("Ctrl+Shift++", "Insert row below"),
    ]),
    ("Clipboard & History", [
        ("Ctrl+Z / Ctrl+Y", "Undo / Redo"),
        ("Ctrl+C / V / X", "Copy / Paste / Cut"),
        ("Ctrl+A", "Select all"),
    ]),
    ("Formatting", [
        ("Ctrl+B / Ctrl+I", "Bold / Italic"),
        ("Ctrl+L / Ctrl+E / Ctrl+R", "Align left / center / right"),
    ]),
    ("Validation", [
        ("Ctrl+Shift+V", "Validate table"),
    ]),
]

def _show_shortcuts(self) -> None:
    dlg = tk.Toplevel(self.frame); dlg.title("Keyboard Shortcuts"); dlg.geometry("440x520")
    dlg.transient(self.frame); dlg.resizable(False, False)
    container = tk.Frame(dlg, padx=20, pady=14); container.pack(fill=tk.BOTH, expand=True)
    for section_name, items in SHORTCUTS:
        tk.Label(container, text=section_name, font=("Segoe UI", 10, "bold"),
                 anchor="w").pack(fill=tk.X, pady=(8, 4))
        for combo, desc in items:
            row = tk.Frame(container); row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=combo, font=("Consolas", 9), fg="#555",
                     width=24, anchor="w").pack(side=tk.LEFT)
            tk.Label(row, text=desc, font=FONT_UI_SMALL, anchor="w",
                     fg="#222").pack(side=tk.LEFT)
    ttk.Button(container, text="Close", command=dlg.destroy).pack(pady=10)
```

Bind Ctrl+Shift+V for validate:

```python
self.canvas.bind("<Control-Shift-V>", lambda e: self.run_validation())
```

- [ ] **Step 2: Visual verification**

Run: `cd src/PDF_PDM && python tmp_preview.py`
Expected: Click "? Shortcuts" → popup lists all shortcuts grouped by section.

- [ ] **Step 3: Commit**

```bash
git add src/PDF_PDM/tables/editor_v2.py
git commit -m "feat(pdf_pdm): editor_v2 keyboard shortcuts popup"
```

---

### Task 19: Column width drag-to-resize

**Files:** Modify `src/PDF_PDM/tables/editor_v2.py`

- [ ] **Step 1: Add column edge hit-detection + drag handling**

```python
COL_EDGE_TOLERANCE = 4

def _on_canvas_motion(self, event):
    cx = self.canvas.canvasx(event.x); cy = self.canvas.canvasy(event.y)
    if cy < COL_HEADER_HEIGHT:
        # Check if near a column edge
        x = ROW_NUM_WIDTH
        for c in range(self.grid.cols):
            x += self.col_widths_px[c]
            if abs(cx - x) <= COL_EDGE_TOLERANCE:
                self.canvas.config(cursor="sb_h_double_arrow")
                return
    self.canvas.config(cursor="")

def _on_col_drag_start(self, event):
    cx = self.canvas.canvasx(event.x); cy = self.canvas.canvasy(event.y)
    if cy >= COL_HEADER_HEIGHT: return
    x = ROW_NUM_WIDTH
    for c in range(self.grid.cols):
        x += self.col_widths_px[c]
        if abs(cx - x) <= COL_EDGE_TOLERANCE:
            self._dragging_col = c
            self._drag_start_x = cx
            self._drag_start_w = self.col_widths_px[c]
            return "break"

def _on_col_drag(self, event):
    if not hasattr(self, "_dragging_col") or self._dragging_col is None: return
    cx = self.canvas.canvasx(event.x)
    dx = cx - self._drag_start_x
    new_w = max(CELL_MIN_WIDTH, self._drag_start_w + int(dx))
    # Re-normalize ratios based on new pixel width
    # Find the proportional total of *other* columns, then adjust this column's ratio
    c = self._dragging_col
    self.col_widths_px[c] = new_w
    # Convert all px widths to ratios (1* per CELL_MIN_WIDTH)
    total_px = sum(self.col_widths_px)
    ratios = [max(1.0, round(w / CELL_MIN_WIDTH)) for w in self.col_widths_px]
    self.grid.col_widths = [f"{int(r)}*" for r in ratios]
    self.redraw()
    return "break"

def _on_col_drag_end(self, event):
    if hasattr(self, "_dragging_col"):
        self._dragging_col = None
```

Bind in `_build_canvas`:

```python
self.canvas.bind("<Motion>", self._on_canvas_motion, add="+")
self.canvas.bind("<Button-1>", self._on_col_drag_start, add="+")
self.canvas.bind("<B1-Motion>", self._on_col_drag, add="+")
self.canvas.bind("<ButtonRelease-1>", self._on_col_drag_end, add="+")
```

- [ ] **Step 2: Visual verification**

Run: `cd src/PDF_PDM && python tmp_preview.py`
Expected:
- Hover over column header edge → cursor becomes h-resize
- Click and drag → column width changes
- Colspec label updates (e.g. "1*" → "2*") when width doubles
- Release → stays at new size

- [ ] **Step 3: Commit**

```bash
git add src/PDF_PDM/tables/editor_v2.py
git commit -m "feat(pdf_pdm): editor_v2 column width drag-to-resize"
```

---

### Task 20: Load/save from `Table` object

**Files:** Modify `src/PDF_PDM/tables/editor_v2.py`

- [ ] **Step 1: Implement load_table and to_table**

```python
def load_table(self, table) -> None:
    """Populate grid from a Table object."""
    self._current_table = table
    # Determine dimensions
    if hasattr(table, "get_actual_dimensions"):
        rows, cols = table.get_actual_dimensions()
    else:
        rows, cols = 2, 2
        if table.cells:
            rows = max(c.row + c.rowspan for c in table.cells)
            cols = max(c.col + c.colspan for c in table.cells)
    self.grid = EditorGrid.new(rows=max(rows, 1), cols=max(cols, 1))
    # Populate cells
    for tc in table.cells:
        if tc.row < self.grid.rows and tc.col < self.grid.cols:
            cd = self.grid.cells[tc.row][tc.col]
            cd.text = tc.text
            cd.rotate = getattr(tc, "rotate", 0)
            cd.align = getattr(tc, "align", "left")
            cd.bold = getattr(tc, "bold", False)
            cd.italic = getattr(tc, "italic", False)
            cd.confidence_category = getattr(tc, "confidence_category", "high")
            if tc.rowspan > 1 or tc.colspan > 1:
                self.grid.merge((tc.row, tc.col), tc.rowspan, tc.colspan)
    # Header rows = rows where all cells marked is_header
    for r in range(self.grid.rows):
        if any(getattr(tc, "is_header", False) for tc in table.cells if tc.row == r):
            self.grid.header_rows.add(r)
    # Col widths
    if hasattr(table, "col_widths") and table.col_widths:
        self.grid.col_widths = list(table.col_widths)
    # DITA attrs
    if hasattr(table, "dita_attrs"):
        da = table.dita_attrs
        self.dita_attrs_state = da.to_dict()
        self.title_var.set(da.title)
        self.pgwide_var.set(da.pgwide)
        font_map = {"font:8": "8pt", "font:10": "10pt", "font:12": "12pt"}
        self.font_var.set(font_map.get(da.font_class, "10pt"))
        self.orient_var.set("Landscape" if da.orient == "land" else "Portrait")
        frame_map = {"all": "All", "top": "Top only", "sides": "Sides", "none": "None"}
        self.frame_var.set(frame_map.get(da.frame, "All"))
    self.history = _GridHistory()
    self.selection = Selection()
    self.redraw()
    self._update_statusbar()

def to_table(self):
    """Serialize current grid back to the loaded Table object."""
    from pdf_models import TableCell, DitaTableAttrs, create_table_cell
    if self._current_table is None: return None
    t = self._current_table
    # Rebuild cells from grid
    new_cells = []
    merged_children = set()
    for (ar, ac), (rs, cs) in self.grid.merges.items():
        for dr in range(rs):
            for dc in range(cs):
                if (dr, dc) != (0, 0):
                    merged_children.add((ar + dr, ac + dc))
    for r in range(self.grid.rows):
        for c in range(self.grid.cols):
            if (r, c) in merged_children: continue
            cd = self.grid.cells[r][c]
            rs, cs = self.grid.merges.get((r, c), (1, 1))
            tc = create_table_cell(row=r, col=c, text=cd.text)
            tc.rowspan = rs; tc.colspan = cs
            tc.is_merged = (rs > 1 or cs > 1)
            tc.is_header = r in self.grid.header_rows
            tc.rotate = cd.rotate
            tc.align = cd.align
            tc.bold = cd.bold
            tc.italic = cd.italic
            new_cells.append(tc)
    t.cells = new_cells
    t.col_widths = list(self.grid.col_widths)
    t.dita_attrs = DitaTableAttrs.from_dict(self.dita_attrs_state)
    return t
```

- [ ] **Step 2: Remove temp seed data**

Remove the `# DEV: seed grid` block from `__init__`. Leave `self.grid = EditorGrid.new(rows=2, cols=2)` as the default.

- [ ] **Step 3: Smoke test with a mock Table object**

Update `tmp_preview.py`:

```python
import tkinter as tk, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tables.editor_v2 import CanvasTableEditor
from pdf_models import Table, create_table_cell, create_element, ElementType

root = tk.Tk(); root.geometry("1100x700"); root.title("editor_v2 preview")
editor = CanvasTableEditor(root)

el = create_element(ElementType.TABLE, (0,0,500,300), 0)
t = Table(element=el)
cells = []
headers = ["Model", "Power", "Voltage", "Type", "ID"]
for c, h in enumerate(headers):
    tc = create_table_cell(row=0, col=c, text=h)
    tc.is_header = True; tc.bold = True
    cells.append(tc)
for r, row_data in enumerate([
    ["ATH600", "12W", "24V", "AC/DC", "001"],
    ["ATH601", "18W", "24V", "AC", "002"],
]):
    for c, v in enumerate(row_data):
        cells.append(create_table_cell(row=r+1, col=c, text=v))
t.cells = cells
t.col_widths = ["1*", "1*", "1*", "2*", "1*"]
editor.load_table(t)
root.mainloop()
```

Run: `cd src/PDF_PDM && python tmp_preview.py`
Expected: Editor loads the Table object, displays with row 0 marked as header (HDR label + blue background).

- [ ] **Step 4: Commit**

```bash
git add src/PDF_PDM/tables/editor_v2.py
git commit -m "feat(pdf_pdm): editor_v2 load_table/to_table serialization"
```

---

### Task 21: from_validated_table classmethod

**Files:** Modify `src/PDF_PDM/tables/editor_v2.py`

- [ ] **Step 1: Add classmethod preserving T19 work**

```python
@classmethod
def from_validated_table(cls, validated, parent, temp_system=None,
                         on_apply=None, on_cancel=None) -> "CanvasTableEditor":
    """Create editor pre-populated from a detection/table_engine_v2 ValidatedTable."""
    from pdf_models import Table, create_table_cell, create_element, ElementType
    editor = cls(parent, on_apply=on_apply, on_cancel=on_cancel, temp_system=temp_system)
    if validated is None: return editor
    el = create_element(ElementType.TABLE, validated.bbox, 0)
    t = Table(element=el)
    cells = []
    for vc in validated.cells:
        tc = create_table_cell(row=vc.row, col=vc.col, text=vc.text)
        tc.rowspan = vc.rowspan; tc.colspan = vc.colspan
        tc.is_merged = (vc.rowspan > 1 or vc.colspan > 1)
        tc.is_header = vc.is_header
        tc.confidence_category = vc.confidence_category
        cells.append(tc)
    t.cells = cells
    editor.load_table(t)
    return editor
```

- [ ] **Step 2: Commit**

```bash
git add src/PDF_PDM/tables/editor_v2.py
git commit -m "feat(pdf_pdm): editor_v2 from_validated_table classmethod"
```

---

### Task 22: Integrate into app.py Table Editor tab

**Files:** Modify `src/PDF_PDM/app.py`

- [ ] **Step 1: Replace the Table Editor tab builder**

In `app.py`, locate `create_table_editor(self, parent)` (around line 578).

Replace the existing implementation with an embedded CanvasTableEditor plus the table selector dropdown kept at top:

```python
def create_table_editor(self, parent):
    """Create embedded Canvas-based table editor (v2)."""
    from tables.editor_v2 import CanvasTableEditor
    # Top strip: table selector
    select_frame = ttk.Frame(parent, padding=(10, 8))
    select_frame.pack(fill=tk.X)
    ttk.Label(select_frame, text="Table:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 6))
    self.table_selector = ttk.Combobox(select_frame, state="readonly")
    self.table_selector.pack(side=tk.LEFT, fill=tk.X, expand=True)
    self.table_selector.bind("<<ComboboxSelected>>", self.on_table_selected)

    # Embedded editor
    editor_container = ttk.Frame(parent)
    editor_container.pack(fill=tk.BOTH, expand=True)
    self.table_editor_v2 = CanvasTableEditor(
        editor_container,
        on_apply=self._on_table_editor_apply,
        on_cancel=None,
        temp_system=self,
    )

def _on_table_editor_apply(self, updated_table):
    """Callback when user clicks Apply in the editor."""
    self.selected_table._table = updated_table
    self.refresh_table_view()
    self._save_to_temp_if_open()
```

- [ ] **Step 2: Update `on_table_selected` to load into embedded editor**

```python
def on_table_selected(self, event=None):
    idx = self.table_selector.current()
    if idx < 0 or idx >= len(self._table_elements): return
    elem = self._table_elements[idx]
    self.selected_table = elem
    # Prefer ValidatedTable from v2 pipeline if present
    validated = getattr(elem, "metadata", {}).get("validated_table")
    if validated is not None:
        from tables.editor_v2 import CanvasTableEditor
        # Rebuild editor in place with validated data
        # Simpler: just load into the existing editor
        from pdf_models import Table, create_table_cell, create_element, ElementType
        el = create_element(ElementType.TABLE, validated.bbox, 0)
        t = Table(element=el)
        cells = []
        for vc in validated.cells:
            tc = create_table_cell(row=vc.row, col=vc.col, text=vc.text)
            tc.rowspan = vc.rowspan; tc.colspan = vc.colspan
            tc.is_header = vc.is_header
            tc.confidence_category = vc.confidence_category
            cells.append(tc)
        t.cells = cells
        self.table_editor_v2.load_table(t)
    elif hasattr(elem, "_table") and elem._table is not None:
        self.table_editor_v2.load_table(elem._table)
```

- [ ] **Step 3: Remove `open_interactive_table_editor` method**

Find and delete the `open_interactive_table_editor` method in `app.py` (and any button that calls it). The editor is now embedded, no popup needed.

- [ ] **Step 4: Smoke test the app**

Run: `cd src/PDF_PDM && python app.py`
Expected: Opens the app. Load a PDF, detect tables. Click the Table Editor tab. The editor is embedded. Selecting a table from the dropdown loads it into the editor in-place. No separate popup window.

- [ ] **Step 5: Commit**

```bash
git add src/PDF_PDM/app.py
git commit -m "feat(pdf_pdm): embed editor_v2 in Table Editor tab"
```

---

### Task 23: DITA exporter — emit `<title>`, `<thead>`, `<colspec>` with colnum/colwidth, `<p>` wrappers

**Files:** Modify `src/PDF_PDM/export/dita.py`
Test: Create `src/PDF_PDM/tests/test_dita_table_export.py`

- [ ] **Step 1: Write failing integration tests**

```python
# tests/test_dita_table_export.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import xml.etree.ElementTree as ET
from pdf_models import Table, DitaTableAttrs, create_table_cell, create_element, ElementType


def _build_simple_table(header=True):
    el = create_element(ElementType.TABLE, (0,0,400,200), 0)
    t = Table(element=el)
    t.dita_attrs = DitaTableAttrs(title="Specs", pgwide=True, font_class="font:10")
    t.col_widths = ["1*", "2*", "1*"]
    cells = []
    for c, h in enumerate(["Name", "Desc", "Value"]):
        tc = create_table_cell(row=0, col=c, text=h)
        tc.is_header = header; tc.bold = header
        cells.append(tc)
    for r in range(1, 3):
        for c in range(3):
            cells.append(create_table_cell(row=r, col=c, text=f"R{r}C{c}"))
    t.cells = cells
    return t


def _export_table_xml(t) -> ET.Element:
    from export.dita import DITAExporter
    exporter = DITAExporter()
    xml_str = exporter.export_table_to_xml(t)  # method to add in this task
    return ET.fromstring(xml_str)


def test_exports_title_element():
    t = _build_simple_table()
    root = _export_table_xml(t)
    titles = root.findall("title")
    assert len(titles) == 1 and titles[0].text == "Specs"


def test_exports_pgwide_and_outputclass():
    t = _build_simple_table()
    root = _export_table_xml(t)
    assert root.get("pgwide") == "1"
    assert root.get("outputclass") == "font:10"


def test_exports_colspec_with_colnum_and_colwidth():
    t = _build_simple_table()
    root = _export_table_xml(t)
    tgroup = root.find("tgroup")
    colspecs = tgroup.findall("colspec")
    assert len(colspecs) == 3
    assert colspecs[0].get("colname") == "col1"
    assert colspecs[0].get("colnum") == "1"
    assert colspecs[0].get("colwidth") == "1*"
    assert colspecs[1].get("colwidth") == "2*"


def test_exports_thead_when_header_rows_present():
    t = _build_simple_table(header=True)
    root = _export_table_xml(t)
    tgroup = root.find("tgroup")
    assert tgroup.find("thead") is not None
    assert tgroup.find("tbody") is not None
    thead_rows = tgroup.find("thead").findall("row")
    assert len(thead_rows) == 1
    body_rows = tgroup.find("tbody").findall("row")
    assert len(body_rows) == 2


def test_no_thead_when_no_header_rows():
    t = _build_simple_table(header=False)
    root = _export_table_xml(t)
    tgroup = root.find("tgroup")
    assert tgroup.find("thead") is None


def test_entry_wraps_text_in_p():
    t = _build_simple_table()
    root = _export_table_xml(t)
    entries = root.findall(".//tbody/row/entry")
    for e in entries:
        # Entry should contain a <p>, not bare text
        assert e.text is None or e.text.strip() == ""
        assert e.find("p") is not None
```

Run: `cd src/PDF_PDM && python -m pytest tests/test_dita_table_export.py -v`
Expected: FAIL — method doesn't exist yet.

- [ ] **Step 2: Inspect current `export/dita.py` table handler**

```bash
grep -n "def.*table\|<table\|<tgroup\|<colspec\|<thead\|<tbody\|<entry" src/PDF_PDM/export/dita.py | head -40
```

Identify the current method(s) that build the table XML. This is likely around lines 700-900 based on gap analysis refs.

- [ ] **Step 3: Update the exporter**

Add a clean method `export_table_to_xml(table) -> str` to `DITAExporter` that produces the correct DITA CALS structure:

```python
def export_table_to_xml(self, table) -> str:
    """Generate DITA CALS <table> XML from a Table object, respecting dita_attrs and header_rows."""
    from pdf_models import DitaTableAttrs
    attrs = getattr(table, "dita_attrs", DitaTableAttrs())
    col_widths = getattr(table, "col_widths", [])
    if not table.cells:
        return "<table/>"
    n_cols = max(c.col + c.colspan for c in table.cells)
    n_rows = max(c.row + c.rowspan for c in table.cells)
    if not col_widths or len(col_widths) != n_cols:
        col_widths = ["1*"] * n_cols

    # Root <table> attributes
    table_attrs = [f'pgwide="1"'] if attrs.pgwide else []
    table_attrs.append(f'outputclass="{attrs.font_class}"')
    table_attrs.append(f'frame="{attrs.frame}"')
    if attrs.orient == "land": table_attrs.append('orient="land"')
    colsep = "1" if attrs.colsep else "0"
    rowsep = "1" if attrs.rowsep else "0"
    table_attrs.append(f'colsep="{colsep}"'); table_attrs.append(f'rowsep="{rowsep}"')
    opening = f'<table {" ".join(table_attrs)}>'

    lines = [opening]
    # <title>
    if attrs.title:
        lines.append(f"  <title>{_xml_escape(attrs.title)}</title>")
    # <tgroup>
    lines.append(f'  <tgroup cols="{n_cols}">')
    for idx, w in enumerate(col_widths, start=1):
        lines.append(f'    <colspec colname="col{idx}" colnum="{idx}" colwidth="{w}"/>')

    # Separate header and body rows
    header_row_indices = sorted({c.row for c in table.cells if getattr(c, "is_header", False)})
    body_row_indices = sorted({r for r in range(n_rows) if r not in header_row_indices})

    def _emit_rows(row_indices, tag):
        if not row_indices: return
        lines.append(f"    <{tag}>")
        for r in row_indices:
            row_cells = sorted([c for c in table.cells if c.row == r], key=lambda c: c.col)
            lines.append("      <row>")
            for cell in row_cells:
                entry_attrs = []
                if cell.colspan > 1:
                    entry_attrs.append(f'namest="col{cell.col+1}"')
                    entry_attrs.append(f'nameend="col{cell.col+cell.colspan}"')
                if cell.rowspan > 1:
                    entry_attrs.append(f'morerows="{cell.rowspan-1}"')
                rotate = getattr(cell, "rotate", 0)
                if rotate: entry_attrs.append(f'rotate="{rotate}"')
                attr_str = (" " + " ".join(entry_attrs)) if entry_attrs else ""
                txt = _xml_escape(cell.text or "")
                # Apply inline B/I formatting
                if getattr(cell, "bold", False): txt = f"<b>{txt}</b>"
                if getattr(cell, "italic", False): txt = f"<i>{txt}</i>"
                # Rotated cells do NOT wrap in <p>
                if rotate:
                    lines.append(f"        <entry{attr_str}>{txt}</entry>")
                else:
                    lines.append(f"        <entry{attr_str}><p>{txt}</p></entry>")
            lines.append("      </row>")
        lines.append(f"    </{tag}>")

    _emit_rows(header_row_indices, "thead")
    _emit_rows(body_row_indices, "tbody")
    lines.append("  </tgroup>")
    lines.append("</table>")
    return "\n".join(lines)


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))
```

Wire this method into the existing export pipeline. Find where the current exporter writes tables and replace the call with `self.export_table_to_xml(table)`.

- [ ] **Step 4: Run tests — all should pass**

Run: `cd src/PDF_PDM && python -m pytest tests/test_dita_table_export.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/PDF_PDM/export/dita.py src/PDF_PDM/tests/test_dita_table_export.py
git commit -m "feat(pdf_pdm): DITA exporter emits title/thead/colspec with p-wrapping"
```

---

### Task 24: Delete old editor.py and migrate imports

**Files:**
- Modify: `src/PDF_PDM/app.py`
- Delete: `src/PDF_PDM/tables/editor.py`

- [ ] **Step 1: Find all remaining imports of the old editor**

```bash
grep -rn "from tables.editor import\|from tables\\.editor " src/PDF_PDM/
```

- [ ] **Step 2: Replace each with editor_v2**

For every match, swap to `from tables.editor_v2 import CanvasTableEditor` (or delete if the imported names no longer exist). Old `InteractiveTableEditor`, `CellFormat`, `TableEditorHistory` should no longer be referenced.

- [ ] **Step 3: Delete the old file**

```bash
git rm src/PDF_PDM/tables/editor.py
```

- [ ] **Step 4: Verify app still imports cleanly**

```bash
cd src/PDF_PDM && python -c "import app; print('ok')"
```
Expected: `ok`

Run smoke: `cd src/PDF_PDM && python app.py`
Expected: App launches, Table Editor tab works with new editor.

- [ ] **Step 5: Remove the temporary preview script**

```bash
rm src/PDF_PDM/tmp_preview.py
```

- [ ] **Step 6: Run the full test suite**

```bash
cd src/PDF_PDM && python -m pytest tests/ -v
```
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/PDF_PDM/app.py src/PDF_PDM/tables/
git commit -m "refactor(pdf_pdm): remove legacy editor.py, migrate to editor_v2"
```

---

## End-of-plan checklist

- [ ] All 24 tasks completed and committed
- [ ] `pytest tests/` passes
- [ ] `python app.py` launches without errors
- [ ] Can load a PDF, detect tables, select a table, mark a row as header, and see blue highlight + HDR label
- [ ] Can edit cells, merge, insert rows, and export DITA with correct `<thead>`, `<title>`, `<colspec colnum/colwidth>`, `<p>`-wrapped entries
- [ ] Window resizes responsively
- [ ] No Toplevel popup for the editor
- [ ] Old `tables/editor.py` removed

## Known follow-ups (not in this plan)

- Copy/Paste (Ctrl+C/V/X) — deferred
- Right-click context menus — deferred
- Save/Apply/Cancel buttons — the editor applies changes via `on_apply` callback; the app.py integration may need explicit save buttons added depending on UX preference
- Cross-page table consolidation — lives in export layer, not editor
- `<simpletable>` export option — gap M11
- `rowheader` attribute — deferred
- Full rotated-text rendering on canvas — currently shown as ↻N badge
