from __future__ import annotations
import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MARKER_RE = re.compile(
    r"% --- JOBPILOT:(\w+(?::\w+)*):START ---\n(.*?)\n% --- JOBPILOT:\1:END ---", re.DOTALL
)


@dataclass
class LaTeXSections:
    summary: Optional[str] = None
    experience_block: Optional[str] = None
    experience_bullets: list[str] = field(default_factory=list)
    letter_paragraph: Optional[str] = None
    has_markers: bool = False


class LaTeXParser:
    def extract_sections(self, tex_content: str) -> LaTeXSections:
        """Extract editable sections using comment markers. Falls back to TexSoup."""
        sections = LaTeXSections()
        matches = dict(MARKER_RE.findall(tex_content))

        if matches:
            sections.has_markers = True
            sections.summary = matches.get("SUMMARY", "").strip() or None
            experience_block = matches.get("EXPERIENCE", "")
            sections.experience_block = experience_block.strip() or None
            if experience_block:
                sections.experience_bullets = self.extract_bullets(experience_block)
            sections.letter_paragraph = matches.get("LETTER:PARA", "").strip() or None
        else:
            # TexSoup fallback: try to find \section{Summary} content
            logger.warning("No JOBPILOT markers found, attempting TexSoup fallback")
            sections.has_markers = False
            try:
                import texsoup

                soup = texsoup.TexSoup(tex_content)
                # Attempt to find a section named Summary and use its content as a hint
                for sec in soup.find_all("section"):
                    title = "".join(str(x) for x in sec.args)
                    if "Summary" in title:
                        sections.summary = str(sec.text).strip() or None
                        break
            except Exception:
                # if TexSoup is not available or parsing fails, silently continue
                pass
        return sections

    def extract_bullets(self, block: str) -> list[str]:
        """Parse \\item lines from a LaTeX itemize block."""
        bullets = re.findall(r"\\item\s+(.+?)(?=\\item|\\end|$)", block, re.DOTALL)
        return [b.strip() for b in bullets if b.strip()]

    def validate_markers(self, tex_content: str) -> list[str]:
        """Return warnings for mismatched or missing markers."""
        warnings = []
        starts = re.findall(r"% --- JOBPILOT:(\w+(?::\w+)*):START ---", tex_content)
        ends = re.findall(r"% --- JOBPILOT:(\w+(?::\w+)*):END ---", tex_content)
        for s in starts:
            if s not in ends:
                warnings.append(f"Marker START without END: JOBPILOT:{s}")
        for e in ends:
            if e not in starts:
                warnings.append(f"Marker END without START: JOBPILOT:{e}")
        return warnings
