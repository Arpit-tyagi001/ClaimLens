"""PDF ingestion with PyMuPDF: lines with bounding boxes, then chunks.

Policies are split into sectioned clause chunks (structure.py). Letters are
kept one chunk per line, so a rejection reason can be highlighted on exactly
the lines it covers.

A page with no extractable text (a scan) is listed in pages_without_text.
If no page has text, has_text_layer is False and the pipeline must stop with
a clear message. OCR is not implemented.
"""

from pathlib import Path

from .schemas import Chunk, ParsedDocument
from .structure import Line, build_chunks, lines_from_text

BOLD_FLAG = 16  # PyMuPDF span flag


def _round_box(box) -> list[float]:
    return [round(float(v), 1) for v in box]


def extract_lines(path: str | Path) -> tuple[list[Line], int, list[int]]:
    """Return (lines, n_pages, pages_without_text). Pages are 1-based."""
    import pymupdf  # imported here so the rest of the package works without it

    lines: list[Line] = []
    empty_pages: list[int] = []
    with pymupdf.open(path) as pdf:
        n_pages = pdf.page_count
        for page_index, page in enumerate(pdf, start=1):
            page_lines = 0
            for block in page.get_text("dict", sort=True)["blocks"]:
                if block.get("type") != 0:  # 0 = text; images have no lines
                    continue
                for raw in block["lines"]:
                    spans = [s for s in raw["spans"] if s["text"].strip()]
                    if not spans:
                        continue
                    text = "".join(s["text"] for s in raw["spans"]).strip()
                    biggest = max(spans, key=lambda s: len(s["text"]))
                    lines.append(Line(
                        page=page_index,
                        text=" ".join(text.split()),
                        bbox=_round_box(raw["bbox"]),
                        size=round(biggest["size"], 1),
                        bold=bool(biggest["flags"] & BOLD_FLAG),
                    ))
                    page_lines += 1
            if page_lines == 0:
                empty_pages.append(page_index)
    return lines, n_pages, empty_pages


def _line_chunks(lines: list[Line], doc_id: str, section_path: str) -> list[Chunk]:
    return [
        Chunk(
            chunk_id=f"{doc_id}-l-{i:04d}",
            doc_id=doc_id,
            section_path=section_path,
            page=line.page,
            text=line.text,
            bbox=line.bbox,
        )
        for i, line in enumerate(lines)
    ]


def _chunks_for(lines: list[Line], doc_id: str, doc_type: str) -> list[Chunk]:
    if doc_type == "policy":
        return build_chunks(lines, doc_id)
    if doc_type == "letter":
        return _line_chunks(lines, doc_id, "letter")
    raise ValueError(f"doc_type must be 'policy' or 'letter', not {doc_type!r}")


def ingest_document(path: str, doc_id: str, doc_type: str) -> ParsedDocument:
    """Contract 4.3: parse a PDF into a ParsedDocument and register it for grounding."""
    from .grounding import register_document

    lines, n_pages, empty_pages = extract_lines(path)
    doc = ParsedDocument(
        doc_id=doc_id,
        doc_type=doc_type,  # type: ignore[arg-type]  # checked in _chunks_for
        n_pages=n_pages,
        has_text_layer=len(empty_pages) < n_pages,
        chunks=_chunks_for(lines, doc_id, doc_type),
        pages_without_text=empty_pages,
    )
    register_document(doc)
    return doc


def ingest_text(text: str, doc_id: str, doc_type: str) -> ParsedDocument:
    """Same as ingest_document but for plain text (eval cases, tests). Pages are all 1."""
    from .grounding import register_document

    doc = ParsedDocument(
        doc_id=doc_id,
        doc_type=doc_type,  # type: ignore[arg-type]
        n_pages=1,
        has_text_layer=bool(text.strip()),
        chunks=_chunks_for(lines_from_text(text), doc_id, doc_type),
    )
    register_document(doc)
    return doc


def document_text(doc: ParsedDocument) -> tuple[str, list[tuple[int, int, Chunk]]]:
    """Rebuild the document as one string, plus the character span of every chunk.

    Chunks are joined with a newline, or a blank line where there was a
    paragraph gap or a page break, so the regex extractors (which look for
    blank lines between list items) work on PDF text as they do on .txt.
    """
    parts: list[str] = []
    spans: list[tuple[int, int, Chunk]] = []
    pos = 0
    previous: Chunk | None = None
    for chunk in doc.chunks:
        if previous is not None:
            gap = chunk.bbox[1] - previous.bbox[3]
            height = previous.bbox[3] - previous.bbox[1]
            new_paragraph = chunk.page != previous.page or (height > 0 and gap > 0.5 * height)
            sep = "\n\n" if new_paragraph else "\n"
            parts.append(sep)
            pos += len(sep)
        parts.append(chunk.text)
        spans.append((pos, pos + len(chunk.text), chunk))
        pos += len(chunk.text)
        previous = chunk
    return "".join(parts), spans


def chunks_in_span(spans: list[tuple[int, int, Chunk]], start: int, end: int) -> list[Chunk]:
    """Chunks that overlap the character range [start, end)."""
    return [chunk for s, e, chunk in spans if s < end and e > start]
