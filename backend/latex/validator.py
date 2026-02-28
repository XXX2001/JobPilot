from __future__ import annotations

import logging
import re
from pathlib import Path

from backend.latex.compiler import LaTeXCompiler, LaTeXCompilationError

logger = logging.getLogger(__name__)


# Simple regex patterns for common LaTeX syntax mistakes
_UNMATCHED_ENV_RE = re.compile(r"\\begin\{(\w+)\}")
_END_ENV_RE = re.compile(r"\\end\{(\w+)\}")


class LaTeXValidator:
    """Validate a .tex file using heuristics and optional Tectonic dry-run."""

    def __init__(self, compiler: LaTeXCompiler | None = None) -> None:
        self._compiler = compiler or LaTeXCompiler()

    async def validate(self, tex_path: Path) -> list[str]:
        """Return a list of warning strings for the given .tex file.

        Tries a Tectonic compilation first (most reliable). Falls back to
        heuristic regex checks if Tectonic is not available.
        """
        try:
            # Use Tectonic as the primary validator
            return await self._validate_via_tectonic(tex_path)
        except LaTeXCompilationError as exc:
            if "not found" in str(exc).lower():
                # Tectonic not installed — fall back to heuristics
                logger.warning("Tectonic not found; using heuristic validation.")
                return self._heuristic_validate(tex_path)
            # Tectonic found but compilation failed — report the error
            return [f"Compilation error: {exc}"]

    async def _validate_via_tectonic(self, tex_path: Path) -> list[str]:
        """Try to compile and return errors on failure, empty list on success."""
        try:
            import tempfile
            import asyncio

            tectonic = self._compiler._find_tectonic()
            if tectonic is None:
                raise LaTeXCompilationError("Tectonic not found")

            with tempfile.TemporaryDirectory() as tmpdir:
                proc = await asyncio.create_subprocess_exec(
                    tectonic,
                    "--outdir",
                    tmpdir,
                    "--keep-logs",
                    str(tex_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    return [stderr.decode("utf-8", errors="replace").strip()]
                return []
        except LaTeXCompilationError:
            raise

    def _heuristic_validate(self, tex_path: Path) -> list[str]:
        """Basic syntax heuristics when Tectonic is unavailable."""
        warnings: list[str] = []
        try:
            content = tex_path.read_text(encoding="utf-8")
        except Exception as exc:
            return [f"Could not read file: {exc}"]

        begins = _UNMATCHED_ENV_RE.findall(content)
        ends = _END_ENV_RE.findall(content)

        from collections import Counter

        begin_counts = Counter(begins)
        end_counts = Counter(ends)

        for env, count in begin_counts.items():
            if end_counts.get(env, 0) != count:
                warnings.append(
                    f"Unmatched environment: \\begin{{{env}}} ({count}) vs \\end{{{env}}} ({end_counts.get(env, 0)})"
                )

        if "\\documentclass" not in content:
            warnings.append("Missing \\documentclass declaration")

        if "\\begin{document}" not in content:
            warnings.append("Missing \\begin{document}")

        return warnings
