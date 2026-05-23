# Table Editor Rebuild — Design Spec

**Date:** 2026-04-05
**Scope:** `PDF_PDM/tables/editor.py` (2,746 lines) — full rewrite
**Downstream impact:** `PDF_PDM/export/dita.py` (consume new attributes)

---

## 1. Goal

Rebuild the PDF_PDM table editor as an **embedded, Canvas-based, DITA-aware** editor with first-class **header row designation**, responsive layout, and an Excel-style selection-then-act interaction model. The rebuild removes 2,746 lines of widget-per-cell rendering and replaces them with a single `tk.Canvas` custom-drawn grid embedded directly in the Table Editor tab.

## 2. Current State & Problems

**Rendering:** The editor uses one `tk.Text` widget per cell. A 10×10 table allocates 100 widgets. Rows are **5 pixels tall by default** (nonsensical). Columns are 150px fixed.

**Window:** Opens in a `Toplevel` window at 1000×700 (fixed), not responsive.

**Controls:** Row/column selection is done by typing a number into a spinbox or picking a letter from a combobox — not by clicking the headers.

**DITA gaps (per `Ref_dita/GAP_ANALYSIS_Export_vs_Reference.md`):**
- **P10** — no `<thead>` in CALS tables → headers don't repeat on page breaks
- **P5** — `<entry>` contains bare text, missing `<p>` wrapping
- **P9** — `<colspec>` missing `colnum` attribute
- No `<title>` (caption) generation
- No `pgwide`, `outputclass="font:10"`, `orient`, `frame` control from UI
- No column-width ratio (`colwidth="1*"`) editing
- No `rotate` attribute on header entries

**Hidden feature debt:** "Split Cell" is confusing (vs. "Unmerge"), "Reset Size 150×5" is a nonsense default, formatting (align/bold/italic) is per-cell but DITA CALS expects alignment at `<colspec>` level.

## 3. Design Decisions

### 3.1 Rendering: single Canvas, custom draw
One `tk.Canvas` draws the entire grid as rectangles + text. Selection highlights, header row tint, low-confidence dashed borders, merged-cell shading are all drawn onto the canvas. Cell editing spawns a floating `tk.Entry` overlay, committed on Enter, destroyed on Esc.

**Why:** Scales to hundreds of cells. Full control over visuals (fill-handle, dashed borders, gradient-free but colored backgrounds). Responsive layout trivial.

### 3.2 Embedded in the tab (no Toplevel)
The editor lives directly inside the existing "Table Editor" tab of `app.py`. Resizes with the main window via `pack(fill=BOTH, expand=True)`.

### 3.3 Excel-style selection-then-act
User clicks a **row number** / **column letter** / **cell** to select. Shift-click extends range. Ctrl-click adds to multi-selection. Toolbar actions (Insert, Delete, Merge, Mark Header) act on current selection. No spinbox or combobox anywhere.

### 3.4 DITA-aware strip
A thin green-pilled strip above the toolbar exposes the 5 table-level DITA attributes that matter: **Title**, **Full-width (pgwide)**, **Font size**, **Orient**, **Borders (frame)**. Every other DITA attribute uses a sensible default.

### 3.5 Validation dot
Status bar shows a live green/yellow/red dot indicating DITA export readiness. Clicking `Validate` opens a small panel listing each issue (empty header, missing colwidth, rotated cell with `<p>`, etc.).

### 3.6 Shortcuts surfaced separately
Keyboard shortcuts are **not** shown in the main window. A `? Shortcuts` button in the title bar opens a small popup. Right-click context menus expose the same operations inline.

### 3.7 Tkinter constraints accepted
Sharp corners (no rounded). No CSS box-shadows. System fonts (Segoe UI → SF → DejaVu fallback chain). All buttons styled via `ttk.Style`.

## 4. UI Components (top-to-bottom)

```
┌─────────────────────────────────────────────────────────────┐
│ Title bar: "Table Editor" · meta · [selector] [? Shortcuts] │
├─────────────────────────────────────────────────────────────┤
│ DITA strip: [Title] [☐Full width] [Font▾] [Orient▾] [Frame▾]│
├─────────────────────────────────────────────────────────────┤
│ Toolbar: Undo Redo | Mark Header Clear Header | Merge Split │
│   Rotate | Insert Row▾ Insert Col▾ Delete | B I Align▾ |    │
│   Validate                                                  │
├─────────────────────────────────────────────────────────────┤
│ Grid (tk.Canvas, expand=True, scrollable both axes)         │
│                                                             │
│   ╔══╤═══════╤═══════╤═══════╗                              │
│   ║  │ A 1*  │ B 1*  │ C 2*  ║  ← column letters + colspec  │
│   ╠══╪═══════╪═══════╪═══════╣                              │
│   ║HDR│Model │Power  │Voltage║  ← header row (green HDR)    │
│   ╠══╪═══════╪═══════╪═══════╣  ← green divider             │
│   ║ 2 │ATH600│12W    │24V    ║                              │
│   ║ 3 │ATH601│18W    │24V    ║  ← low-conf cell = dashed    │
│   ╚══╧═══════╧═══════╧═══════╝                              │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│ Status: Row 3 selected · 5×5 · Headers: 1 · ●DITA ready    │
└─────────────────────────────────────────────────────────────┘
```

## 5. Feature Inventory

### 5.1 Kept from current editor
| Current | Notes |
|---|---|
| Add / Delete Row | Kept (reworked as `Insert Row▾` with Above/Below) |
| Add / Delete Column | Kept (reworked as `Insert Col▾` with Left/Right) |
| Merge Cells | Kept (rowspan/colspan → `namest/nameend/morerows`) |
| Unmerge Cell | Kept (renamed from "Unmerge") |
| Split Cell | **Dropped** (ambiguous vs. Unmerge) |
| Clear Content | Kept (now the `Delete` key) |
| Reset Size (150×5) | **Dropped** (responsive = no broken sizes) |
| Align L/C/R | Kept as `Align▾` dropdown |
| Bold / Italic | Kept (B / I toggles → inline `<b>`/`<i>` on export) |
| Undo / Redo | Kept (extend existing `TableEditorHistory`) |
| Save / Apply & Close / Cancel | Kept (bottom of editor, outside canvas) |
| Row spinbox / Col combobox | **Dropped** (click-to-select instead) |

### 5.2 New features
| Feature | Purpose | DITA mapping |
|---|---|---|
| **Mark Header Row** (`H` key, primary button) | Designate row(s) as header | Wraps rows in `<thead>` |
| **Clear Header** | Unmark header rows | Moves rows back to `<tbody>` |
| **Title input** in DITA strip | Table caption | `<title>` inside `<table>` |
| **Full-width checkbox** | Expand to page margins | `pgwide="1"` |
| **Font dropdown** (8/10/12pt) | Table font size | `outputclass="font:10"` |
| **Orient dropdown** (Portrait/Landscape) | Orientation | `orient="port"/"land"` |
| **Frame dropdown** (All/Top/Sides/None) | Border style | `frame="all"/"top"/"sides"/"none"` |
| **Column colspec editor** (drag edge) | Proportional widths | `<colspec colwidth="1*">` |
| **Rotate** (R key) | Header cell rotation | `rotate="1"/"2"/"3"` |
| **Validate** button | DITA readiness check | (validation rules below) |
| **? Shortcuts** popup | Keyboard reference | User-facing help |

### 5.3 Interaction model
| Action | Method |
|---|---|
| Select cell | Click cell |
| Select row | Click row number |
| Select column | Click column letter |
| Extend selection | Shift+click |
| Multi-select | Ctrl+click |
| Edit cell | Double-click OR F2 |
| Commit edit | Enter OR click away |
| Cancel edit | Esc |
| Navigate | Arrow keys |
| Insert row above | `Insert Row▾` → Above (OR Ctrl+Plus) |
| Insert row below | `Insert Row▾` → Below (OR Ctrl+Shift+Plus) |
| Mark row as header | Select row + click Mark Header (OR H key) |

## 6. Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| ↑ ↓ ← → | Navigate cells |
| Tab / Shift+Tab | Move right / left |
| Enter | Move down |
| F2 | Edit current cell |
| Esc | Cancel edit |
| Delete | Clear content |
| H | Toggle header row |
| R | Rotate cell (cycle 0°→90°→180°→270°→0°) |
| Ctrl+M | Merge selection |
| Ctrl+Shift+M | Unmerge |
| Ctrl+Z / Ctrl+Y | Undo / Redo |
| Ctrl+C / V / X | Copy / Paste / Cut |
| Ctrl+A | Select all |
| Ctrl+B / Ctrl+I | Bold / Italic |
| Ctrl+L / E / R | Align left / center / right |
| Ctrl+Plus | Insert row above |
| Ctrl+Shift+Plus | Insert row below |
| Ctrl+Shift+V | Validate |

## 7. DITA Export Mapping

Editor state → DITA XML produced by `export/dita.py`:

```python
# Editor state
title: str                         # → <title>{title}</title>
pgwide: bool                       # → pgwide="1" if True
font_class: str ("font:10")        # → outputclass="font:10"
orient: str ("port"/"land")        # → orient="port"
frame: str ("all"/"top"/...)       # → frame="all"
colsep: bool (default True)        # → colsep="1"
rowsep: bool (default True)        # → rowsep="1"
header_rows: Set[int]              # → rows wrapped in <thead>
col_widths: List[str] ("1*"/"2*")  # → <colspec colwidth="1*">
cells[r,c].rotate: int (0/1/2/3)   # → rotate="1" on <entry>
cells[r,c].bold, italic            # → <b>/<i> inside <p>
cells[r,c].rowspan, colspan        # → morerows, namest/nameend
```

**Export rules:**
- Every `<entry>` wraps text in `<p>` **unless** `rotate != 0` (per DITA rules)
- At least one `<colspec>` MUST have proportional width (`colwidth="N*"`)
- `<thead>` emitted only if `len(header_rows) > 0`
- `<colspec colnum="N" colname="colN">` for each column (fixes P9)
- Column names `col1`, `col2`, ... used in `namest`/`nameend` spans

## 8. Validation Rules

Shown in the `Validate` panel; aggregated into the status-bar dot:

| Rule | Severity | Fix hint |
|---|---|---|
| No header row marked, but row 1 is visually header-like (bold, all text) | warning | "Mark row 1 as header?" |
| Header row has empty cells | warning | "Fill all header cells or remove column" |
| Column has no `colwidth` set (all equal) | info | auto-applied `1*` |
| No column has proportional width | error | At least one must be `*` |
| Cell with `rotate != 0` contains multi-paragraph text | error | "Rotated cells cannot have `<p>` — single line only" |
| Merged cell crosses header/body boundary | error | "Cannot merge header + body rows" |
| Table has 0 rows or 0 cols | error | "Empty table" |
| Title is empty | info | (optional) |

**Status-bar dot:**
- Green = no errors, no warnings
- Yellow = no errors, ≥1 warning
- Red = ≥1 error

## 9. Data Model

### 9.1 Editor state (new dataclass)
```python
@dataclass
class DitaTableAttrs:
    title: str = ""
    pgwide: bool = True
    font_class: str = "font:10"
    orient: str = "port"       # "port" or "land"
    frame: str = "all"         # "all" | "top" | "sides" | "none"
    colsep: bool = True
    rowsep: bool = True

@dataclass
class EditorGrid:
    rows: int
    cols: int
    cells: List[List[CellData]]          # [row][col] — 2D
    header_rows: Set[int]                # row indices in <thead>
    col_widths: List[str]                # e.g. ["1*", "2*", "1*"]
    merges: Dict[Tuple[int,int], Tuple[int,int]]  # (r,c) -> (rowspan, colspan)
    attrs: DitaTableAttrs

@dataclass
class CellData:
    text: str = ""
    align: str = "left"                  # "left" | "center" | "right"
    bold: bool = False
    italic: bool = False
    rotate: int = 0                      # 0 | 1 | 2 | 3
    confidence_category: str = "high"    # from existing pipeline
```

### 9.2 Selection
```python
@dataclass
class Selection:
    kind: str                            # "cell" | "row" | "col" | "range" | "multi"
    anchor: Tuple[int, int]              # for shift-extend
    cells: Set[Tuple[int, int]]          # materialized set
```

### 9.3 Persistence (existing `Table`/`TableCell` from `pdf_models.py`)
Loaded and saved via `EditorGrid.from_table(table)` / `.to_table() -> Table`.
Existing `TableCell.rowspan/colspan/is_merged` model preserved. New fields added to `TableCell`:
- `is_header: bool = False`
- `rotate: int = 0`
- `align: str = "left"`
- `bold: bool = False`
- `italic: bool = False`

`Table` gains new attributes:
- `dita_attrs: DitaTableAttrs`
- `col_widths: List[str]`

## 10. Integration Points

1. **app.py `create_table_editor(parent)`** — instead of building the old Combobox/Button layout + launching a Toplevel, builds the embedded editor directly in `parent`.
2. **app.py `on_table_selected(event)`** — calls `editor.load_table(table_obj)` instead of `open_interactive_table_editor()`.
3. **Old `open_interactive_table_editor` method** — deleted.
4. **`InteractiveTableEditor.from_validated_table(vt)`** — classmethod on the new editor preserved from T19 work.
5. **`export/dita.py`** — consumes new fields (`dita_attrs`, `col_widths`, per-cell `is_header`/`rotate`/`align`/`bold`/`italic`); emits `<title>`, `<thead>`, correct `<colspec>` with `colnum`/`colwidth`, `<p>` wrappers on `<entry>`.

## 11. Out of Scope

- **Cross-page table consolidation** (merge multi-page tables sharing headers) — handled in DITA export layer later, not in editor.
- **`<simpletable>` generation** (Gap M11) — separate export-mode decision.
- **`rowheader` attribute** (leftmost column as header) — can add later if needed.
- **Per-cell `rowsep`/`colsep`** — defaults to `1` globally, almost never overridden.
- **Find & Replace** — use Ctrl+F later if demanded.
- **Formula support** — never (this isn't a spreadsheet).
- **Collaborative editing** — N/A.
- **PDF preview sidebar** — dropped by user decision.
- **Rounded corners / box shadows** — tkinter limitations accepted.

## 12. Acceptance Criteria

The rebuild is done when:
1. Table Editor tab loads without opening a Toplevel window.
2. Clicking a row number selects the row and highlights it green.
3. Clicking `Insert Row▾` → `Insert Above` with row 3 selected inserts a new row at position 3.
4. Pressing `H` on a selected row toggles it into `<thead>` — visual turns blue + HDR label.
5. Typing into the DITA strip updates the underlying `DitaTableAttrs`.
6. Dragging a column edge updates its colspec ratio.
7. Clicking `Validate` with an empty header cell shows a warning in the panel.
8. Exporting a table with 1 header row and title "Specs" produces:
   ```xml
   <table pgwide="1" outputclass="font:10" frame="all">
     <title>Specs</title>
     <tgroup cols="3">
       <colspec colname="col1" colnum="1" colwidth="1*"/>
       ...
       <thead><row><entry><p>Hdr1</p></entry>...</row></thead>
       <tbody><row><entry><p>Cell</p></entry>...</row></tbody>
     </tgroup>
   </table>
   ```
9. All cell text is wrapped in `<p>` (fixes P5).
10. Window resizes responsively — no fixed 1000×700.
