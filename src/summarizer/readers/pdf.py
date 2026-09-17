"""PDF reader (optional dependency).

Requires the `pdf` extra (pypdf). OCR is a separate, even more optional
capability that needs `pytesseract` + Poppler (`pdftoppm`); it is only
attempted when `--ocr` is passed and the PDF has no text layer.
"""

from __future__ import annotations

from pathlib import Path

from summarizer.document.metadata import file_metadata
from summarizer.document.model import Document
from summarizer.document.reader import (
    ABSOLUTE_MAX_BYTES,
    ABSOLUTE_MAX_LINES,
    checked_size,
    effective_limit,
)
from summarizer.errors import InputError
from summarizer.log import Diagnostics

__all__ = ["read_pdf", "extract_pdf_pages"]

_PAGE_SEP = "\f"  # form feed separates pages; survives plain-text output


def _import_pypdf():
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as exc:
        raise InputError(
            "PDF support requires the optional dependency `pypdf`",
            hint="install it with: pip install 'summarizer[pdf]'",
        ) from exc
    return PdfReader


def extract_pdf_pages(path: Path, *, diag: Diagnostics, options: dict | None = None) -> list[str]:
    options = options or {}
    PdfReader = _import_pypdf()
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise InputError(f"cannot open PDF {path}: {exc}") from exc

    pages: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # pypdf can choke on malformed pages
            diag.warn(f"PDF page {index}: text extraction failed ({exc}); skipping")
            text = ""
        # Normalize: keep the page readable but avoid pathological whitespace.
        lines = [ln.rstrip() for ln in text.splitlines()]
        while lines and not lines[0].strip():
            lines.pop(0)
        pages.append("\n".join(lines))

    no_text = not any(p.strip() for p in pages)
    if no_text and options.get("ocr"):
        pages = _ocr_pages(path, reader, diag=diag)
    elif no_text:
        diag.warn(f"PDF {path} has no extractable text layer; result may be empty")
    return pages


def _ocr_pages(path: Path, reader, diag: Diagnostics) -> list[str]:
    """Best-effort OCR for scanned PDFs. Requires pytesseract + poppler."""
    try:
        import pytesseract  # type: ignore
        from pdf2image import convert_from_path  # type: ignore
    except ImportError:
        raise InputError(
            "OCR requires optional dependencies `pytesseract` and `pdf2image` "
            "plus the `pdftoppm` binary (poppler-utils)",
            hint="install with: pip install 'summarizer[ocr]'",
        ) from None
    try:
        images = convert_from_path(str(path), dpi=200)
    except Exception as exc:
        raise InputError(
            f"cannot render PDF for OCR: {exc}",
            hint="is poppler-utils installed? (`pdftoppm` must be on PATH)",
        ) from exc
    pages: list[str] = []
    for index, image in enumerate(images, start=1):
        diag.progress(f"OCR page {index}/{len(images)}...")
        try:
            text = pytesseract.image_to_string(image)
        except Exception as exc:
            raise InputError(f"OCR failed on page {index}: {exc}") from exc
        pages.append(text.strip())
    return pages


def read_pdf(path: Path, *, diag: Diagnostics, options: dict | None = None) -> Document:
    options = options or {}
    max_bytes = effective_limit(options.get("max_bytes"), ABSOLUTE_MAX_BYTES)
    max_lines = effective_limit(options.get("max_lines"), ABSOLUTE_MAX_LINES)
    checked_size(path, max_bytes)
    pages = extract_pdf_pages(path, diag=diag, options=options)
    content = _PAGE_SEP.join(pages)
    if content.count("\n") + (1 if content else 0) > max_lines:
        raise InputError(
            f"input too long: {path} exceeds maximum of {max_lines} lines "
            f"(see `input.max_lines` in config)"
        )
    meta = file_metadata(path)
    meta["pages"] = len(pages)
    meta["pdf"] = True
    if options.get("ocr"):
        meta["ocr"] = True
    return Document(
        content=content,
        source=str(path),
        mime_type="application/pdf",
        encoding="utf-8",
        size=path.stat().st_size,
        metadata=meta,
    )
