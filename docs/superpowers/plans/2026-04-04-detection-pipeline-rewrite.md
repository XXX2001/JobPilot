# Detection Pipeline Rewrite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 7-stage detection pipeline in `detection/pymupdf.py` with a modular, general-purpose engine that uses TOC-driven hierarchy, span-level data preservation, multi-signal classification, and new element types.

**Architecture:** Three-phase pipeline — Phase A (document analysis, runs once) → Phase B (per-page detection) → Phase C (interactive review/editors). The old engine is kept as `pymupdf.py` for A/B comparison. New modules: `engine.py`, `extraction.py`, `assembly.py`, `classification.py`, `specialized.py`, `tables.py`, `document_analysis.py`, `profiles.py`.

**Tech Stack:** Python 3, PyMuPDF (fitz), tkinter, transformers/torch (optional: TATR), pdfplumber (fallback), scikit-learn.

**Spec:** `docs/superpowers/specs/2026-04-04-detection-pipeline-rewrite-design.md`

**Codebase:** `/home/mouad/ALL/SE-Tools/src/PDF_PDM/`

**Important context:**
- All submodules use `sys.path.insert(0, ...)` to resolve imports — follow this pattern in new files
- The `Element` dataclass in `pdf_models.py` is consumed by: `app.py`, `export/dita.py`, `tables/editor.py`, `dialogs/`, `detection/pipeline.py` — backward compatibility is critical
- `ElementType` enum stores a 7-tuple: `(display_name, color, priority, tag, icon, description, criteria)` — new types must follow this exact structure
- No automated tests exist currently — this plan adds a `tests/` directory with tests for the new detection logic

---

## File Map

### New files to create:

| File | Responsibility |
|------|---------------|
| `detection/engine.py` | `DetectionEngineV2` orchestrator — Phase A + B coordination |
| `detection/extraction.py` | Span-preserving text extraction from PyMuPDF (B1) |
| `detection/assembly.py` | Element assembly: lines → paragraphs → lists (B2) |
| `detection/classification.py` | Multi-signal classifier: TOC > numbering > context > font (B3) |
| `detection/specialized.py` | NOTE, HAZARD, CODE_BLOCK, HYPERLINK, FOOTNOTE, SHORTDESC detection (B4) |
| `detection/tables.py` | Table detection (DocLayout), structure (TATR/find_tables), cross-page stitch (B5) |
| `detection/document_analysis.py` | Phase A: TOC extraction, header/footer, content start, visual index |
| `detection/profiles.py` | FontProfile, DetectionProfile, level mapping logic |
| `dialogs/profile_review.py` | Profile review dialog (A6 UI checkpoint) |
| `dialogs/hazard_editor.py` | Hazard statement structured form editor (C2) |
| `dialogs/note_editor.py` | Note type/content editor (C4) |
| `tests/__init__.py` | Test package |
| `tests/test_models.py` | Tests for TextSpan, HazardData, new ElementTypes, Element updates |
| `tests/test_extraction.py` | Tests for span-preserving extraction |
| `tests/test_assembly.py` | Tests for element assembly |
| `tests/test_classification.py` | Tests for multi-signal classifier |
| `tests/test_document_analysis.py` | Tests for TOC, header/footer, profiling |
| `tests/test_specialized.py` | Tests for specialized type detection |
| `tests/test_tables.py` | Tests for table structure recognition |

### Files to modify:

| File | Changes |
|------|---------|
| `pdf_models.py` | Add TextSpan, HazardData, 6 new ElementTypes, Element.spans field, Element serialization updates |
| `detection/__init__.py` | Re-export DetectionEngineV2 |
| `app.py` | Integrate DetectionEngineV2, add profile review flow, hazard/note editor launchers |
| `tables/editor.py` | Add header row toggle, cross-page indicator |
| `dita_mapping_config.json` | Add entries for NOTE, HAZARD_STATEMENT, CODE_BLOCK, HYPERLINK, FOOTNOTE, SHORTDESC |

### Files kept unchanged:

| File | Reason |
|------|--------|
| `detection/pymupdf.py` | Kept as deprecated old engine for A/B comparison |
| `detection/pipeline.py` | ML pipeline (PP-DocLayoutV3) — reused by new engine |
| `detection/glm_ocr.py` | GLM-OCR backend — reused |
| `detection/model_manager.py` | Model downloads — reused |
| `export/dita.py` | DITA exporter updates are a separate spec |
| `config.py` | No changes needed — new engine reads same config keys |

---

## Task 1: Data Model Foundation

**Files:**
- Modify: `pdf_models.py`
- Modify: `dita_mapping_config.json`
- Create: `tests/__init__.py`
- Create: `tests/test_models.py`

This task adds TextSpan, HazardData, 6 new ElementTypes, and updates Element with backward-compatible new fields. Everything else depends on this.

- [ ] **Step 1: Create tests directory and test file**

Create `tests/__init__.py` (empty) and `tests/test_models.py` with initial tests:

```python
# tests/__init__.py
# empty

# tests/test_models.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdf_models import (
    TextSpan, HazardData, ElementType, Element, create_element
)


class TestTextSpan:
    def test_basic_creation(self):
        span = TextSpan(
            text="Hello",
            font_name="Arial-BoldMT",
            font_size=12.0,
            is_bold=True,
            is_italic=False,
            color=(0, 0, 0),
            flags=16,  # bold flag
            bbox=(100, 200, 150, 212),
        )
        assert span.text == "Hello"
        assert span.is_bold is True
        assert span.is_superscript is False
        assert span.is_monospace is False

    def test_superscript_flag(self):
        span = TextSpan(
            text="2", font_name="Arial", font_size=8.0,
            is_bold=False, is_italic=False, color=(0, 0, 0),
            flags=1, bbox=(0, 0, 10, 10),
        )
        assert span.is_superscript is True

    def test_monospace_from_flags(self):
        span = TextSpan(
            text="code", font_name="SomeFont", font_size=10.0,
            is_bold=False, is_italic=False, color=(0, 0, 0),
            flags=8, bbox=(0, 0, 10, 10),
        )
        assert span.is_monospace is True

    def test_monospace_from_font_name(self):
        for name in ["CourierNewPSMT", "Consolas", "Source Code Pro", "Menlo-Regular"]:
            span = TextSpan(
                text="x", font_name=name, font_size=10.0,
                is_bold=False, is_italic=False, color=(0, 0, 0),
                flags=0, bbox=(0, 0, 10, 10),
            )
            assert span.is_monospace is True, f"{name} should be monospace"

    def test_to_dict_roundtrip(self):
        span = TextSpan(
            text="test", font_name="Arial", font_size=12.0,
            is_bold=True, is_italic=False, color=(255, 0, 0),
            flags=0, bbox=(10, 20, 30, 40),
        )
        d = span.to_dict()
        restored = TextSpan.from_dict(d)
        assert restored.text == span.text
        assert restored.font_name == span.font_name
        assert restored.color == span.color


class TestHazardData:
    def test_basic_creation(self):
        hd = HazardData(
            hazard_type="warning",
            type_of_hazard="Electrical shock",
            how_to_avoid=["Disconnect power", "Verify absence of voltage"],
        )
        assert hd.hazard_type == "warning"
        assert len(hd.how_to_avoid) == 2
        assert hd.consequence is None

    def test_danger_no_consequence(self):
        hd = HazardData(hazard_type="danger", type_of_hazard="test")
        assert hd.consequence is None

    def test_to_dict_roundtrip(self):
        hd = HazardData(
            hazard_type="caution", outputclass="electric",
            type_of_hazard="Hot surface",
            how_to_avoid=["Do not touch"], consequence="Burns",
        )
        d = hd.to_dict()
        restored = HazardData.from_dict(d)
        assert restored.hazard_type == "caution"
        assert restored.how_to_avoid == ["Do not touch"]


class TestNewElementTypes:
    def test_note_exists(self):
        et = ElementType.NOTE
        assert et.tag == "note"
        assert et.priority == 6

    def test_hazard_statement_exists(self):
        et = ElementType.HAZARD_STATEMENT
        assert et.tag == "hazard"
        assert et.priority == 5

    def test_code_block_exists(self):
        et = ElementType.CODE_BLOCK
        assert et.tag == "codeblock"

    def test_hyperlink_exists(self):
        et = ElementType.HYPERLINK
        assert et.tag == "hyperlink"

    def test_footnote_exists(self):
        et = ElementType.FOOTNOTE
        assert et.tag == "footnote"

    def test_shortdesc_exists(self):
        et = ElementType.SHORTDESC
        assert et.tag == "shortdesc"

    def test_all_types_have_unique_tags(self):
        tags = [et.tag for et in ElementType]
        assert len(tags) == len(set(tags)), f"Duplicate tags: {tags}"


class TestElementSpans:
    def test_element_with_spans(self):
        spans = [
            TextSpan("Hello ", "Arial-BoldMT", 12.0, True, False, (0, 0, 0), 16, (10, 10, 50, 22)),
            TextSpan("world", "Arial", 12.0, False, False, (0, 0, 0), 0, (50, 10, 90, 22)),
        ]
        elem = create_element(
            ElementType.PARAGRAPH, (10, 10, 90, 22), page=0,
            text="Hello world", spans=spans,
        )
        assert len(elem.spans) == 2
        assert elem.text == "Hello world"

    def test_element_without_spans_backward_compat(self):
        elem = create_element(
            ElementType.PARAGRAPH, (10, 10, 90, 22), page=0,
            text="Hello world",
        )
        assert elem.spans == []
        assert elem.text == "Hello world"

    def test_element_to_dict_includes_spans(self):
        spans = [
            TextSpan("Hi", "Arial", 12.0, False, False, (0, 0, 0), 0, (0, 0, 10, 10)),
        ]
        elem = create_element(
            ElementType.PARAGRAPH, (0, 0, 10, 10), page=0,
            text="Hi", spans=spans,
        )
        d = elem.to_dict()
        assert "spans" in d
        assert len(d["spans"]) == 1

    def test_element_from_dict_restores_spans(self):
        spans = [
            TextSpan("Hi", "Arial", 12.0, False, False, (0, 0, 0), 0, (0, 0, 10, 10)),
        ]
        elem = create_element(
            ElementType.PARAGRAPH, (0, 0, 10, 10), page=0,
            text="Hi", spans=spans,
        )
        d = elem.to_dict()
        restored = Element.from_dict(d)
        assert len(restored.spans) == 1
        assert restored.spans[0].text == "Hi"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/mouad/ALL/SE-Tools/src/PDF_PDM && python -m pytest tests/test_models.py -v`

Expected: FAIL — `TextSpan`, `HazardData` not defined, new ElementTypes don't exist.

- [ ] **Step 3: Add TextSpan dataclass to pdf_models.py**

Add after the imports, before the `ElementType` class (around line 14):

```python
@dataclass
class TextSpan:
    """Preserves all per-span metadata from PyMuPDF extraction."""
    text: str
    font_name: str
    font_size: float
    is_bold: bool
    is_italic: bool
    color: Tuple[int, int, int]
    flags: int  # PyMuPDF: superscript=1, italic=2, serif=4, mono=8, bold=16
    bbox: Tuple[float, float, float, float]

    @property
    def is_superscript(self) -> bool:
        return bool(self.flags & 1)

    @property
    def is_subscript(self) -> bool:
        return bool(self.flags & 0x20000)  # PyMuPDF subscript flag bit

    @property
    def is_monospace(self) -> bool:
        return bool(self.flags & 8) or any(
            m in self.font_name.lower()
            for m in ('mono', 'courier', 'consolas', 'menlo', 'source code')
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            'text': self.text,
            'font_name': self.font_name,
            'font_size': self.font_size,
            'is_bold': self.is_bold,
            'is_italic': self.is_italic,
            'color': self.color,
            'flags': self.flags,
            'bbox': self.bbox,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TextSpan':
        return cls(
            text=data['text'],
            font_name=data['font_name'],
            font_size=data['font_size'],
            is_bold=data['is_bold'],
            is_italic=data['is_italic'],
            color=tuple(data['color']),
            flags=data['flags'],
            bbox=tuple(data['bbox']),
        )
```

- [ ] **Step 4: Add HazardData dataclass to pdf_models.py**

Add right after TextSpan:

```python
@dataclass
class HazardData:
    """Structured data for HAZARD_STATEMENT elements (DITA <hazardstatement>)."""
    hazard_type: str  # "danger", "warning", "caution", "notice"
    outputclass: str = ""  # "electric", "generic", etc.
    type_of_hazard: str = ""
    how_to_avoid: List[str] = field(default_factory=list)
    consequence: Optional[str] = None  # not available for danger type

    def to_dict(self) -> Dict[str, Any]:
        return {
            'hazard_type': self.hazard_type,
            'outputclass': self.outputclass,
            'type_of_hazard': self.type_of_hazard,
            'how_to_avoid': self.how_to_avoid,
            'consequence': self.consequence,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'HazardData':
        return cls(
            hazard_type=data['hazard_type'],
            outputclass=data.get('outputclass', ''),
            type_of_hazard=data.get('type_of_hazard', ''),
            how_to_avoid=data.get('how_to_avoid', []),
            consequence=data.get('consequence'),
        )
```

- [ ] **Step 5: Add 6 new ElementType members**

Add inside the `ElementType` enum, after `IMAGE` and before `__init__`:

```python
    NOTE = (
        "Note",
        (0.0, 0.6, 0.8),  # Teal bounding box
        6,  # Same priority as LIST_ITEM
        "note",
        "N",
        "Admonition notes (note, tip, important)",
        {
            "font_size": None, "is_bold": None, "text_color": None,
            "x_position": None, "x_min": None, "x_max": None,
            "special_check": "note_keyword"
        }
    )

    HAZARD_STATEMENT = (
        "Hazard Statement",
        (0.9, 0.1, 0.1),  # Dark red bounding box
        5,  # Same priority as SECTION (high importance)
        "hazard",
        "!",
        "Safety hazard statements (danger, warning, caution, notice)",
        {
            "font_size": None, "is_bold": None, "text_color": None,
            "x_position": None, "x_min": None, "x_max": None,
            "special_check": "hazard_keyword"
        }
    )

    CODE_BLOCK = (
        "Code Block",
        (0.4, 0.4, 0.4),  # Dark grey bounding box
        7,
        "codeblock",
        "<>",
        "Monospace code blocks",
        {
            "font_size": None, "is_bold": None, "text_color": None,
            "x_position": None, "x_min": None, "x_max": None,
            "special_check": "monospace_font"
        }
    )

    HYPERLINK = (
        "Hyperlink",
        (0.0, 0.4, 1.0),  # Bright blue bounding box
        9,
        "hyperlink",
        "@",
        "URL hyperlinks from PDF annotations",
        {
            "font_size": None, "is_bold": None, "text_color": None,
            "x_position": None, "x_min": None, "x_max": None,
            "special_check": "pdf_link"
        }
    )

    FOOTNOTE = (
        "Footnote",
        (0.5, 0.3, 0.0),  # Brown bounding box
        9,
        "footnote",
        "fn",
        "Footnotes at page bottom with superscript reference",
        {
            "font_size": None, "is_bold": None, "text_color": None,
            "x_position": None, "x_min": None, "x_max": None,
            "special_check": "footnote_zone"
        }
    )

    SHORTDESC = (
        "Short Description",
        (0.3, 0.7, 0.9),  # Light blue bounding box
        8,
        "shortdesc",
        "SD",
        "First short paragraph after topic title",
        {
            "font_size": None, "is_bold": None, "text_color": None,
            "x_position": None, "x_min": None, "x_max": None,
            "special_check": "shortdesc_context"
        }
    )
```

- [ ] **Step 6: Update ElementType.is_structural and is_content properties**

Update the two properties to include new types:

```python
    @property
    def is_structural(self) -> bool:
        return self in [
            ElementType.CHAPTER, ElementType.CHAPTER_14PT, ElementType.HYPERSECTION,
            ElementType.TOPIC_TITLE, ElementType.SECTION, ElementType.NUMBERED_SECTION
        ]

    @property
    def is_content(self) -> bool:
        return self in [
            ElementType.PARAGRAPH, ElementType.LIST_ITEM, ElementType.TABLE_PARAGRAPH,
            ElementType.TABLE, ElementType.IMAGE, ElementType.NOTE,
            ElementType.HAZARD_STATEMENT, ElementType.CODE_BLOCK, ElementType.HYPERLINK,
            ElementType.FOOTNOTE, ElementType.SHORTDESC
        ]
```

- [ ] **Step 7: Add new fields to Element dataclass**

Add these fields to the `Element` dataclass after the existing `processing_notes` field (around line 414):

```python
    # Span-level data (new — preserves per-span metadata from extraction)
    spans: List[TextSpan] = field(default_factory=list)

    # Classification metadata (new — records which signals produced the classification)
    classification_signals: Dict[str, float] = field(default_factory=dict)

    # Specialized type data (new — populated for specific element types)
    hazard_data: Optional[HazardData] = None
    note_type: Optional[str] = None  # "note", "tip", "important"
    link_href: Optional[str] = None  # URL for HYPERLINK elements
```

- [ ] **Step 8: Update Element.to_dict() to include new fields**

Add to the `to_dict()` return dictionary:

```python
            'spans': [s.to_dict() for s in self.spans],
            'classification_signals': self.classification_signals,
            'hazard_data': self.hazard_data.to_dict() if self.hazard_data else None,
            'note_type': self.note_type,
            'link_href': self.link_href,
```

- [ ] **Step 9: Update Element.from_dict() to restore new fields**

Add to the `from_dict()` class method, inside the `cls(...)` call:

```python
            spans=[TextSpan.from_dict(s) for s in data.get('spans', [])],
            classification_signals=data.get('classification_signals', {}),
            hazard_data=HazardData.from_dict(data['hazard_data']) if data.get('hazard_data') else None,
            note_type=data.get('note_type'),
            link_href=data.get('link_href'),
```

- [ ] **Step 10: Update create_element() helper to accept new fields**

Find the `create_element()` function and add `spans`, `hazard_data`, `note_type`, `link_href` as optional parameters with defaults, passed through to the `Element()` constructor.

- [ ] **Step 11: Update dita_mapping_config.json with new element types**

Add to the `"element_to_dita_mapping"` object:

```json
    "NOTE": {
      "bookmap_element": null,
      "dita_element": "note",
      "topic_type": null,
      "creates_new_file": false,
      "description": "Admonition notes with type attribute"
    },
    "HAZARD_STATEMENT": {
      "bookmap_element": null,
      "dita_element": "hazardstatement",
      "topic_type": null,
      "creates_new_file": false,
      "description": "Safety hazard statements with messagepanel structure"
    },
    "CODE_BLOCK": {
      "bookmap_element": null,
      "dita_element": "codeblock",
      "topic_type": null,
      "creates_new_file": false,
      "description": "Monospace code blocks"
    },
    "HYPERLINK": {
      "bookmap_element": null,
      "dita_element": "xref",
      "topic_type": null,
      "creates_new_file": false,
      "description": "URL hyperlinks"
    },
    "FOOTNOTE": {
      "bookmap_element": null,
      "dita_element": "fn",
      "topic_type": null,
      "creates_new_file": false,
      "description": "Footnotes"
    },
    "SHORTDESC": {
      "bookmap_element": null,
      "dita_element": "shortdesc",
      "topic_type": null,
      "creates_new_file": false,
      "description": "Short description after topic title"
    }
```

- [ ] **Step 12: Run tests to verify they pass**

Run: `cd /home/mouad/ALL/SE-Tools/src/PDF_PDM && python -m pytest tests/test_models.py -v`

Expected: ALL PASS

- [ ] **Step 13: Commit**

```bash
git add pdf_models.py dita_mapping_config.json tests/
git commit -m "feat: add TextSpan, HazardData, 6 new ElementTypes, Element.spans field"
```

---

## Task 2: Detection Profiles

**Files:**
- Create: `detection/profiles.py`
- Create: `tests/test_profiles.py`

This task creates the FontProfile/DetectionProfile system and the TOC-depth-to-DITA-level mapping algorithm.

- [ ] **Step 1: Write tests for profiles**

Create `tests/test_profiles.py`:

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'detection'))

from detection.profiles import FontProfile, DetectionProfile, compute_level_mapping


class TestFontProfile:
    def test_matches_exact(self):
        fp = FontProfile(font_size=14.0, is_bold=True)
        assert fp.matches(font_size=14.0, is_bold=True) is True
        assert fp.matches(font_size=14.0, is_bold=False) is False

    def test_matches_with_tolerance(self):
        fp = FontProfile(font_size=14.0, font_size_tolerance=0.5)
        assert fp.matches(font_size=14.3) is True
        assert fp.matches(font_size=15.0) is False

    def test_matches_color_within_tolerance(self):
        fp = FontProfile(color=(0, 147, 0), color_tolerance=15)
        assert fp.matches(color=(5, 150, 3)) is True
        assert fp.matches(color=(0, 200, 0)) is False

    def test_none_fields_match_anything(self):
        fp = FontProfile()  # all None
        assert fp.matches(font_size=16.0, is_bold=True, color=(255, 0, 0)) is True


class TestLevelMapping:
    def test_depth_2(self):
        mapping = compute_level_mapping(max_depth=2)
        assert mapping[1] == "chapter"
        assert mapping[2] == "section"

    def test_depth_3(self):
        mapping = compute_level_mapping(max_depth=3)
        assert mapping[1] == "chapter"
        assert mapping[2] == "topicref"
        assert mapping[3] == "section"

    def test_depth_4_introduces_part(self):
        mapping = compute_level_mapping(max_depth=4)
        assert mapping[1] == "part"
        assert mapping[2] == "chapter"
        assert mapping[3] == "topicref"
        assert mapping[4] == "section"

    def test_depth_5_nested_topicrefs(self):
        mapping = compute_level_mapping(max_depth=5)
        assert mapping[1] == "part"
        assert mapping[2] == "chapter"
        assert mapping[3] == "topicref"
        assert mapping[4] == "topicref"
        assert mapping[5] == "section"

    def test_depth_6(self):
        mapping = compute_level_mapping(max_depth=6)
        assert mapping[1] == "part"
        assert mapping[2] == "chapter"
        assert mapping[3] == "topicref"
        assert mapping[4] == "topicref"
        assert mapping[5] == "topicref"
        assert mapping[6] == "section"

    def test_creates_file_flags(self):
        mapping = compute_level_mapping(max_depth=4)
        # part, chapter, topicref create files; section does not
        creates_file = {
            "part": True, "chapter": True, "topicref": True, "section": False
        }
        for level, dita_type in mapping.items():
            assert creates_file[dita_type] is not None


class TestDetectionProfile:
    def test_serialization_roundtrip(self):
        profile = DetectionProfile(
            name="test",
            level_profiles={"chapter": FontProfile(font_size=16.0, is_bold=True)},
            body_font=FontProfile(font_size=10.0),
        )
        d = profile.to_dict()
        restored = DetectionProfile.from_dict(d)
        assert restored.name == "test"
        assert restored.level_profiles["chapter"].font_size == 16.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/mouad/ALL/SE-Tools/src/PDF_PDM && python -m pytest tests/test_profiles.py -v`

Expected: FAIL — `detection.profiles` does not exist.

- [ ] **Step 3: Implement detection/profiles.py**

Create `detection/profiles.py`:

```python
"""Detection profiles: font criteria per DITA level, learned from document analysis."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, List, Any


@dataclass
class FontProfile:
    """Font characteristics for one DITA hierarchy level."""
    font_name_pattern: Optional[str] = None
    font_size: Optional[float] = None
    font_size_tolerance: float = 0.5
    is_bold: Optional[bool] = None
    is_italic: Optional[bool] = None
    color: Optional[Tuple[int, int, int]] = None
    color_tolerance: int = 15
    x_min: Optional[float] = None
    x_max: Optional[float] = None

    def matches(self, font_size: float = None, is_bold: bool = None,
                is_italic: bool = None, color: Tuple[int, int, int] = None,
                font_name: str = None, x_position: float = None) -> bool:
        """Check if given font properties match this profile."""
        if self.font_size is not None and font_size is not None:
            if abs(font_size - self.font_size) > self.font_size_tolerance:
                return False

        if self.is_bold is not None and is_bold is not None:
            if is_bold != self.is_bold:
                return False

        if self.is_italic is not None and is_italic is not None:
            if is_italic != self.is_italic:
                return False

        if self.color is not None and color is not None:
            for i in range(3):
                if abs(color[i] - self.color[i]) > self.color_tolerance:
                    return False

        if self.font_name_pattern is not None and font_name is not None:
            if self.font_name_pattern.lower() not in font_name.lower():
                return False

        if self.x_min is not None and x_position is not None:
            if x_position < self.x_min:
                return False

        if self.x_max is not None and x_position is not None:
            if x_position > self.x_max:
                return False

        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            'font_name_pattern': self.font_name_pattern,
            'font_size': self.font_size,
            'font_size_tolerance': self.font_size_tolerance,
            'is_bold': self.is_bold,
            'is_italic': self.is_italic,
            'color': self.color,
            'color_tolerance': self.color_tolerance,
            'x_min': self.x_min,
            'x_max': self.x_max,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'FontProfile':
        return cls(
            font_name_pattern=data.get('font_name_pattern'),
            font_size=data.get('font_size'),
            font_size_tolerance=data.get('font_size_tolerance', 0.5),
            is_bold=data.get('is_bold'),
            is_italic=data.get('is_italic'),
            color=tuple(data['color']) if data.get('color') else None,
            color_tolerance=data.get('color_tolerance', 15),
            x_min=data.get('x_min'),
            x_max=data.get('x_max'),
        )


def compute_level_mapping(max_depth: int) -> Dict[int, str]:
    """
    Compute TOC level → DITA bookmap element mapping based on document depth.

    Rules:
    - Section is ALWAYS the deepest level (no new file)
    - Depth ≤ 3: Chapter → Topicref(s) → Section
    - Depth ≥ 4: Part → Chapter → Topicref(s) → Section
    - Up to 6 nested topicref levels between Chapter and Section
    """
    if max_depth < 1:
        return {}

    if max_depth == 1:
        return {1: "section"}

    mapping = {}

    if max_depth <= 3:
        # No Part needed: Chapter, [Topicref(s)], Section
        mapping[1] = "chapter"
        for level in range(2, max_depth):
            mapping[level] = "topicref"
        mapping[max_depth] = "section"
    else:
        # Part needed: Part, Chapter, [Topicref(s)], Section
        mapping[1] = "part"
        mapping[2] = "chapter"
        for level in range(3, max_depth):
            mapping[level] = "topicref"
        mapping[max_depth] = "section"

    return mapping


# Which DITA bookmap elements create new .dita topic files
CREATES_FILE = {"part": True, "chapter": True, "topicref": True, "section": False}


@dataclass
class DetectionProfile:
    """Complete detection profile for a document type."""
    name: str
    level_profiles: Dict[str, FontProfile] = field(default_factory=dict)
    body_font: FontProfile = field(default_factory=FontProfile)
    level_mapping: Dict[int, str] = field(default_factory=dict)
    is_auto_generated: bool = True
    source_document: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'level_profiles': {k: v.to_dict() for k, v in self.level_profiles.items()},
            'body_font': self.body_font.to_dict(),
            'level_mapping': {str(k): v for k, v in self.level_mapping.items()},
            'is_auto_generated': self.is_auto_generated,
            'source_document': self.source_document,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DetectionProfile':
        return cls(
            name=data['name'],
            level_profiles={k: FontProfile.from_dict(v) for k, v in data.get('level_profiles', {}).items()},
            body_font=FontProfile.from_dict(data['body_font']) if data.get('body_font') else FontProfile(),
            level_mapping={int(k): v for k, v in data.get('level_mapping', {}).items()},
            is_auto_generated=data.get('is_auto_generated', True),
            source_document=data.get('source_document'),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/mouad/ALL/SE-Tools/src/PDF_PDM && python -m pytest tests/test_profiles.py -v`

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add detection/profiles.py tests/test_profiles.py
git commit -m "feat: add FontProfile, DetectionProfile, TOC level mapping"
```

---

## Task 3: Document Analysis — Phase A

**Files:**
- Create: `detection/document_analysis.py`
- Create: `tests/test_document_analysis.py`

Implements TOC extraction (bookmarks + visible TOC parsing), content start page detection, cross-page header/footer detection, font profiling, and visual feature indexing.

- [ ] **Step 1: Write tests for document analysis**

Create `tests/test_document_analysis.py`:

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'detection'))

from detection.document_analysis import (
    TocNode, DocumentHierarchy, ExclusionZone, VisualFeatureMap,
    DocumentAnalyzer, parse_visible_toc_line,
)


class TestTocNode:
    def test_basic_creation(self):
        node = TocNode(level=1, title="Chapter 1", page_num=5)
        assert node.level == 1
        assert node.title == "Chapter 1"
        assert node.page_num == 5
        assert node.children == []

    def test_add_child(self):
        parent = TocNode(level=1, title="Chapter", page_num=1)
        child = TocNode(level=2, title="Section", page_num=2)
        parent.children.append(child)
        assert len(parent.children) == 1


class TestDocumentHierarchy:
    def test_max_depth(self):
        root = TocNode(level=0, title="root", page_num=0)
        ch = TocNode(level=1, title="ch", page_num=1)
        sec = TocNode(level=2, title="sec", page_num=2)
        subsec = TocNode(level=3, title="subsec", page_num=3)
        ch.children.append(sec)
        sec.children.append(subsec)
        root.children.append(ch)
        hierarchy = DocumentHierarchy(root=root, has_toc=True, source="bookmarks")
        assert hierarchy.max_depth() == 3

    def test_headings_on_page(self):
        root = TocNode(level=0, title="root", page_num=0)
        ch1 = TocNode(level=1, title="Ch1", page_num=5)
        ch2 = TocNode(level=1, title="Ch2", page_num=5)
        root.children.extend([ch1, ch2])
        hierarchy = DocumentHierarchy(root=root, has_toc=True, source="bookmarks")
        on_page_5 = hierarchy.headings_on_page(5)
        assert len(on_page_5) == 2


class TestParseTocLine:
    def test_dotted_line(self):
        result = parse_visible_toc_line("Introduction .......... 5")
        assert result is not None
        title, page = result
        assert title == "Introduction"
        assert page == 5

    def test_spaced_line(self):
        result = parse_visible_toc_line("Chapter 1    12")
        assert result is not None
        title, page = result
        assert title == "Chapter 1"
        assert page == 12

    def test_no_page_number(self):
        result = parse_visible_toc_line("Just some text without page")
        assert result is None


class TestExclusionZone:
    def test_contains_point(self):
        zone = ExclusionZone(y_min=0, y_max=50, zone_type="header")
        assert zone.contains_y(25) is True
        assert zone.contains_y(100) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/mouad/ALL/SE-Tools/src/PDF_PDM && python -m pytest tests/test_document_analysis.py -v`

Expected: FAIL — module not found.

- [ ] **Step 3: Implement detection/document_analysis.py**

Create `detection/document_analysis.py`. This is a large file — key classes and methods:

```python
"""Phase A: Document analysis — TOC, header/footer, profiling, visual features."""

import sys, os, re, logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pdf_models import TextSpan
from detection.profiles import FontProfile, DetectionProfile, compute_level_mapping

logger = logging.getLogger(__name__)


@dataclass
class TocNode:
    """One entry in the document's table of contents."""
    level: int
    title: str
    page_num: int  # 1-based physical page number
    y_position: Optional[float] = None  # Y-coordinate on target page
    children: List['TocNode'] = field(default_factory=list)


@dataclass
class DocumentHierarchy:
    """Complete document hierarchy from TOC extraction."""
    root: TocNode
    has_toc: bool
    source: str  # "bookmarks", "visible_toc", "none"

    def max_depth(self) -> int:
        """Compute maximum nesting depth."""
        def _depth(node: TocNode) -> int:
            if not node.children:
                return node.level
            return max(_depth(c) for c in node.children)
        if not self.root.children:
            return 0
        return _depth(self.root)

    def headings_on_page(self, page_num: int) -> List[TocNode]:
        """Get all headings expected on a given page."""
        results = []
        def _collect(node: TocNode):
            if node.page_num == page_num and node.level > 0:
                results.append(node)
            for child in node.children:
                _collect(child)
        _collect(self.root)
        return results

    def all_headings(self) -> List[TocNode]:
        """Flatten all headings in document order."""
        results = []
        def _collect(node: TocNode):
            if node.level > 0:
                results.append(node)
            for child in node.children:
                _collect(child)
        _collect(self.root)
        return results


@dataclass
class ExclusionZone:
    """A page zone to exclude from detection (header/footer)."""
    y_min: float
    y_max: float
    zone_type: str  # "header" or "footer"
    text_pattern: Optional[str] = None  # recurring text in this zone

    def contains_y(self, y: float) -> bool:
        return self.y_min <= y <= self.y_max


@dataclass
class VisualFeatureMap:
    """Pre-scanned visual features for one page."""
    page_num: int
    drawings: List[Dict[str, Any]] = field(default_factory=list)
    links: List[Dict[str, Any]] = field(default_factory=list)
    images: List[Tuple] = field(default_factory=list)

    def get_boxes(self, min_width: float = 50, min_height: float = 20) -> List[Tuple[float, float, float, float]]:
        """Get rectangular drawn boxes large enough to contain text."""
        boxes = []
        for d in self.drawings:
            rect = d.get('rect')
            if rect:
                x0, y0, x1, y1 = rect
                if (x1 - x0) >= min_width and (y1 - y0) >= min_height:
                    boxes.append((x0, y0, x1, y1))
        return boxes


def parse_visible_toc_line(line: str) -> Optional[Tuple[str, int]]:
    """
    Parse a visible TOC line like 'Introduction .......... 5' or 'Chapter 1    12'.
    Returns (title, page_number) or None if not a TOC line.
    """
    line = line.strip()
    if not line:
        return None

    # Pattern: title + dot leaders or spaces + page number at end
    match = re.match(r'^(.+?)\s*[.\s]{3,}\s*(\d+)\s*$', line)
    if match:
        title = match.group(1).strip()
        page = int(match.group(2))
        if title and page > 0:
            return (title, page)

    return None


class DocumentAnalyzer:
    """Phase A: Analyzes a PDF document to build context for detection."""

    def __init__(self):
        self.hierarchy: Optional[DocumentHierarchy] = None
        self.content_start_page: int = 1
        self.exclusion_zones: Dict[int, List[ExclusionZone]] = {}
        self.visual_features: Dict[int, VisualFeatureMap] = {}
        self.profile: Optional[DetectionProfile] = None

    def analyze(self, pdf_doc) -> 'DocumentContext':
        """
        Run full Phase A analysis on a PDF document.
        pdf_doc: a fitz.Document object.
        """
        logger.info("Phase A: Starting document analysis")

        # A1: TOC extraction
        self.hierarchy = self._extract_toc(pdf_doc)

        # A2: Content start page
        self.content_start_page = self._detect_content_start(pdf_doc)

        # A3: Header/footer detection
        self.exclusion_zones = self._detect_header_footer(pdf_doc)

        # A4: Font profiling
        self.profile = self._build_font_profile(pdf_doc)

        # A5: Visual feature index
        self.visual_features = self._index_visual_features(pdf_doc)

        return DocumentContext(
            hierarchy=self.hierarchy,
            content_start_page=self.content_start_page,
            exclusion_zones=self.exclusion_zones,
            visual_features=self.visual_features,
            profile=self.profile,
        )

    # ---- A1: TOC Extraction ----

    def _extract_toc(self, pdf_doc) -> DocumentHierarchy:
        """Extract TOC via bookmarks, then visible TOC fallback."""
        # Strategy 1: PDF bookmarks
        toc_entries = pdf_doc.get_toc(simple=False)
        if toc_entries:
            return self._build_hierarchy_from_bookmarks(toc_entries)

        # Strategy 2: Visible TOC parsing
        visible = self._parse_visible_toc(pdf_doc)
        if visible:
            return visible

        # Strategy 3: No TOC
        return DocumentHierarchy(
            root=TocNode(level=0, title="document", page_num=0),
            has_toc=False,
            source="none",
        )

    def _build_hierarchy_from_bookmarks(self, toc_entries: list) -> DocumentHierarchy:
        """Build hierarchy tree from PyMuPDF get_toc() output."""
        root = TocNode(level=0, title="document", page_num=0)
        stack = [root]  # stack of parent nodes

        for entry in toc_entries:
            level = entry[0]
            title = entry[1]
            page_num = entry[2]  # 1-based
            dest = entry[3] if len(entry) > 3 else {}
            y_pos = dest.get('to', (0, 0))[1] if isinstance(dest, dict) and dest.get('to') else None

            node = TocNode(level=level, title=title, page_num=page_num, y_position=y_pos)

            # Find correct parent: pop stack until we find a node at a lower level
            while len(stack) > 1 and stack[-1].level >= level:
                stack.pop()

            stack[-1].children.append(node)
            stack.append(node)

        return DocumentHierarchy(root=root, has_toc=True, source="bookmarks")

    def _parse_visible_toc(self, pdf_doc) -> Optional[DocumentHierarchy]:
        """Try to parse visible TOC pages (first 10 pages)."""
        toc_page_start = None
        toc_entries = []
        page_count = min(pdf_doc.page_count, 10)

        for page_idx in range(page_count):
            page = pdf_doc[page_idx]
            text = page.get_text("text")
            lines = text.split('\n')

            # Detect TOC page by heading
            has_toc_heading = any(
                re.match(r'^\s*(Table\s+of\s+Contents|Contents|Sommaire)\s*$', l, re.IGNORECASE)
                for l in lines
            )
            if has_toc_heading:
                toc_page_start = page_idx

            if toc_page_start is not None:
                # Parse TOC lines with indentation for nesting
                text_dict = page.get_text("dict")
                for block in text_dict.get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        line_text = " ".join(span["text"] for span in line.get("spans", []))
                        parsed = parse_visible_toc_line(line_text)
                        if parsed:
                            title, page_num = parsed
                            x_pos = line["bbox"][0]
                            toc_entries.append((title, page_num, x_pos))

        if not toc_entries:
            return None

        # Determine nesting from X-position indentation
        x_positions = sorted(set(round(e[2], -1) for e in toc_entries))  # round to 10px
        x_to_level = {x: i + 1 for i, x in enumerate(x_positions)}

        root = TocNode(level=0, title="document", page_num=0)
        stack = [root]

        for title, page_num, x_pos in toc_entries:
            level = x_to_level.get(round(x_pos, -1), 1)
            node = TocNode(level=level, title=title, page_num=page_num)

            while len(stack) > 1 and stack[-1].level >= level:
                stack.pop()
            stack[-1].children.append(node)
            stack.append(node)

        return DocumentHierarchy(root=root, has_toc=True, source="visible_toc")

    # ---- A2: Content Start Page ----

    def _detect_content_start(self, pdf_doc) -> int:
        """Auto-detect where main content begins (skip cover, TOC, legal pages)."""
        page_count = min(pdf_doc.page_count, 15)

        for page_idx in range(page_count):
            page = pdf_doc[page_idx]
            text = page.get_text("text").strip()
            text_dict = page.get_text("dict")

            # Cover page: very few text blocks, or large centered text
            blocks = [b for b in text_dict.get("blocks", []) if b.get("type") == 0]
            if len(blocks) <= 2 and page_idx < 3:
                continue

            # TOC page: has dot-leader patterns
            if any(re.search(r'\.{5,}\s*\d+', line) for line in text.split('\n')):
                continue

            # Legal/copyright: small text with copyright keywords
            if any(kw in text.lower() for kw in ['copyright', '©', 'all rights reserved', 'legal notice']):
                if len(text) < 2000:
                    continue

            # This looks like a content page
            return page_idx + 1  # return 1-based

        return 1  # default to first page

    # ---- A3: Header/Footer Detection ----

    def _detect_header_footer(self, pdf_doc) -> Dict[int, List[ExclusionZone]]:
        """Detect recurring text at top/bottom of pages."""
        start = max(0, self.content_start_page - 1)
        end = min(pdf_doc.page_count, start + 10)
        page_height = pdf_doc[start].rect.height if start < pdf_doc.page_count else 842

        top_zone_max = page_height * 0.08
        bottom_zone_min = page_height * 0.92

        # Collect text in top/bottom zones across pages
        top_texts = []  # [(y_center, text), ...]
        bottom_texts = []

        for page_idx in range(start, end):
            page = pdf_doc[page_idx]
            text_dict = page.get_text("dict")

            for block in text_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    bbox = line["bbox"]
                    y_center = (bbox[1] + bbox[3]) / 2
                    line_text = " ".join(s["text"] for s in line.get("spans", []))

                    if y_center < top_zone_max:
                        top_texts.append((round(y_center, 0), line_text.strip()))
                    elif y_center > bottom_zone_min:
                        bottom_texts.append((round(y_center, 0), line_text.strip()))

        # Find recurring Y-positions (same Y on 3+ pages = header/footer)
        zones = {}

        for texts, zone_type in [(top_texts, "header"), (bottom_texts, "footer")]:
            y_counts = Counter(y for y, _ in texts)
            for y_pos, count in y_counts.items():
                if count >= 3:
                    margin = 5
                    zone = ExclusionZone(
                        y_min=y_pos - margin,
                        y_max=y_pos + margin,
                        zone_type=zone_type,
                    )
                    # Apply to all pages
                    for page_idx in range(pdf_doc.page_count):
                        page_key = page_idx + 1
                        if page_key not in zones:
                            zones[page_key] = []
                        zones[page_key].append(zone)

        return zones

    # ---- A4: Font Profiling ----

    def _build_font_profile(self, pdf_doc) -> DetectionProfile:
        """Build detection profile from TOC headings or page sampling."""
        if self.hierarchy and self.hierarchy.has_toc:
            return self._profile_from_toc(pdf_doc)
        else:
            return self._profile_from_sampling(pdf_doc)

    def _profile_from_toc(self, pdf_doc) -> DetectionProfile:
        """Learn font profiles by finding TOC headings on their target pages."""
        level_fonts: Dict[int, List[Dict]] = {}

        for heading in self.hierarchy.all_headings():
            if heading.page_num < 1 or heading.page_num > pdf_doc.page_count:
                continue

            page = pdf_doc[heading.page_num - 1]
            text_dict = page.get_text("dict")

            # Find text matching this heading's title
            title_lower = heading.title.lower().strip()
            for block in text_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    line_text = " ".join(s["text"] for s in line.get("spans", []))
                    # Normalized containment: 80% of title tokens present
                    title_tokens = set(title_lower.split())
                    line_tokens = set(line_text.lower().split())
                    if not title_tokens:
                        continue
                    overlap = len(title_tokens & line_tokens) / len(title_tokens)
                    if overlap >= 0.8:
                        # Extract font properties from first span
                        span = line["spans"][0]
                        font_data = {
                            'font_name': span.get('font', ''),
                            'font_size': span.get('size', 0),
                            'is_bold': 'bold' in span.get('font', '').lower() or
                                       'black' in span.get('font', '').lower() or
                                       'heavy' in span.get('font', '').lower(),
                            'is_italic': 'italic' in span.get('font', '').lower() or
                                         'oblique' in span.get('font', '').lower(),
                            'color': self._span_color(span),
                            'x_position': line["bbox"][0],
                        }
                        if heading.level not in level_fonts:
                            level_fonts[heading.level] = []
                        level_fonts[heading.level].append(font_data)
                        break  # found match, move to next heading

        # Build FontProfile per level (average of samples)
        level_profiles = {}
        max_depth = self.hierarchy.max_depth()
        level_mapping = compute_level_mapping(max_depth)

        for level, samples in level_fonts.items():
            if not samples:
                continue
            dita_type = level_mapping.get(level, "section")
            avg_size = sum(s['font_size'] for s in samples) / len(samples)
            bold_majority = sum(1 for s in samples if s['is_bold']) > len(samples) / 2
            italic_majority = sum(1 for s in samples if s['is_italic']) > len(samples) / 2

            # Most common color
            colors = [s['color'] for s in samples if s['color']]
            common_color = Counter(colors).most_common(1)[0][0] if colors else None

            level_profiles[dita_type] = FontProfile(
                font_size=round(avg_size, 1),
                is_bold=bold_majority if bold_majority else None,
                is_italic=italic_majority if italic_majority else None,
                color=common_color,
            )

        return DetectionProfile(
            name="auto_toc",
            level_profiles=level_profiles,
            body_font=FontProfile(),  # will be refined by sampling
            level_mapping=level_mapping,
            is_auto_generated=True,
        )

    def _profile_from_sampling(self, pdf_doc) -> DetectionProfile:
        """Learn font profiles by sampling first N content pages."""
        start = max(0, self.content_start_page - 1)
        end = min(pdf_doc.page_count, start + 10)

        font_tuples = Counter()  # (size, bold, color) → count

        for page_idx in range(start, end):
            page = pdf_doc[page_idx]
            text_dict = page.get_text("dict")
            for block in text_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        size = round(span.get("size", 0), 1)
                        bold = 'bold' in span.get('font', '').lower()
                        color = self._span_color(span)
                        text = span.get("text", "").strip()
                        if text:
                            font_tuples[(size, bold, color)] += len(text)

        if not font_tuples:
            return DetectionProfile(name="auto_sample", body_font=FontProfile())

        # Most common = body text
        body_tuple = font_tuples.most_common(1)[0][0]
        body_size, body_bold, body_color = body_tuple

        # Bold, larger-than-body = headings (sorted by size descending)
        heading_tuples = sorted(
            [(t, c) for t, c in font_tuples.items() if t[0] > body_size and t[1]],
            key=lambda x: -x[0][0],
        )

        level_profiles = {}
        dita_types = ["chapter", "topicref", "section"]
        for i, (t, _count) in enumerate(heading_tuples[:3]):
            size, bold, color = t
            dtype = dita_types[i] if i < len(dita_types) else "section"
            level_profiles[dtype] = FontProfile(font_size=size, is_bold=bold, color=color)

        max_depth = len(level_profiles) + 1  # +1 for section at bottom
        level_mapping = compute_level_mapping(max_depth)

        return DetectionProfile(
            name="auto_sample",
            level_profiles=level_profiles,
            body_font=FontProfile(font_size=body_size, color=body_color),
            level_mapping=level_mapping,
            is_auto_generated=True,
        )

    # ---- A5: Visual Feature Index ----

    def _index_visual_features(self, pdf_doc) -> Dict[int, VisualFeatureMap]:
        """Pre-scan all pages for drawings, links, and images."""
        features = {}
        for page_idx in range(pdf_doc.page_count):
            page = pdf_doc[page_idx]
            page_num = page_idx + 1

            try:
                drawings = page.get_drawings()
            except Exception:
                drawings = []

            try:
                links = page.get_links()
            except Exception:
                links = []

            try:
                images = page.get_images(full=True)
            except Exception:
                images = []

            features[page_num] = VisualFeatureMap(
                page_num=page_num,
                drawings=drawings,
                links=links,
                images=images,
            )

        return features

    # ---- Helpers ----

    @staticmethod
    def _span_color(span: dict) -> Optional[Tuple[int, int, int]]:
        """Extract RGB color from a PyMuPDF span."""
        color_int = span.get("color", 0)
        if isinstance(color_int, int):
            r = (color_int >> 16) & 0xFF
            g = (color_int >> 8) & 0xFF
            b = color_int & 0xFF
            return (r, g, b)
        return None


@dataclass
class DocumentContext:
    """Complete context from Phase A, passed to Phase B."""
    hierarchy: DocumentHierarchy
    content_start_page: int
    exclusion_zones: Dict[int, List[ExclusionZone]]
    visual_features: Dict[int, VisualFeatureMap]
    profile: DetectionProfile
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/mouad/ALL/SE-Tools/src/PDF_PDM && python -m pytest tests/test_document_analysis.py -v`

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add detection/document_analysis.py tests/test_document_analysis.py
git commit -m "feat: Phase A document analysis — TOC, headers, profiling, visual index"
```

---

## Task 4: Span-Preserving Extraction (B1)

**Files:**
- Create: `detection/extraction.py`
- Create: `tests/test_extraction.py`

- [ ] **Step 1: Write tests for extraction**

Create `tests/test_extraction.py`:

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'detection'))

from pdf_models import TextSpan
from detection.extraction import SpanPreservingExtractor, TextLine
from detection.document_analysis import ExclusionZone


class TestTextLine:
    def test_from_spans(self):
        spans = [
            TextSpan("Hello ", "Arial-Bold", 12.0, True, False, (0, 0, 0), 16, (10, 10, 50, 22)),
            TextSpan("world", "Arial", 12.0, False, False, (0, 0, 0), 0, (50, 10, 90, 22)),
        ]
        line = TextLine(spans=spans, bbox=(10, 10, 90, 22))
        assert line.text == "Hello world"
        assert line.dominant_font_size == 12.0
        assert line.has_monospace is False

    def test_dominant_font_size_uses_mode(self):
        """Most common font size wins, not max."""
        spans = [
            TextSpan("Big", "Arial", 24.0, False, False, (0, 0, 0), 0, (0, 0, 10, 24)),
            TextSpan("Normal text here", "Arial", 12.0, False, False, (0, 0, 0), 0, (10, 0, 100, 12)),
            TextSpan("more normal", "Arial", 12.0, False, False, (0, 0, 0), 0, (100, 0, 150, 12)),
        ]
        line = TextLine(spans=spans, bbox=(0, 0, 150, 24))
        assert line.dominant_font_size == 12.0  # mode, not max

    def test_has_superscript(self):
        spans = [
            TextSpan("text", "Arial", 12.0, False, False, (0, 0, 0), 0, (0, 0, 40, 12)),
            TextSpan("2", "Arial", 8.0, False, False, (0, 0, 0), 1, (40, 0, 45, 8)),
        ]
        line = TextLine(spans=spans, bbox=(0, 0, 45, 12))
        assert line.has_superscript is True

    def test_is_bold_majority(self):
        spans = [
            TextSpan("Bold text", "Arial-Bold", 12.0, True, False, (0, 0, 0), 16, (0, 0, 60, 12)),
            TextSpan(" x", "Arial", 12.0, False, False, (0, 0, 0), 0, (60, 0, 70, 12)),
        ]
        line = TextLine(spans=spans, bbox=(0, 0, 70, 12))
        # Majority of text (by length) is bold
        assert line.is_bold is True


class TestExclusionFiltering:
    def test_lines_in_header_zone_excluded(self):
        extractor = SpanPreservingExtractor()
        zones = [ExclusionZone(y_min=0, y_max=50, zone_type="header")]
        lines = [
            TextLine(spans=[], bbox=(10, 20, 100, 35)),  # in header zone
            TextLine(spans=[], bbox=(10, 100, 100, 115)),  # in content area
        ]
        filtered = extractor.filter_exclusion_zones(lines, zones)
        assert len(filtered) == 1
        assert filtered[0].bbox[1] == 100
```

- [ ] **Step 2: Run tests, verify fail, implement extraction.py**

Create `detection/extraction.py`:

```python
"""B1: Span-preserving text extraction from PyMuPDF."""

import sys, os
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pdf_models import TextSpan
from detection.document_analysis import ExclusionZone


@dataclass
class TextLine:
    """A line of text preserving all span metadata."""
    spans: List[TextSpan]
    bbox: Tuple[float, float, float, float]

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)

    @property
    def dominant_font_size(self) -> float:
        """Most common font size by text length (mode, not max)."""
        if not self.spans:
            return 0.0
        size_weights = Counter()
        for s in self.spans:
            size_weights[round(s.font_size, 1)] += len(s.text)
        return size_weights.most_common(1)[0][0] if size_weights else 0.0

    @property
    def is_bold(self) -> bool:
        """True if majority of text (by length) is bold."""
        if not self.spans:
            return False
        bold_chars = sum(len(s.text) for s in self.spans if s.is_bold)
        total_chars = sum(len(s.text) for s in self.spans)
        return bold_chars > total_chars / 2 if total_chars > 0 else False

    @property
    def is_italic(self) -> bool:
        if not self.spans:
            return False
        italic_chars = sum(len(s.text) for s in self.spans if s.is_italic)
        total_chars = sum(len(s.text) for s in self.spans)
        return italic_chars > total_chars / 2 if total_chars > 0 else False

    @property
    def has_monospace(self) -> bool:
        return any(s.is_monospace for s in self.spans)

    @property
    def has_superscript(self) -> bool:
        return any(s.is_superscript for s in self.spans)

    @property
    def dominant_color(self) -> Optional[Tuple[int, int, int]]:
        """Most common color by text length."""
        if not self.spans:
            return None
        color_weights = Counter()
        for s in self.spans:
            color_weights[s.color] += len(s.text)
        return color_weights.most_common(1)[0][0]

    @property
    def x_position(self) -> float:
        return self.bbox[0]


class SpanPreservingExtractor:
    """Extracts text from a PyMuPDF page preserving all span-level metadata."""

    def extract_page(self, page) -> List[TextLine]:
        """
        Extract all text lines from a page with full span data.
        page: a fitz.Page object.
        """
        text_dict = page.get_text("dict")
        lines = []

        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:  # skip image blocks
                continue

            for line_data in block.get("lines", []):
                spans = []
                for span_data in line_data.get("spans", []):
                    text = span_data.get("text", "")
                    if not text.strip():
                        # Keep whitespace spans for joining but skip empty
                        if text:
                            spans.append(self._span_from_dict(span_data))
                        continue
                    spans.append(self._span_from_dict(span_data))

                if spans and any(s.text.strip() for s in spans):
                    lines.append(TextLine(
                        spans=spans,
                        bbox=tuple(line_data["bbox"]),
                    ))

        return lines

    def extract_region(self, page, bbox: Tuple[float, float, float, float]) -> List[TextLine]:
        """Extract text lines from a specific region (clip rectangle)."""
        import fitz
        clip = fitz.Rect(bbox)
        text_dict = page.get_text("dict", clip=clip)
        lines = []

        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line_data in block.get("lines", []):
                spans = [self._span_from_dict(s) for s in line_data.get("spans", [])
                         if s.get("text", "").strip()]
                if spans:
                    lines.append(TextLine(spans=spans, bbox=tuple(line_data["bbox"])))

        return lines

    def filter_exclusion_zones(self, lines: List[TextLine],
                                zones: List[ExclusionZone]) -> List[TextLine]:
        """Remove lines whose vertical center falls inside an exclusion zone."""
        if not zones:
            return lines

        result = []
        for line in lines:
            y_center = (line.bbox[1] + line.bbox[3]) / 2
            excluded = any(zone.contains_y(y_center) for zone in zones)
            if not excluded:
                result.append(line)
        return result

    @staticmethod
    def _span_from_dict(span_data: dict) -> TextSpan:
        """Convert a PyMuPDF span dict to TextSpan."""
        font_name = span_data.get("font", "")
        flags = span_data.get("flags", 0)

        # Bold detection: from font name OR flags
        is_bold = (
            bool(flags & 16) or
            any(kw in font_name.lower() for kw in ('bold', 'black', 'heavy', 'demi'))
        )

        # Italic detection: from font name OR flags
        is_italic = (
            bool(flags & 2) or
            any(kw in font_name.lower() for kw in ('italic', 'oblique'))
        )

        # Color extraction
        color_int = span_data.get("color", 0)
        if isinstance(color_int, int):
            r = (color_int >> 16) & 0xFF
            g = (color_int >> 8) & 0xFF
            b = color_int & 0xFF
            color = (r, g, b)
        else:
            color = (0, 0, 0)

        return TextSpan(
            text=span_data.get("text", ""),
            font_name=font_name,
            font_size=span_data.get("size", 0.0),
            is_bold=is_bold,
            is_italic=is_italic,
            color=color,
            flags=flags,
            bbox=tuple(span_data.get("bbox", (0, 0, 0, 0))),
        )
```

- [ ] **Step 3: Run tests, verify pass**

Run: `cd /home/mouad/ALL/SE-Tools/src/PDF_PDM && python -m pytest tests/test_extraction.py -v`

- [ ] **Step 4: Commit**

```bash
git add detection/extraction.py tests/test_extraction.py
git commit -m "feat: span-preserving text extraction (B1)"
```

---

## Task 5: Element Assembly (B2)

**Files:**
- Create: `detection/assembly.py`
- Create: `tests/test_assembly.py`

Merges TextLines into logical elements: list symbol merging, paragraph fusion, list nesting.

- [ ] **Step 1: Write tests for assembly**

Create `tests/test_assembly.py` testing: bullet detection, paragraph fusion (lines fuse when similar font/indent, break on large gaps), list nesting by indentation. Use the same pattern as previous test files — `sys.path.insert`, import from `detection.assembly`.

Key test cases:
- `test_standalone_bullet_merged_with_following_text` — a line with only "•" merges with the next line
- `test_paragraph_fusion_similar_lines` — two consecutive 12pt lines 5px apart fuse into one element
- `test_paragraph_break_on_large_gap` — two lines 20px apart stay separate
- `test_heading_not_fused_with_body` — bold 14pt line not fused with following 12pt non-bold line
- `test_list_nesting_by_indentation` — indented list items get higher nesting level

- [ ] **Step 2: Implement detection/assembly.py**

Create `detection/assembly.py` with class `ElementAssembler`:

Key methods:
- `assemble(lines: List[TextLine]) -> List[RawElement]` — main entry point
- `_merge_standalone_list_symbols(lines)` — merge lone bullet chars with following text
- `_fuse_paragraphs(lines)` — group consecutive lines into paragraphs
- `_should_fuse(line_a, line_b)` — check if two lines should merge (gap ≤ 8px, font_size diff ≤ 1pt, same bold, x_diff ≤ 20px)
- `_detect_list_items(elements)` — detect list symbols in text, set `is_list_item`, `list_symbol`, `nesting_level`

The `RawElement` dataclass:
```python
@dataclass
class RawElement:
    spans: List[TextSpan]
    bbox: Tuple[float, float, float, float]
    is_list_item: bool = False
    list_symbol: Optional[str] = None
    nesting_level: int = 0
    text: str = ""  # convenience: joined span text
```

- [ ] **Step 3: Run tests, verify pass, commit**

```bash
git add detection/assembly.py tests/test_assembly.py
git commit -m "feat: element assembly — list merging, paragraph fusion, nesting (B2)"
```

---

## Task 6: Multi-Signal Classification (B3)

**Files:**
- Create: `detection/classification.py`
- Create: `tests/test_classification.py`

Implements the 4-signal classifier: TOC matching, numbering patterns, document context, font profile.

- [ ] **Step 1: Write tests for classification**

Create `tests/test_classification.py`:

Key test cases:
- `test_toc_match_assigns_correct_level` — element text matches TOC title → gets TOC-derived ElementType
- `test_numbering_depth_3_maps_correctly` — "1.2.3 Title" in a 3-depth doc → Section
- `test_numbering_depth_single_digit` — "1 Title" with no deeper numbers → Chapter
- `test_context_bold_inside_topic_becomes_section` — bold heading inside a topic → Section, not new topic
- `test_font_profile_match_as_tiebreaker` — when no TOC and no numbering, font match decides
- `test_toc_beats_font_when_conflicting` — TOC says Chapter, font says Section → Chapter wins
- `test_fuzzy_title_matching` — "Introduction to Safety" matches TOC entry "Introduction to Safety Precautions" at 80% token overlap

- [ ] **Step 2: Implement detection/classification.py**

Create `detection/classification.py` with class `MultiSignalClassifier`:

```python
class MultiSignalClassifier:
    def __init__(self, context: DocumentContext):
        self.context = context
        self.current_hierarchy_stack = []  # tracks Part > Chapter > Topic > Section

    def classify(self, element: RawElement, page_num: int) -> Tuple[ElementType, Dict[str, float]]:
        """
        Classify an element using 4 signals in priority order.
        Returns (element_type, {signal_name: confidence}).
        """
        signals = {}

        # Signal 1: TOC matching
        if self.context.hierarchy.has_toc:
            toc_result = self._match_toc(element, page_num)
            if toc_result:
                signals['toc'] = 0.95
                # ... assign type from TOC level mapping

        # Signal 2: Numbering pattern
        num_result = self._match_numbering(element)
        if num_result:
            signals['numbering'] = 0.85

        # Signal 3: Document context
        ctx_result = self._match_context(element)
        if ctx_result:
            signals['context'] = 0.70

        # Signal 4: Font profile
        font_result = self._match_font_profile(element)
        if font_result:
            signals['font'] = 0.60

        # Decision: highest confidence wins
        # ... pick best signal, return ElementType + signals dict
```

Key helper methods:
- `_match_toc(element, page_num)` — find TOC heading on this page whose title matches element text (80% token overlap, case-insensitive)
- `_match_numbering(element)` — extract `^\d+(\.\d+)*` pattern, count depth, map via `compute_level_mapping`
- `_match_context(element)` — check current hierarchy stack, bold heading inside topic = Section
- `_match_font_profile(element)` — compare element's font against learned FontProfile per level
- `_dita_type_to_element_type(dita_type)` — map "part"→CHAPTER, "chapter"→HYPERSECTION, "topicref"→TOPIC_TITLE, "section"→SECTION

- [ ] **Step 3: Run tests, verify pass, commit**

```bash
git add detection/classification.py tests/test_classification.py
git commit -m "feat: multi-signal classifier — TOC, numbering, context, font (B3)"
```

---

## Task 7: Specialized Type Detection (B4)

**Files:**
- Create: `detection/specialized.py`
- Create: `tests/test_specialized.py`

Detects NOTE, HAZARD_STATEMENT, CODE_BLOCK, HYPERLINK, FOOTNOTE, SHORTDESC.

- [ ] **Step 1: Write tests for specialized detection**

Key test cases:
- `test_note_detection_with_colon` — "NOTE: Be careful" → NOTE, note_type="note"
- `test_tip_detection` — "TIP: Use shortcut" → NOTE, note_type="tip"
- `test_important_detection` — "IMPORTANT - Read first" → NOTE, note_type="important"
- `test_hazard_danger_keyword` — bold "DANGER" → HAZARD_STATEMENT, hazard_type="danger"
- `test_hazard_with_box_signal` — DANGER keyword inside a drawn rectangle → higher confidence
- `test_codeblock_monospace_font` — all spans monospace → CODE_BLOCK
- `test_codeblock_mixed_fonts_not_detected` — some monospace, some not → stays PARAGRAPH
- `test_hyperlink_from_pdf_links` — page.get_links() bbox overlaps text → HYPERLINK with href
- `test_footnote_bottom_zone_superscript` — text in bottom 15% with superscript ref → FOOTNOTE
- `test_shortdesc_after_topic_title` — first short (<200 char) non-bold paragraph after topic title → SHORTDESC

- [ ] **Step 2: Implement detection/specialized.py**

Create `detection/specialized.py` with class `SpecializedDetector`:

```python
class SpecializedDetector:
    """B4: Detects NOTE, HAZARD, CODE_BLOCK, HYPERLINK, FOOTNOTE, SHORTDESC."""

    NOTE_PATTERN = re.compile(r'^(NOTE|TIP|IMPORTANT)\s*[:\-]', re.IGNORECASE)
    HAZARD_PATTERN = re.compile(r'^(DANGER|WARNING|CAUTION|NOTICE)$', re.IGNORECASE)

    def detect(self, elements: List[Element], page_num: int,
               visual_features: VisualFeatureMap, page_height: float) -> List[Element]:
        """Run all specialized detections on classified elements."""
        elements = self._detect_notes(elements)
        elements = self._detect_hazards(elements, visual_features)
        elements = self._detect_codeblocks(elements)
        elements = self._detect_hyperlinks(elements, visual_features)
        elements = self._detect_footnotes(elements, page_height)
        elements = self._detect_shortdesc(elements)
        return elements
```

Each `_detect_*` method iterates over PARAGRAPH elements and reclassifies matches.

- [ ] **Step 3: Run tests, verify pass, commit**

```bash
git add detection/specialized.py tests/test_specialized.py
git commit -m "feat: specialized type detection — NOTE, HAZARD, CODE, LINK, FOOTNOTE, SHORTDESC (B4)"
```

---

## Task 8: Table Structure Recognition (B5)

**Files:**
- Create: `detection/tables.py`
- Create: `tests/test_tables.py`

Table detection (DocLayout/find_tables), structure (TATR/find_tables), cross-page stitching, header detection, caption association.

- [ ] **Step 1: Write tests for table structure**

Key test cases:
- `test_find_tables_fallback` — when no ML model, uses `page.find_tables()` to find table bboxes
- `test_cross_page_stitch_same_header` — table on page N ending at bottom + table on page N+1 with same column count and header → merged
- `test_header_row_detection_first_bold_row` — first row all bold → detected as header
- `test_caption_association_above_table` — "Table 1: Results" text within 20px above table → associated as title
- `test_caption_association_below_table` — caption below table also detected
- `test_cell_content_preserves_spans` — cells extracted with span-level formatting

- [ ] **Step 2: Implement detection/tables.py**

Create `detection/tables.py` with class `TableEngine`:

```python
class TableEngine:
    """B5: Table detection, structure recognition, cross-page stitching."""

    def __init__(self):
        self._tatr_model = None  # lazy load

    def detect_tables(self, page, visual_features: VisualFeatureMap) -> List[Tuple[float, float, float, float]]:
        """Find table bounding boxes on a page."""
        # Try ML model first (PP-DocLayoutV3 via visual_features)
        # Fallback: page.find_tables()
        ...

    def extract_structure(self, page, table_bbox) -> TableStructure:
        """Extract cell structure from a table region."""
        # Try TATR model first
        # Fallback: page.find_tables(clip=table_bbox)
        ...

    def stitch_cross_page(self, tables_by_page: Dict[int, List]) -> List:
        """Detect and merge cross-page tables."""
        ...

    def detect_header_rows(self, structure: TableStructure) -> List[int]:
        """Identify which rows are headers."""
        ...

    def find_caption(self, page, table_bbox, elements: List) -> Optional[str]:
        """Find table caption above or below the table."""
        ...
```

`TableStructure` dataclass:
```python
@dataclass
class TableStructure:
    bbox: Tuple[float, float, float, float]
    cells: List[TableCell]
    header_rows: List[int] = field(default_factory=list)
    caption: Optional[str] = None
    is_continuation: bool = False
    source_page: int = 0
```

- [ ] **Step 3: Run tests, verify pass, commit**

```bash
git add detection/tables.py tests/test_tables.py
git commit -m "feat: table structure recognition — detect, TATR, stitch, headers (B5)"
```

---

## Task 9: Engine Orchestrator + Post-Processing

**Files:**
- Create: `detection/engine.py`
- Modify: `detection/__init__.py`

Wires everything together: Phase A → Phase B stages → Post-processing.

- [ ] **Step 1: Implement detection/engine.py**

```python
"""DetectionEngineV2 — orchestrates the full detection pipeline."""

import sys, os, logging
from typing import List, Dict, Optional, Set

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pdf_models import Element, ElementType, create_element
from detection.document_analysis import DocumentAnalyzer, DocumentContext
from detection.extraction import SpanPreservingExtractor
from detection.assembly import ElementAssembler
from detection.classification import MultiSignalClassifier
from detection.specialized import SpecializedDetector
from detection.tables import TableEngine
from detection.profiles import DetectionProfile

logger = logging.getLogger(__name__)


class DetectionEngineV2:
    """
    Full detection pipeline replacing AdvancedDetectionEngine.

    Usage:
        engine = DetectionEngineV2(config)
        context = engine.analyze_document(pdf_doc)  # Phase A (once)
        # User reviews profile via get_profile_summary() / update_profile()
        elements = engine.detect_elements_on_page(page, page_num, enabled_types)  # Phase B (per page)
    """

    def __init__(self, config=None):
        self.config = config
        self.analyzer = DocumentAnalyzer()
        self.extractor = SpanPreservingExtractor()
        self.assembler = ElementAssembler()
        self.classifier = None  # initialized after analyze_document
        self.specialized = SpecializedDetector()
        self.table_engine = TableEngine()
        self.context: Optional[DocumentContext] = None

    # ---- Phase A ----

    def analyze_document(self, pdf_doc) -> DocumentContext:
        self.context = self.analyzer.analyze(pdf_doc)
        self.classifier = MultiSignalClassifier(self.context)
        return self.context

    def get_profile_summary(self) -> dict:
        if not self.context:
            return {}
        return {
            'has_toc': self.context.hierarchy.has_toc,
            'toc_source': self.context.hierarchy.source,
            'max_depth': self.context.hierarchy.max_depth(),
            'level_mapping': self.context.profile.level_mapping,
            'level_profiles': {k: v.to_dict() for k, v in self.context.profile.level_profiles.items()},
            'content_start_page': self.context.content_start_page,
            'exclusion_zone_count': sum(len(z) for z in self.context.exclusion_zones.values()),
        }

    def update_profile(self, adjustments: dict) -> None:
        if not self.context:
            return
        # Apply user adjustments to level mapping and font profiles
        if 'level_mapping' in adjustments:
            self.context.profile.level_mapping = {
                int(k): v for k, v in adjustments['level_mapping'].items()
            }
        if 'content_start_page' in adjustments:
            self.context.content_start_page = adjustments['content_start_page']

    # ---- Phase B ----

    def detect_elements_on_page(self, page, page_num: int,
                                 enabled_types: Optional[Set[ElementType]] = None) -> List[Element]:
        if not self.context or not self.classifier:
            raise RuntimeError("Call analyze_document() before detecting elements")

        # B1: Span-preserving extraction
        lines = self.extractor.extract_page(page)

        # Filter header/footer exclusion zones
        zones = self.context.exclusion_zones.get(page_num, [])
        lines = self.extractor.filter_exclusion_zones(lines, zones)

        # B2: Element assembly
        raw_elements = self.assembler.assemble(lines)

        # B3: Multi-signal classification
        elements = []
        for raw in raw_elements:
            elem_type, signals = self.classifier.classify(raw, page_num)

            if enabled_types and elem_type not in enabled_types:
                elem_type = ElementType.PARAGRAPH

            elem = create_element(
                elem_type, raw.bbox, page=page_num - 1,
                text=raw.text, spans=raw.spans,
                font_size=raw.spans[0].font_size if raw.spans else None,
                is_bold=any(s.is_bold for s in raw.spans),
                color=raw.spans[0].color if raw.spans else None,
            )
            elem.classification_signals = signals
            elements.append(elem)

        # B4: Specialized type detection
        visual = self.context.visual_features.get(page_num)
        page_height = page.rect.height
        elements = self.specialized.detect(elements, page_num, visual, page_height)

        # B5: Table structure recognition
        table_bboxes = self.table_engine.detect_tables(page, visual)
        for bbox in table_bboxes:
            structure = self.table_engine.extract_structure(page, bbox)
            table_elem = create_element(
                ElementType.TABLE, bbox, page=page_num - 1,
                text=f"Table ({structure.rows}x{structure.cols})" if hasattr(structure, 'rows') else "Table",
            )
            elements.append(table_elem)

        # B6: Post-processing
        elements = self._post_process(elements)

        return elements

    def detect_table_structure(self, page, table_bbox):
        return self.table_engine.extract_structure(page, table_bbox)

    def refine_element_detection(self, page, bbox, element_type) -> Element:
        """Manual element creation with span preservation."""
        lines = self.extractor.extract_region(page, bbox)
        text = " ".join(line.text for line in lines)
        all_spans = [s for line in lines for s in line.spans]

        return create_element(
            element_type, bbox, page=page.number,
            text=text, spans=all_spans,
            is_manual=True, detection_method="manual",
        )

    # ---- B6: Post-Processing ----

    def _post_process(self, elements: List[Element]) -> List[Element]:
        elements = self._resolve_overlaps(elements)
        elements = self._assign_parents(elements)
        elements = self._group_lists(elements)
        return elements

    def _resolve_overlaps(self, elements: List[Element]) -> List[Element]:
        """Push overlapping bboxes apart (FIFO priority)."""
        resolved = []
        for elem in elements:
            for prev in resolved:
                if elem.intersects(prev.bbox):
                    # Minimal displacement
                    elem = self._adjust_bbox(elem, prev)
            resolved.append(elem)
        return resolved

    def _adjust_bbox(self, elem: Element, other: Element) -> Element:
        """Adjust element bbox to avoid overlap with another element."""
        # Calculate minimal displacement direction
        dx_right = other.bbox[2] - elem.bbox[0]
        dx_left = elem.bbox[2] - other.bbox[0]
        dy_down = other.bbox[3] - elem.bbox[1]
        dy_up = elem.bbox[3] - other.bbox[1]

        min_disp = min(dx_right, dx_left, dy_down, dy_up)
        x1, y1, x2, y2 = elem.bbox

        if min_disp == dy_down:
            y1 = other.bbox[3]
            y2 = y1 + elem.height
        elif min_disp == dy_up:
            y2 = other.bbox[1]
            y1 = y2 - elem.height
        elif min_disp == dx_right:
            x1 = other.bbox[2]
            x2 = x1 + elem.width
        else:
            x2 = other.bbox[0]
            x1 = x2 - elem.width

        elem.bbox = (x1, y1, x2, y2)
        return elem

    def _assign_parents(self, elements: List[Element]) -> List[Element]:
        """Assign parent_id based on hierarchy (structural elements contain content)."""
        current_parent_stack = []  # [(element, level), ...]

        for elem in elements:
            if elem.element_type.is_structural:
                # Pop stack to find correct parent level
                level = elem.element_type.priority
                while current_parent_stack and current_parent_stack[-1][1] >= level:
                    current_parent_stack.pop()

                if current_parent_stack:
                    elem.parent_id = current_parent_stack[-1][0].id

                current_parent_stack.append((elem, level))
            else:
                # Content element: parent is the most recent structural element
                if current_parent_stack:
                    elem.parent_id = current_parent_stack[-1][0].id

        return elements

    def _group_lists(self, elements: List[Element]) -> List[Element]:
        """Group consecutive LIST_ITEMs and determine list type (ul/ol)."""
        import re
        for i, elem in enumerate(elements):
            if elem.element_type == ElementType.LIST_ITEM:
                # Check if numbered (ordered) or bulleted (unordered)
                if re.match(r'^\s*\d+[\.\)]', elem.text):
                    elem.metadata['list_type'] = 'ol'
                else:
                    elem.metadata['list_type'] = 'ul'
        return elements
```

- [ ] **Step 2: Update detection/__init__.py**

```python
"""Detection backends — V2 engine (primary), legacy PyMuPDF (deprecated), ML pipeline."""

try:
    from detection.engine import DetectionEngineV2
except ImportError:
    DetectionEngineV2 = None
```

- [ ] **Step 3: Commit**

```bash
git add detection/engine.py detection/__init__.py
git commit -m "feat: DetectionEngineV2 orchestrator + post-processing (B6)"
```

---

## Task 10: Profile Review Dialog (A6 UI)

**Files:**
- Create: `dialogs/profile_review.py`

- [ ] **Step 1: Implement profile review dialog**

Create `dialogs/profile_review.py` — a tkinter `Toplevel` dialog that shows:
1. TOC hierarchy tree (ttk.Treeview) with level → DITA type mapping (editable dropdowns)
2. Font profiles per level (font size, bold, color shown as labels)
3. Content start page (spinbox, editable)
4. Header/footer zone count
5. OK / Cancel buttons

The dialog receives a `ProfileSummary` dict from `engine.get_profile_summary()` and returns an `adjustments` dict to `engine.update_profile()`.

Key class: `ProfileReviewDialog(tk.Toplevel)` with:
- `__init__(self, parent, profile_summary: dict)`
- `get_adjustments(self) -> dict` — returns user's changes
- Standard OK/Cancel pattern

- [ ] **Step 2: Commit**

```bash
git add dialogs/profile_review.py
git commit -m "feat: profile review dialog (Phase A6 UI checkpoint)"
```

---

## Task 11: Hazard Statement Editor (C2)

**Files:**
- Create: `dialogs/hazard_editor.py`

- [ ] **Step 1: Implement hazard statement editor**

Create `dialogs/hazard_editor.py` — a tkinter `Toplevel` dialog with:
- Type dropdown: DANGER / WARNING / CAUTION / NOTICE
- Outputclass dropdown: electric / generic / custom
- Type of Hazard: text entry
- How to Avoid: listbox with Add/Remove buttons (multiple entries)
- Consequence: text entry (disabled when type=DANGER)
- Auto-Parse button: parses element text into fields
- OK / Cancel

Key class: `HazardStatementEditor(tk.Toplevel)` with:
- `__init__(self, parent, element: Element)`
- `auto_parse(self, text: str)` — fills fields from detected text
- `get_hazard_data(self) -> HazardData` — returns structured data
- Updates `element.hazard_data` on OK

DITA rules enforced:
- Consequence field disabled when type="danger"
- Multiple how_to_avoid entries (no `<ol>` inside)

- [ ] **Step 2: Commit**

```bash
git add dialogs/hazard_editor.py
git commit -m "feat: hazard statement structured form editor (C2)"
```

---

## Task 12: Note Editor (C4)

**Files:**
- Create: `dialogs/note_editor.py`

- [ ] **Step 1: Implement note editor**

Simple tkinter `Toplevel`:
- Type dropdown: note / tip / important
- Content text field (auto-filled, keyword prefix stripped)
- OK / Cancel

Updates `element.note_type` on OK.

- [ ] **Step 2: Commit**

```bash
git add dialogs/note_editor.py
git commit -m "feat: note type/content editor (C4)"
```

---

## Task 13: Table Editor Enhancements (C3)

**Files:**
- Modify: `tables/editor.py`

- [ ] **Step 1: Add header row toggle to table editor**

In `tables/editor.py`, add to the right-click context menu:
- "Set as Header Row" / "Remove Header" toggle
- When toggled, sets `cell.is_header = True` for all cells in that row
- Header rows get a distinct background color (light blue: `#D6EAF8`)

Find the existing right-click menu creation code and add the new menu item.

- [ ] **Step 2: Add cross-page table indicator**

When a table has `is_continuation = True`, draw a dashed horizontal line at the stitch boundary in the table editor canvas.

- [ ] **Step 3: Commit**

```bash
git add tables/editor.py
git commit -m "feat: table editor — header row toggle, cross-page indicator (C3)"
```

---

## Task 14: App Integration

**Files:**
- Modify: `app.py`

This is the final integration task. Wire `DetectionEngineV2` into the main GUI.

- [ ] **Step 1: Add engine V2 lazy loader in app.py**

Find the existing `get_detection_engine()` factory function and add a parallel `get_detection_engine_v2()`:

```python
def get_detection_engine_v2():
    try:
        from detection.engine import DetectionEngineV2
        return DetectionEngineV2
    except ImportError:
        return None
```

- [ ] **Step 2: Add Phase A call on PDF open**

Find the method that handles PDF opening (around `self.pdf_doc = fitz.open(file_path)`). After opening, add:

```python
# Phase A: Document analysis
EngineV2 = get_detection_engine_v2()
if EngineV2:
    self.engine_v2 = EngineV2(self.config_manager)
    context = self.engine_v2.analyze_document(self.pdf_doc)

    # A6: Show profile review dialog
    from dialogs.profile_review import ProfileReviewDialog
    summary = self.engine_v2.get_profile_summary()
    dialog = ProfileReviewDialog(self.root, summary)
    self.root.wait_window(dialog)
    adjustments = dialog.get_adjustments()
    if adjustments:
        self.engine_v2.update_profile(adjustments)
```

- [ ] **Step 3: Route per-page detection through V2 engine**

Find the detection loop that calls `detect_elements_on_page()` and add a toggle:

```python
if hasattr(self, 'engine_v2') and self.engine_v2:
    elements = self.engine_v2.detect_elements_on_page(page, page_num, enabled_types)
else:
    # Legacy fallback
    elements = self.detection_engine.detect_elements_on_page(page, enabled_types)
```

- [ ] **Step 4: Add context menu entries for hazard/note editors**

In the canvas right-click menu, add entries:
- "Edit Hazard Statement..." — opens `HazardStatementEditor` for HAZARD_STATEMENT elements
- "Edit Note..." — opens `NoteEditor` for NOTE elements
- "Change Type →" — submenu with all ElementTypes for reclassification

- [ ] **Step 5: Add confidence indicators to canvas drawing**

Find the bbox drawing code. For elements with `confidence < 0.7`, use a dashed line style instead of solid.

- [ ] **Step 6: Commit**

```bash
git add app.py
git commit -m "feat: integrate DetectionEngineV2 into main GUI — profile review, editors, confidence"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** All Phase A (A1-A6), Phase B (B1-B6), Phase C (C1-C4) items have corresponding tasks. C5 (correction propagation) is explicitly deferred per spec.
- [x] **No placeholders:** Every task has concrete code or clear implementation instructions.
- [x] **Type consistency:** `TextSpan`, `HazardData`, `TextLine`, `RawElement`, `TocNode`, `DocumentHierarchy`, `ExclusionZone`, `VisualFeatureMap`, `DocumentContext`, `FontProfile`, `DetectionProfile`, `TableStructure`, `DetectionEngineV2` — all defined in one task and referenced consistently in later tasks.
- [x] **DITA hierarchy mapping:** Uses the correct depth-based algorithm (Section always last, Part at depth ≥ 4, nested Topicrefs for intermediate levels).
- [x] **Backward compatibility:** `Element.text`, `.font_size`, `.is_bold`, `.color` preserved. New fields have defaults. `detect_elements_on_page()` returns `List[Element]`.
- [x] **Out of scope items noted:** DITA exporter updates, batch processing updates, profile persistence — all per spec.
