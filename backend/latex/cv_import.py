"""Import a PDF/DOCX CV and convert it into the project's LaTeX template.

The tailoring pipeline (CVModifier/CVApplicator) operates on a structured
LaTeX CV. Most users only have a PDF or Word CV, so this module extracts their
text and uses the configured LLM to render it into a copy of the bundled
``resume.cls`` template — removing the "you must hand-write LaTeX" adoption
barrier. The result is compile-validated before it is accepted.
"""
from __future__ import annotations

import io
import logging

from backend.llm.base import LLMClient

logger = logging.getLogger(__name__)


class CVImportError(Exception):
    """Raised when a PDF/DOCX CV cannot be extracted or converted."""


def extract_text(data: bytes, ext: str) -> str:
    """Extract plain text from a PDF or DOCX byte payload."""
    ext = ext.lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    elif ext == ".docx":
        import docx  # python-docx

        doc = docx.Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" \t ".join(cell.text for cell in row.cells))
        text = "\n".join(parts)
    else:
        raise CVImportError(f"Unsupported CV import type: {ext}")

    text = text.strip()
    if len(text) < 50:
        raise CVImportError(
            "Could not extract readable text from the file (it may be a scanned "
            "image or empty). Please upload a text-based PDF/DOCX or a .tex CV."
        )
    return text


_CONVERT_PROMPT = """You convert a candidate's CV text into a LaTeX document.

You are given (1) a LaTeX TEMPLATE that compiles with a custom `resume.cls`
class, and (2) the raw TEXT extracted from the candidate's existing CV.

Produce a COMPLETE LaTeX document that:
- Keeps the TEMPLATE's preamble, `\\documentclass{{resume}}`, the custom header
  macros (\\name, \\contactdetails, \\myname, \\printname) and the
  `\\begin{{rSection}}{{...}}` section structure EXACTLY as in the template.
- Replaces the EXAMPLE content inside each section with the real candidate's
  details from the TEXT (name, contact line, profile/summary, education,
  experience bullets, skills, additional info).
- Uses only LaTeX commands already present in the template. Do NOT introduce
  new packages or commands. Escape LaTeX specials in free text (& % $ # _).
- Keeps section names in English: Profile, Education, Experience,
  Additional Information. Omit a section if the TEXT has nothing for it.
- Must compile as-is. Output ONLY the LaTeX source, no commentary, no code
  fences.

TEMPLATE:
{template}

CANDIDATE CV TEXT:
{cv_text}
"""


def _strip_to_document(raw: str) -> str:
    """Strip code fences/prose, returning from \\documentclass to \\end{document}."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        raw = raw.rsplit("```", 1)[0]
    start = raw.find("\\documentclass")
    end = raw.rfind("\\end{document}")
    if start == -1 or end == -1:
        raise CVImportError("LLM did not return a complete LaTeX document.")
    return raw[start: end + len("\\end{document}")] + "\n"


async def convert_to_latex(cv_text: str, template_tex: str, client: LLMClient) -> str:
    """Render extracted CV text into a compile-ready copy of *template_tex*."""
    # Bound the input so a huge CV can't blow the context window.
    cv_text = cv_text[:12_000]
    prompt = _CONVERT_PROMPT.format(template=template_tex, cv_text=cv_text)
    raw = await client.generate_text(prompt)
    tex = _strip_to_document(raw)
    # Sanity: the conversion must still be the resume-class template.
    if "\\begin{document}" not in tex:
        raise CVImportError("Converted CV is missing \\begin{document}.")
    return tex
