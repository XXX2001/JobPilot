from __future__ import annotations

import asyncio
import logging
import platform
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


class LaTeXCompilationError(Exception):
    """Raised when Tectonic compilation fails or Tectonic is not found."""


class LaTeXCompiler:
    """Async wrapper around the Tectonic LaTeX compiler."""

    def _find_tectonic(self) -> str | None:
        """Locate the tectonic binary.

        Search order:
        1. System PATH
        2. <project root>/bin/tectonic (or tectonic.exe on Windows)
        """
        # Check PATH first
        binary = shutil.which("tectonic")
        if binary:
            return binary

        # Check local bin/ directory relative to this file's project root
        is_windows = platform.system() == "Windows"
        exe_suffix = ".exe" if is_windows else ""
        bin_name = f"tectonic{exe_suffix}"

        # Walk up from this file to find project root (has pyproject.toml)
        here = Path(__file__).resolve()
        for parent in here.parents:
            candidate = parent / "bin" / bin_name
            if candidate.exists():
                return str(candidate)

        return None

    async def compile(self, tex_path: Path, output_dir: Path | None = None) -> Path:
        """Compile a .tex file using Tectonic.

        Args:
            tex_path: Path to the .tex file to compile.
            output_dir: Directory to write the PDF. Defaults to tex_path's parent.

        Returns:
            Path to the compiled PDF file.

        Raises:
            LaTeXCompilationError: If Tectonic is not found or compilation fails.
        """
        tectonic = self._find_tectonic()
        if tectonic is None:
            raise LaTeXCompilationError(
                "Tectonic not found. Run 'python scripts/install.sh' or add tectonic to PATH."
            )

        if output_dir is None:
            output_dir = tex_path.parent

        output_dir.mkdir(parents=True, exist_ok=True)

        cmd = [tectonic, "--outdir", str(output_dir), str(tex_path)]
        logger.info("Compiling %s with tectonic -> %s", tex_path.name, output_dir)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace")
            raise LaTeXCompilationError(f"Tectonic exited {proc.returncode}:\n{stderr_text}")

        # Tectonic writes <stem>.pdf in output_dir
        pdf_path = output_dir / (tex_path.stem + ".pdf")
        if not pdf_path.exists():
            raise LaTeXCompilationError(
                f"Compilation appeared to succeed but PDF not found at {pdf_path}"
            )

        logger.info("PDF written to %s (%d bytes)", pdf_path, pdf_path.stat().st_size)
        return pdf_path
