"""Render a generated contract (Markdown with clause anchors) into a real PDF.

The generators write contracts as Markdown; this turns them into the PDF a carrier would
actually send, so the contract parser is tested against a PDF and not against the Markdown.
Supports what the generated contracts use: `#` headings, `**bold**`, paragraphs, and
`<a id="clause-..."></a>` anchors (dropped; the parser finds clauses by their numbers).
Output is byte-reproducible: the creation date is fixed.
"""

import re
from datetime import datetime, timezone
from pathlib import Path

from fpdf import FPDF

_ANCHOR = re.compile(r'^<a id="[^"]*"></a>$')
_FIXED_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)  # constant so the same input gives the same bytes
_LATIN1 = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                         "–": "-", "—": "-", "×": "x", "−": "-", "…": "..."})


def _clean(text: str) -> str:
    return text.translate(_LATIN1).encode("latin-1", "replace").decode("latin-1")


def render_markdown_pdf(markdown: str, out_path: str | Path) -> Path:
    pdf = FPDF(format="A4")
    pdf.set_creation_date(_FIXED_DATE)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    for block in re.split(r"\n\s*\n", markdown.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip() and not _ANCHOR.match(ln.strip())]
        if not lines:
            continue
        heading = re.match(r"^(#+)\s+(.*)", lines[0])
        if heading:
            pdf.set_font("Helvetica", "B", 16 if len(heading.group(1)) == 1 else 13)
            pdf.multi_cell(0, 8, _clean(heading.group(2)), new_x="LMARGIN", new_y="NEXT")
            lines = lines[1:]
        pdf.set_font("Helvetica", "", 10)
        for line in lines:
            pdf.multi_cell(0, 5, _clean(line), markdown=True, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out))
    return out
