"""Render the synthetic policy and letters to PDF, so ingestion is tested on real PDFs.

    python -m eval.make_pdfs            # writes eval/data/pdf/*.pdf

Headings (all-caps lines and "SECTION n." lines) are drawn bold and larger,
long lines are wrapped, and a blank line becomes a paragraph gap, like a
typical insurer document.
"""

import textwrap
from pathlib import Path

EVAL_DIR = Path(__file__).parent
OUT_DIR = EVAL_DIR / "data" / "pdf"

PAGE_W, PAGE_H = 595, 842  # A4 in points
MARGIN = 56
BODY_SIZE, HEADING_SIZE = 10, 12.5
LINE_GAP = 14
PARAGRAPH_GAP = 8
WRAP_CHARS = 92


def _is_heading(line: str) -> bool:
    s = line.strip()
    return s.upper().startswith("SECTION ") or (s.isupper() and len(s) < 80 and ":" not in s)


def render_text_pdf(text: str, path: Path, blank_pages: int = 0) -> Path:
    """Write text to a PDF. blank_pages adds pages with no text layer (like a scan)."""
    import pymupdf

    pdf = pymupdf.open()
    page = pdf.new_page(width=PAGE_W, height=PAGE_H)
    y = MARGIN

    for raw in text.splitlines():
        if not raw.strip():
            y += PARAGRAPH_GAP
            continue
        heading = _is_heading(raw)
        size = HEADING_SIZE if heading else BODY_SIZE
        font = "hebo" if heading else "helv"
        if heading:
            y += 4
        # Continuation lines of a numbered item are indented like a real document.
        indent = "    " if raw.lstrip()[:1].isdigit() and not heading else ""
        for piece in textwrap.wrap(raw.strip(), WRAP_CHARS, subsequent_indent=indent):
            if y > PAGE_H - MARGIN:
                page = pdf.new_page(width=PAGE_W, height=PAGE_H)
                y = MARGIN
            page.insert_text((MARGIN, y + size), piece, fontname=font, fontsize=size)
            y += LINE_GAP if not heading else LINE_GAP + 2

    for _ in range(blank_pages):
        pdf.new_page(width=PAGE_W, height=PAGE_H)

    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.save(path)
    pdf.close()
    return path


def main() -> None:
    policy = (EVAL_DIR / "data" / "synthetic_policy.txt").read_text(encoding="utf-8")
    print(render_text_pdf(policy, OUT_DIR / "synthetic_policy.pdf"))
    for folder in sorted(p for p in (EVAL_DIR / "cases").iterdir() if p.is_dir()):
        letter = (folder / "letter.txt").read_text(encoding="utf-8")
        print(render_text_pdf(letter, OUT_DIR / f"{folder.name}_letter.pdf"))


if __name__ == "__main__":
    main()
