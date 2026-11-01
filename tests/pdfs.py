"""A real, parseable one-page PDF for tests (R09: magic bytes alone no longer pass)."""

from __future__ import annotations

import io

from pypdf import PdfWriter


def real_pdf(text: str = "") -> bytes:
    """One blank page; *text* lands in the metadata, so different texts hash differently."""
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.add_metadata({"/Subject": text})
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()
