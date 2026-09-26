"""PDF reader (optional dependency).

Requires the `pdf` extra (pypdf). OCR is a separate, even more optional
capability that needs `pytesseract` + Poppler (`pdftoppm`); it is only
attempted when `--ocr` is passed and the PDF has no text layer.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from summarizer.document.metadata import file_metadata
from summarizer.document.model import Document
from summarizer.document.reader import (
    ABSOLUTE_MAX_BYTES,
    ABSOLUTE_MAX_LINES,
    checked_size,
    effective_limit,
)
from summarizer.document.tables import count_structured_tables, structure_tables
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
    """Extract per-page text with finite, configured resource limits.

    The page count, extracted bytes and extracted lines are all bounded, so a
    hostile or malformed PDF cannot be used to exhaust memory. Per-page
    failures are recorded as warnings instead of crashing the run.
    """
    options = options or {}
    PdfReader = _import_pypdf()
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise InputError(f"cannot open PDF {path}: {exc}") from exc

    if reader.is_encrypted:
        raise InputError(
            "encrypted PDF: supply a decrypted local copy",
            hint="this tool never decrypts documents",
        )
    page_limit = effective_limit(options.get("max_pdf_pages"), 10_000)
    if len(reader.pages) > page_limit:
        raise InputError(
            f"PDF has {len(reader.pages)} pages, exceeding the maximum of "
            f"{page_limit} (see `input.max_pdf_pages` in config)",
            hint="raise `input.max_pdf_pages` if this document is genuine",
        )

    max_extracted = effective_limit(options.get("max_extracted_bytes"), ABSOLUTE_MAX_BYTES)
    max_lines = effective_limit(options.get("max_lines"), ABSOLUTE_MAX_LINES)
    structure = bool(options.get("structure_tables", True))
    structured_tables = 0
    total_bytes = total_lines = 0
    pages: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # pypdf can choke on malformed pages
            if len(diag.warnings) < 200:
                diag.warn(f"PDF page {index}: text extraction failed ({exc}); skipping")
            text = ""
        if structure and text:
            text, count = structure_tables(text)
            structured_tables += count
        # Bound as we accumulate: the limit must hold *before* the whole
        # document has been pulled into memory, not after.
        total_bytes += len(text.encode("utf-8")) + bool(pages)
        total_lines += text.count("\n") + bool(text)
        if total_bytes > max_extracted or total_lines > max_lines:
            raise InputError(
                "PDF extracted text exceeds input.max_extracted_bytes or "
                "input.max_lines",
                hint="raise the limit or use a smaller document",
            )
        if not text.strip() and len(diag.warnings) < 200:
            diag.warn(f"PDF page {index}: no extractable text")
        pages.append(text)

    if not any(page.strip() for page in pages):
        if options.get("ocr"):
            pages = _ocr_pages(path, len(reader.pages), diag=diag, options=options)
        if not any(page.strip() for page in pages):
            raise InputError(
                "PDF has no extractable text (empty or scanned/image-only)",
                hint="use --ocr to read a scanned PDF locally",
            )
    return pages


def _ocr_pages(path: Path, page_count: int, *, diag: Diagnostics, options: dict) -> list[str]:
    """Local-only OCR for scanned PDFs; never installs or fetches anything."""
    try:
        import pytesseract  # type: ignore
        from pdf2image import convert_from_path  # type: ignore
    except ImportError:
        raise InputError(
            "OCR requires the optional dependencies `pytesseract` and "
            "`pdf2image` plus the local `pdftoppm` and `tesseract` binaries",
            hint="install with: pip install 'summarizer[ocr]'",
        ) from None
    for binary in ("pdftoppm", "pdfinfo", "tesseract"):
        if shutil.which(binary) is None:
            raise InputError(
                f"OCR binary `{binary}` was not found; nothing is installed "
                "automatically and no online OCR service is used",
                hint="install Tesseract and Poppler locally, then retry",
            )
    timeout = effective_limit(options.get("ocr_timeout_seconds"), 600)
    pages: list[str] = []
    for index in range(1, page_count + 1):
        diag.progress(f"OCR page {index}/{page_count}...")
        try:
            images = convert_from_path(
                str(path), first_page=index, last_page=index, dpi=150,
                size=(2400, 2400), thread_count=1, timeout=timeout,
            )
        except Exception as exc:
            raise InputError(f"cannot render PDF page {index} for OCR: {exc}") from exc
        try:
            text = pytesseract.image_to_string(images[0], timeout=timeout) if images else ""
        except Exception as exc:
            raise InputError(f"OCR failed on page {index}: {exc}") from exc
        finally:
            for image in images:
                image.close()
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
    starts = []
    offset = 0
    for page in pages:
        starts.append(offset)
        offset += len(page) + 1
    meta["page_starts"] = starts
    meta["pdf"] = True
    meta["extracted_bytes"] = sum(len(page.encode("utf-8")) for page in pages)
    structured = count_structured_tables(content)
    if structured:
        meta["structured_tables"] = structured
    if options.get("ocr"):
        meta["ocr"] = True
    warnings = diag.take_warnings()
    if warnings:
        meta["warnings"] = warnings[:200]
    return Document(
        content=content,
        source=str(path),
        mime_type="application/pdf",
        encoding="utf-8",
        size=path.stat().st_size,
        metadata=meta,
    )
