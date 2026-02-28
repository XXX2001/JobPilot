from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


class LaTeXInjector:
    def _replace_marker_content(self, tex: str, marker: str, new_content: str) -> str:
        """Replace content between JOBPILOT:MARKER:START and END."""
        pattern = re.compile(
            rf"(% --- JOBPILOT:{marker}:START ---\n).*?(\n% --- JOBPILOT:{marker}:END ---)",
            re.DOTALL,
        )
        replacement = rf"\g<1>{new_content}\g<2>"
        result, count = pattern.subn(replacement, tex)
        if count == 0:
            raise ValueError(f"Marker JOBPILOT:{marker} not found in LaTeX content")
        return result

    def inject_summary_edit(self, original_tex: str, new_summary: str) -> str:
        """Replace summary section. Returns modified copy, never touches original."""
        return self._replace_marker_content(original_tex, "SUMMARY", new_summary)

    def inject_experience_edits(self, original_tex: str, edits: list) -> str:
        """Replace only specific \\item lines that were edited."""
        tex = original_tex
        for edit in edits:
            # Replace the specific bullet at this index
            bullets = re.findall(r"(\\item\s+.+?)(?=\\item|\\end|$)", tex, re.DOTALL)
            if edit.index < len(bullets):
                old_bullet = bullets[edit.index]
                new_bullet = f"\\item {edit.edited}"
                tex = tex.replace(old_bullet.rstrip(), new_bullet, 1)
        return tex

    def inject_letter_edit(self, original_tex: str, new_paragraph: str, company_name: str) -> str:
        """Replace letter paragraph and {company_name} placeholders."""
        tex = self._replace_marker_content(original_tex, "LETTER:PARA", new_paragraph)
        tex = tex.replace("{company_name}", company_name)
        return tex
