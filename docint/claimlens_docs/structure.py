"""Turn the lines of a policy into sectioned chunks.

A chunk is one numbered clause ("4.2 Specified disease waiting period ...")
or one unnumbered paragraph. Each chunk carries a section_path such as
"4 Waiting Periods > 4.2" so retrieval and the UI can show where it came from.

Headings are found by numbering ("SECTION 4. WAITING PERIODS", "4. EXCLUSIONS"),
by font size when the line came from a PDF, or by a short all-caps line.
"""

import re
from collections import Counter
from dataclasses import dataclass

from .schemas import BBox, Chunk

WAITING_PERIOD = "WAITING_PERIOD"
EXCLUSION = "EXCLUSION"
DEFINITION = "DEFINITION"


@dataclass
class Line:
    page: int
    text: str
    bbox: BBox
    size: float | None = None  # font size; None for plain-text input
    bold: bool = False


# "SECTION 4. WAITING PERIODS", "Section 4 - Exclusions", "4. EXCLUSIONS"
SECTION_HEADING = re.compile(r"^(?:section|part|chapter)\s+(\d+)\s*[.:\-]?\s*(.*)$", re.I)
NUMBERED_HEADING = re.compile(r"^(\d+)\.\s+([A-Z][A-Z &/,\-()]{2,})$")
# "4.2 text", "4.2.3. text", "4.2) text"
CLAUSE = re.compile(r"^(\d+(?:\.\d+)+)[.)]?\s+(.+)$")
DEFINITION_SENTENCE = re.compile(r"^[\"“']?[A-Z][\w\s\-/()]{1,60}[\"”']?\s+(?:means|shall mean)\b")


def _title(text: str) -> str:
    text = text.strip(" .:-")
    return text.title() if text.isupper() else text


def _body_size(lines: list[Line]) -> float | None:
    sizes: Counter[float] = Counter()
    for line in lines:
        if line.size:
            sizes[round(line.size, 1)] += len(line.text)
    return sizes.most_common(1)[0][0] if sizes else None


def _is_unnumbered_heading(line: Line, body_size: float | None) -> bool:
    text = line.text.strip()
    if not text or len(text) > 80 or CLAUSE.match(text) or text.endswith((".", ",", ";")):
        return False
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 3:
        return False
    if body_size and line.size and line.size >= body_size * 1.15:
        return True
    return text.isupper() and ":" not in text


def lines_from_text(text: str) -> list[Line]:
    """Plain text to Line objects (page 1, empty bbox). Used for .txt input and tests.

    A blank line in the text becomes a vertical gap, so paragraphs stay apart.
    """
    lines, y = [], 0.0
    for raw in text.splitlines():
        if raw.strip():
            lines.append(Line(page=1, text=raw.strip(), bbox=[0.0, y, 0.0, y + 10.0]))
            y += 12.0
        else:
            y += 12.0  # the gap is what marks a new paragraph
    return lines


def _union(boxes: list[BBox]) -> BBox:
    return [
        min(b[0] for b in boxes), min(b[1] for b in boxes),
        max(b[2] for b in boxes), max(b[3] for b in boxes),
    ]


def _join(texts: list[str]) -> str:
    out = ""
    for t in texts:
        if not out:
            out = t
        elif out.endswith("-"):
            out += t  # keep "sub-" + "limit" together as "sub-limit"
        else:
            out += " " + t
    return out


def _tags(text: str, section_path: str) -> list[str]:
    lower, path = text.lower(), section_path.lower()
    tags = []
    if "waiting period" in lower or "waiting period" in path:
        tags.append(WAITING_PERIOD)
    if "exclu" in lower or "exclu" in path:
        tags.append(EXCLUSION)
    if "definition" in path or DEFINITION_SENTENCE.match(CLAUSE.sub(r"\2", text)):
        tags.append(DEFINITION)
    return tags


def _clause_path(top: str | None, top_num: str | None, number: str) -> str:
    parts = number.split(".")
    ancestors = [".".join(parts[: i + 1]) for i in range(1, len(parts))]
    if top and (top_num is None or parts[0] == top_num):
        return " > ".join([top] + ancestors)
    return " > ".join(ancestors)


def build_chunks(lines: list[Line], doc_id: str) -> list[Chunk]:
    body_size = _body_size(lines)
    chunks: list[Chunk] = []
    top: str | None = None
    top_num: str | None = None

    current: list[Line] = []
    current_path = "Preamble"

    def flush() -> None:
        if not current:
            return
        text = _join([ln.text for ln in current])
        chunks.append(Chunk(
            chunk_id=f"{doc_id}-c-{len(chunks):04d}",
            doc_id=doc_id,
            section_path=current_path,
            page=current[0].page,
            text=text,
            bbox=_union([ln.bbox for ln in current]),
            tags=_tags(text, current_path),
        ))
        current.clear()

    previous: Line | None = None
    for line in lines:
        text = line.text.strip()
        if not text:
            continue

        m = SECTION_HEADING.match(text) or NUMBERED_HEADING.match(text)
        if m and not CLAUSE.match(text):
            flush()
            top_num = m.group(1)
            top = f"{top_num} {_title(m.group(2))}".strip()
            current_path = top
            previous = line
            continue

        if _is_unnumbered_heading(line, body_size):
            flush()
            top, top_num = _title(text), None
            current_path = top
            previous = line
            continue

        clause = CLAUSE.match(text)
        if clause:
            flush()
            current_path = _clause_path(top, top_num, clause.group(1))
        elif current and previous is not None:
            new_page = line.page != previous.page
            gap = line.bbox[1] - previous.bbox[3]
            height = previous.bbox[3] - previous.bbox[1]
            paragraph_break = gap > 0.5 * height if height > 0 else False
            if new_page:
                flush()  # same clause, but a chunk has one page and one bbox
            elif paragraph_break:
                flush()  # a new unnumbered paragraph belongs to the section, not the clause
                current_path = top or "Preamble"

        current.append(line)
        previous = line

    flush()
    return chunks
