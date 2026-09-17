"""
Screenplay PDF → Final Draft (.fdx) adapter.

The design contract (and the reason this module is deliberately thin):
``fdx.py`` already owns the normalized screenplay model — ``FinalDraft`` /
``Content`` / ``Paragraph`` — and the entire downstream pipeline
(``to_script_model`` → ``ScriptModel`` → breakdown)
is built on it. A PDF carries no structure, only glyphs at (x, y); the *only*
hard problem here is classifying each text line into one of Final Draft's
paragraph types. So that's all this module does: it extracts geometry-tagged
lines, classifies them by their left-margin band (the single most reliable
signal in a properly formatted screenplay), assembles them into paragraphs,
and re-uses the exact ``FinalDraft`` model ``fdx.py`` parses.

The output is a real, valid ``.fdx`` — the standard working currency. PDF
ingestion therefore collapses to "convert to .fdx, then run the .fdx path",
so there is one conversion path to maintain, and every ingested PDF leaves a
diffable, testable artifact behind.

Dependencies: PyMuPDF (``fitz``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF
import xmltodict

from scriptbuddy.fdx import ParagraphType, ScriptContainer
from scriptbuddy.headings import repair_headings

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Line-level classification vocabulary
# ---------------------------------------------------------------------------

# A slugline always opens with a scope token. Matched case-insensitively against
# the cleaned line; this is authoritative regardless of x-position.
SCENE_RE = re.compile(
    r"^\s*(INT\.?/EXT\.?|EXT\.?/INT\.?|I/E\.?|INT\.?|EXT\.?|EST\.?)\b", re.IGNORECASE
)
# Transitions read "CUT TO:", "DISSOLVE TO:", "FADE OUT.", etc. — all-caps, end colon.
TRANSITION_RE = re.compile(r"^[A-Z0-9 \-'/]+TO:\s*$")
FADE_RE = re.compile(
    r"^(FADE (IN|OUT|TO BLACK)|SMASH CUT|MATCH CUT|HARD CUT)[\.:]?\s*$"
)
# A whole-line parenthetical wrapper: "(beat)", "(to JUAN)", "(V.O.)".
PAREN_LINE_RE = re.compile(r"^\(.*\)$")
# Page number / revision slug in a corner: "2.", "12", "A3.".
PAGE_NUM_RE = re.compile(r"^[A-Z]?\d{1,3}[A-Z]?\.?$")
# Front-matter markers used to detect (and drop) a title page.
FRONT_MATTER_RE = re.compile(
    r"^(written by|by|story by|screenplay by|draft|revision)\b", re.IGNORECASE
)

# Shot prefixes that read as all-caps but are SHOT lines, not character cues.
_SHOT_KEYWORDS = (
    "INSERT",
    "ANGLE ON",
    "ANGLE",
    "POV",
    "CLOSE ON",
    "CLOSE UP",
    "WIDE ON",
    "WIDE",
    "BACK TO",
    "INTERCUT",
)


def norm_text(s: str) -> str:
    """Normalize typographic punctuation to ASCII and collapse runs of whitespace.

    Smart quotes / em-dashes from print PDFs poison downstream string matching
    (and TTS); fold them once, here at the door."""
    if not s:
        return ""
    s = (
        s.replace("–", "-")
        .replace("—", "-")
        .replace("‘", "'")
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("…", "...")
        .replace("ﬁ", "fi")
        .replace("ﬂ", "fl")
        .replace("�", "'")  # replacement char most often stands in for an apostrophe
    )
    return re.sub(r"[ \t ]+", " ", s).strip()


# ---------------------------------------------------------------------------
# Geometry-tagged line
# ---------------------------------------------------------------------------


@dataclass
class Line:
    """One visual line of text with the geometry the classifier needs."""

    text: str
    x0: float
    x1: float
    y0: float
    page: int
    width: float
    paragraph_type: Optional[str] = None  # a ParagraphType value, or "__skip__"

    @property
    def core(self) -> str:
        """The line with trailing parentheticals stripped — the cue-name candidate."""
        return re.sub(r"\s*\([^)]*\)\s*$", "", self.text).strip()

    @property
    def is_allcaps(self) -> bool:
        c = self.core
        return bool(c) and c.upper() == c and any(ch.isalpha() for ch in c)


@dataclass
class Bands:
    """Auto-calibrated x-margin thresholds for one document (points)."""

    action_x: float
    dialogue_min: float
    character_min: float
    page_num_x: float = 470.0


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def pdf_text_chars(pdf_path: str | Path, sample_pages: int = 6) -> int:
    """Total extractable text characters across the first ``sample_pages`` pages.

    The cheap signal for "is there a usable text layer, or is this a scan?"."""
    chars = 0
    with fitz.open(str(pdf_path)) as doc:
        for pno, page in enumerate(doc):
            if pno >= sample_pages:
                break
            chars += len(page.get_text("text").strip())
    return chars


def resolve_source_pdf(
    pdf_path: str | Path, ocr: bool = True, min_chars: int = 200
) -> Path:
    """Return a text-bearing PDF to parse — the original if it already has a text
    layer, otherwise an OCR'd sidecar produced by ``ocrmypdf`` (if installed).

    Image-only screenplays (faxed/scanned) carry no extractable glyphs; ``fitz``
    returns nothing and parsing yields an empty script. ``ocrmypdf --skip-text``
    bakes a searchable text layer in, after which the normal geometry pipeline
    works unchanged. If ``ocrmypdf`` isn't on PATH we log and fall back to the
    original (a no-text parse is better than a crash, and surfaces the cause)."""
    pdf_path = Path(pdf_path)
    if not ocr or pdf_text_chars(pdf_path) >= min_chars:
        return pdf_path

    import shutil as _shutil
    import subprocess

    if _shutil.which("ocrmypdf") is None:
        _log.warning(
            "PdfScreenplay: %s has no text layer and 'ocrmypdf' is not installed; "
            "parsing will be empty. Install ocrmypdf to ingest scanned scripts.",
            pdf_path.name,
        )
        return pdf_path

    ocr_out = pdf_path.with_suffix(".ocr.pdf")
    try:
        subprocess.run(
            [
                "ocrmypdf",
                "--skip-text",
                "--deskew",
                "--optimize",
                "1",
                str(pdf_path),
                str(ocr_out),
            ],
            check=True,
            capture_output=True,
        )
        if pdf_text_chars(ocr_out) >= min_chars:
            _log.info("PdfScreenplay: OCR'd scanned script → %s", ocr_out.name)
            return ocr_out
    except subprocess.CalledProcessError as exc:
        _log.warning("PdfScreenplay: ocrmypdf failed (%s); using original.", exc)
    return pdf_path


@dataclass
class _Span:
    """A positioned text fragment harvested from the page (pre-grouping)."""

    text: str
    x0: float
    x1: float
    y0: float
    size: float = 12.0
    font: str = ""


@dataclass
class Watermark:
    """A stamped overlay (font size far above the screenplay body) detected and
    stripped during parsing — e.g. a "Property of …" ownership watermark. Kept so
    provenance can be recorded rather than silently discarded."""

    text: str
    font: str
    size: float
    pages: List[int] = field(default_factory=list)  # 0-based pages it appears on

    @property
    def count(self) -> int:
        return len(self.pages)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "font": self.font,
            "size": round(self.size, 1),
            "pages": [p + 1 for p in self.pages],  # 1-based for humans
            "count": self.count,
        }

    def to_fdx_attrs(self) -> Dict[str, Any]:
        """The ``<Watermark .../>`` attribute dict embedded in the FinalDraft doc
        so it round-trips through .fdx and lands in script.json."""
        return {
            "@Text": self.text,
            "@Font": self.font,
            "@Size": round(self.size, 1),
            "@Count": self.count,
        }


def _harvest_spans(page) -> List[_Span]:
    """Every non-empty text span on the page, with geometry + font — the raw
    material for visual-line reconstruction and watermark detection."""
    spans: List[_Span] = []
    data = page.get_text("dict") or {}
    for blk in data.get("blocks", []):
        if blk.get("type") != 0:  # 0 == text block
            continue
        for dl in blk.get("lines", []):
            for s in dl.get("spans", []):
                raw = s.get("text", "")
                if not raw or not raw.strip():
                    continue
                bx = s["bbox"]
                spans.append(
                    _Span(
                        text=raw,
                        x0=bx[0],
                        x1=bx[2],
                        y0=bx[1],
                        size=float(s.get("size", 12.0)),
                        font=str(s.get("font", "")),
                    )
                )
    return spans


def _body_font_size(pages_spans: List[List[_Span]]) -> float:
    """The screenplay's body text size = the most common span size across the
    document. Anchors watermark detection: overlays are typeset far larger than
    the 12pt Courier body."""
    from collections import Counter

    sizes = Counter(
        round(sp.size * 2) / 2  # 0.5pt buckets
        for spans in pages_spans
        for sp in spans
        if _has_alpha(sp.text)
    )
    if not sizes:
        return 12.0
    return sizes.most_common(1)[0][0]


# A span is an overlay/watermark when its font is dramatically larger than the
# body — both a ratio (relative to this doc) and an absolute floor, so a merely
# slightly-larger heading is never mistaken for a watermark.
_WATERMARK_SIZE_RATIO = 1.6
_WATERMARK_SIZE_FLOOR = 18.0


def _is_oversized(sp: _Span, body_size: float) -> bool:
    return sp.size >= max(body_size * _WATERMARK_SIZE_RATIO, _WATERMARK_SIZE_FLOOR)


def _group_spans_into_lines(
    spans: List[_Span], width: float, page_no: int, y_tol: float = 5.0
) -> List[Line]:
    """Regroup spans that share a baseline into visual lines.

    This is the key robustness step for scanned / OCR'd PDFs, where each *word*
    is positioned separately and would otherwise read as its own line at its own
    x-margin. We cluster by ``y0`` (a line's words share a baseline), sort each
    cluster left-to-right, and take the leftmost x as the line's true margin —
    the only x-value the margin classifier can trust. Clean digital PDFs (whole
    line already in one span) pass through unchanged."""
    if not spans:
        return []
    spans = sorted(spans, key=lambda s: (round(s.y0, 1), s.x0))
    lines: List[Line] = []
    cluster: List[_Span] = [spans[0]]
    ref_y = spans[0].y0
    for sp in spans[1:]:
        if abs(sp.y0 - ref_y) <= y_tol:
            cluster.append(sp)
        else:
            lines.append(_finish_line(cluster, width, page_no))
            cluster = [sp]
            ref_y = sp.y0
    lines.append(_finish_line(cluster, width, page_no))
    return lines


def _finish_line(cluster: List[_Span], width: float, page_no: int) -> Line:
    """Collapse a baseline-sharing span cluster into one :class:`Line`."""
    cluster = sorted(cluster, key=lambda s: s.x0)
    text = norm_text(" ".join(s.text for s in cluster))
    return Line(
        text=text,
        x0=min(s.x0 for s in cluster),
        x1=max(s.x1 for s in cluster),
        y0=min(s.y0 for s in cluster),
        page=page_no,
        width=width,
    )


def _has_alnum(s: str) -> bool:
    return any(ch.isalnum() for ch in s)


def _has_alpha(s: str) -> bool:
    return any(ch.isalpha() for ch in s)


def extract_lines_and_watermarks(
    pdf_path: str | Path,
    ignore_pages: Optional[List[int]] = None,
    watermark: Optional[str] = None,
) -> Tuple[List[Line], List[Watermark]]:
    """Extract visual text lines AND detect/strip oversized overlay watermarks.

    Spans are harvested with their font size, then any span far larger than the
    document body (a stamped watermark like "Property of …") is pulled out BEFORE
    baseline grouping — critical, because a watermark word can share a baseline
    with real dialogue and would otherwise merge into and corrupt that line. The
    remaining spans are regrouped into lines (see :func:`_group_spans_into_lines`)
    so scanned/OCR'd scripts still reconstruct. ``ignore_pages`` is 1-based.
    Returns ``(lines, watermarks)`` — the watermarks deduped by text across pages."""
    ignore = {p - 1 for p in (ignore_pages or [])}

    # Pass 1 — harvest every page's spans (need the whole doc to learn body size).
    page_spans: List[Tuple[int, float, List[_Span]]] = []
    with fitz.open(str(pdf_path)) as doc:
        for pno, page in enumerate(doc):
            if pno in ignore:
                continue
            page_spans.append((pno, page.rect.width, _harvest_spans(page)))

    body_size = _body_font_size([sp for _, _, sp in page_spans])

    # Pass 2 — split oversized overlays out, group the rest into lines.
    lines: List[Line] = []
    wm_by_text: Dict[str, Watermark] = {}
    for pno, width, spans in page_spans:
        content: List[_Span] = []
        for sp in spans:
            if _is_oversized(sp, body_size):
                key = norm_text(sp.text)
                if not key:
                    continue
                wm = wm_by_text.get(key)
                if wm is None:
                    wm = Watermark(text=key, font=sp.font, size=sp.size)
                    wm_by_text[key] = wm
                if pno not in wm.pages:
                    wm.pages.append(pno)
            else:
                content.append(sp)

        for ln in _group_spans_into_lines(content, width, pno):
            if watermark and ln.text == watermark:
                continue
            # Drop scan speckle: a 1-char line with no alphanumeric content.
            if len(ln.text) <= 1 and not _has_alnum(ln.text):
                continue
            lines.append(ln)

    return lines, list(wm_by_text.values())


def extract_lines(
    pdf_path: str | Path,
    ignore_pages: Optional[List[int]] = None,
    watermark: Optional[str] = None,
) -> List[Line]:
    """Visual text lines only (watermarks stripped). See
    :func:`extract_lines_and_watermarks` for the watermark report."""
    return extract_lines_and_watermarks(pdf_path, ignore_pages, watermark)[0]


def detect_title_page(lines: List[Line]) -> bool:
    """True if page 0 looks like a title page (front matter, no slugline)."""
    page0 = [ln for ln in lines if ln.page == 0 and ln.text]
    if not page0:
        return False
    has_front = any(FRONT_MATTER_RE.match(ln.text) for ln in page0)
    has_scene = any(SCENE_RE.match(ln.text) for ln in page0)
    return has_front and not has_scene


# ---------------------------------------------------------------------------
# Margin calibration
# ---------------------------------------------------------------------------


def calibrate_bands(lines: List[Line]) -> Bands:
    """Measure the document's own indent columns rather than hard-coding them.

    A screenplay has a few fixed left margins (action, dialogue, character). We
    locate each as the most *populated* x-peak inside its expected zone, smoothed
    over a ±12pt window so OCR column-jitter (which smears one logical column
    across a 30–40pt range) still resolves to a single column. Anchoring on
    population — not the leftmost/rightmost cluster — makes calibration immune to
    minority scan artifacts (margin bullets, edge speckle, page numbers). Only
    alphabetic lines vote; digit/punctuation slugs carry no margin signal."""
    width = median([ln.width for ln in lines]) if lines else 612.0
    counts: Dict[int, int] = {}
    for ln in lines:
        if _has_alpha(ln.text):
            x = round(ln.x0)
            counts[x] = counts.get(x, 0) + 1
    if not counts:
        return Bands(action_x=108.0, dialogue_min=150.0, character_min=216.0)

    smooth = 12

    def peak(lo: float, hi: float) -> Optional[float]:
        """The x in [lo, hi] with the greatest smoothed population, or None."""
        best_x, best_pop = None, -1
        for x in counts:
            if not (lo <= x <= hi):
                continue
            pop = sum(c for xx, c in counts.items() if abs(xx - x) <= smooth)
            if pop > best_pop:
                best_x, best_pop = float(x), pop
        return best_x

    # Action is the dominant column in the left band (left of where dialogue
    # starts, ~26% of page width); it anchors the proportional search windows.
    action_x = peak(0, width * 0.26) or 108.0
    dialogue_x = peak(action_x + 40, action_x + 115) or (action_x + 72)
    character_x = peak(action_x + 130, action_x + 230) or (action_x + 144)

    return Bands(
        action_x=action_x,
        dialogue_min=(action_x + dialogue_x) / 2.0,
        character_min=(dialogue_x + character_x) / 2.0,
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify(line: Line, bands: Bands, prev_type: Optional[str]) -> str:
    """Assign a single line its :class:`ParagraphType` value.

    Margin band is the primary signal; caps / punctuation / regex refine it.
    Returns ``"__skip__"`` for page numbers and other non-content."""
    t = line.text
    if not t:
        return "__skip__"

    # No letters → page number, revision slug, or scan speckle; never content.
    if not _has_alpha(t):
        return "__skip__"

    # Page number / corner slug — only when it actually sits in the corner.
    if PAGE_NUM_RE.match(t) and (line.x0 >= bands.page_num_x or line.y0 < 60):
        return "__skip__"

    # Authoritative structural matches (position-independent).
    if SCENE_RE.match(t):
        return ParagraphType.SCENE_HEADING.value
    if TRANSITION_RE.match(t) or FADE_RE.match(t):
        return ParagraphType.TRANSITION.value
    if PAREN_LINE_RE.match(t):
        return ParagraphType.PARENTHETICAL.value

    in_char_band = line.x0 >= bands.character_min
    in_dialogue_band = line.x0 >= bands.dialogue_min

    # Character cue: all-caps, SHORT (a name, not a sentence), in the character
    # column. The word/char ceilings reject all-caps scene description that
    # happens to sit in the cue band (common in OCR'd scans). A trailing
    # parenthetical (CONT'D)/(V.O.) is fine — it's stripped downstream.
    core = line.core
    if in_char_band and line.is_allcaps and len(core) <= 40 and len(core.split()) <= 5:
        if any(kw in t.upper() for kw in _SHOT_KEYWORDS):
            return ParagraphType.SHOT.value
        return ParagraphType.CHARACTER.value

    # Dialogue band, but not a cue → spoken line (the cue set prev_type=Character).
    if in_dialogue_band:
        return ParagraphType.DIALOGUE.value

    # Left margin. All-caps here are shots/inserts (e.g. "WANTED POSTER - ..."),
    # which read as action description, not as cues.
    if line.is_allcaps and any(kw in t.upper() for kw in _SHOT_KEYWORDS):
        return ParagraphType.SHOT.value

    # A lowercase-leading line continuing an action paragraph stays action.
    return ParagraphType.ACTION.value


# ---------------------------------------------------------------------------
# Paragraph assembly
# ---------------------------------------------------------------------------

# Types whose wrapped visual lines should be merged into one paragraph.
_MERGEABLE = {
    ParagraphType.ACTION.value,
    ParagraphType.DIALOGUE.value,
    ParagraphType.PARENTHETICAL.value,
}


def _typical_line_gaps(lines: List[Line]) -> Dict[int, float]:
    """Per-page median *intra-paragraph* line spacing (points).

    PDFs don't emit a blank line between paragraphs — they just leave a wider
    vertical gap. To recover paragraph breaks we first learn each page's normal
    line-to-line spacing (the small, regular gaps), then treat anything markedly
    larger as a break."""
    per_page: Dict[int, List[float]] = {}
    prev: Dict[int, Line] = {}
    for ln in lines:
        if not ln.text:
            continue
        p = ln.page
        if p in prev:
            dy = ln.y0 - prev[p].y0
            if 4.0 <= dy <= 20.0:  # plausible single line-step
                per_page.setdefault(p, []).append(dy)
        prev[p] = ln
    return {p: (median(g) if g else 12.0) for p, g in per_page.items()}


def assemble_paragraphs(lines: List[Line], bands: Bands) -> List[Dict[str, str]]:
    """Classify, then merge wrapped lines into FDX paragraph dicts.

    Wrapping is purely visual: a single speech or action beat spans several PDF
    lines at the same margin. We coalesce consecutive same-type lines (action /
    dialogue / parenthetical) into one paragraph, breaking on a type change, a
    page change, a blank line, or a vertical gap wider than the page's normal
    line spacing (the implicit paragraph break). Sluglines, cues, transitions
    and shots stay one paragraph each."""
    gaps = _typical_line_gaps(lines)
    # Pass 1 — classify with running context.
    prev_type: Optional[str] = None
    classified: List[Line] = []
    for ln in lines:
        if not ln.text:
            ln.paragraph_type = "__blank__"
            classified.append(ln)
            continue
        ptype = classify(ln, bands, prev_type)
        ln.paragraph_type = ptype
        if ptype != "__skip__":
            prev_type = ptype
        classified.append(ln)

    # Pass 2 — merge.
    paragraphs: List[Dict[str, str]] = []
    buf: List[Line] = []
    buf_type: Optional[str] = None

    def flush() -> None:
        nonlocal buf, buf_type
        if buf and buf_type:
            text = " ".join(b.text for b in buf if b.text).strip()
            # Tidy spacing inside parentheticals: "( beat )" → "(beat)".
            if buf_type == ParagraphType.PARENTHETICAL.value:
                text = text.replace("( ", "(").replace(" )", ")")
            if text:
                paragraphs.append({"@Type": buf_type, "Text": text})
        buf, buf_type = [], None

    for ln in classified:
        ptype = ln.paragraph_type
        if ptype in ("__skip__",):
            continue
        if ptype == "__blank__":
            flush()
            continue
        # Mergeable run on the same page, with no implicit paragraph break
        # (a vertical jump well beyond the page's normal line spacing) → buffer.
        if (
            buf_type == ptype
            and ptype in _MERGEABLE
            and buf
            and ln.page == buf[-1].page
        ):
            typical = gaps.get(ln.page, 12.0)
            if (ln.y0 - buf[-1].y0) <= typical * 1.6:
                buf.append(ln)
                continue
        flush()
        buf, buf_type = [ln], ptype
    flush()
    return paragraphs


# ---------------------------------------------------------------------------
# Public adapter
# ---------------------------------------------------------------------------


class PdfScreenplay:
    """Adapter turning a screenplay PDF into the shared FDX model / a .fdx file.

    Usage mirrors :class:`scriptbuddy.fdx.ScriptContainer`::

        container = PdfScreenplay.to_container(pdf_path)   # in-memory, == .fdx load
        fdx_path  = PdfScreenplay.to_fdx(pdf_path)         # writes the artifact
    """

    DOCUMENT_TYPE = "Script"
    VERSION = "5"

    @staticmethod
    def to_finaldraft_dict(
        pdf_path: str | Path,
        ignore_pages: Optional[List[int]] = None,
        watermark: Optional[str] = None,
        ocr: bool = True,
        check_headings: bool = True,
    ) -> Dict[str, Any]:
        """Parse the PDF into the ``{"FinalDraft": {...}}`` dict that both the
        pydantic model and the .fdx serializer consume — the single source of
        truth, so the in-memory container and the written file never diverge.

        A scanned/image-only PDF is transparently OCR'd first (see
        :func:`resolve_source_pdf`) when ``ocr=True``. Stamped overlay watermarks
        are detected + stripped, and embedded as ``<Watermark>`` elements on the
        document so they round-trip through .fdx and flow into script.json."""
        pdf_path = resolve_source_pdf(pdf_path, ocr=ocr)
        lines, watermarks = extract_lines_and_watermarks(
            pdf_path, ignore_pages=ignore_pages, watermark=watermark
        )
        if watermarks:
            _log.info(
                "PdfScreenplay: stripped %d watermark(s): %s",
                len(watermarks),
                ", ".join(repr(w.text) for w in watermarks),
            )
        # Auto-drop a detected title page unless the caller already scoped pages.
        if ignore_pages is None and detect_title_page(lines):
            lines = [ln for ln in lines if ln.page != 0]
        bands = calibrate_bands(lines)
        paragraphs = assemble_paragraphs(lines, bands)
        # Sanity-check the headings now that lines are assembled into paragraphs.
        # ``classify`` sees one line at a time and can only call a slugline on an
        # INT./EXT. prefix, so every sub-heading a writer drops mid-scene
        # ("DEEP SPACE", "IN THE WATER, NEAR THE CLIFFS - NIGHT") arrives here as
        # Action — and then leaks into the props pool downstream. Runs on the
        # assembled list so both to_container and to_fdx get the same result.
        if check_headings:
            report = repair_headings(paragraphs)
            if report.fixes:
                _log.info("PdfScreenplay: %s", report.summary())
        final_draft: Dict[str, Any] = {
            "@DocumentType": PdfScreenplay.DOCUMENT_TYPE,
            "@Version": PdfScreenplay.VERSION,
            "Content": {"Paragraph": paragraphs},
        }
        if watermarks:
            final_draft["Watermark"] = [w.to_fdx_attrs() for w in watermarks]
        return {"FinalDraft": final_draft}

    @staticmethod
    def detect_watermarks(
        pdf_path: str | Path, ignore_pages: Optional[List[int]] = None
    ) -> List[Watermark]:
        """Report the stamped overlay watermarks in the PDF without parsing the
        full script — for a pre-ingest UI check or ownership audit."""
        return extract_lines_and_watermarks(pdf_path, ignore_pages=ignore_pages)[1]

    @staticmethod
    def to_container(
        pdf_path: str | Path,
        ignore_pages: Optional[List[int]] = None,
        watermark: Optional[str] = None,
        ocr: bool = True,
        check_headings: bool = True,
    ) -> ScriptContainer:
        """Parse the PDF straight into a validated :class:`ScriptContainer` —
        identical in type to ``ScriptContainer.load(some.fdx)``."""
        fd = PdfScreenplay.to_finaldraft_dict(
            pdf_path,
            ignore_pages=ignore_pages,
            watermark=watermark,
            ocr=ocr,
            check_headings=check_headings,
        )
        container = ScriptContainer.model_validate(fd)
        container.source_path = Path(pdf_path)
        return container

    @staticmethod
    def to_fdx(
        pdf_path: str | Path,
        out_path: Optional[str | Path] = None,
        ignore_pages: Optional[List[int]] = None,
        watermark: Optional[str] = None,
        ocr: bool = True,
        check_headings: bool = True,
    ) -> Path:
        """Write a valid Final Draft ``.fdx`` beside the PDF (or at ``out_path``)
        and return its path. This is the standard, inspectable artifact every
        ingest leaves behind; downstream then treats it as any other .fdx. Any
        detected overlay watermarks are embedded as ``<Watermark>`` elements, so
        they persist on the .fdx (and into script.json) — no sidecar needed.

        ``check_headings`` runs the post-parse slugline sanity check (see
        :mod:`scriptbuddy.headings`); pass ``False`` to write exactly
        what the line classifier produced."""
        fd = PdfScreenplay.to_finaldraft_dict(
            pdf_path,
            ignore_pages=ignore_pages,
            watermark=watermark,
            ocr=ocr,
            check_headings=check_headings,
        )
        out = Path(out_path) if out_path else Path(pdf_path).with_suffix(".fdx")
        out.parent.mkdir(parents=True, exist_ok=True)
        xml = xmltodict.unparse(fd, pretty=True, full_document=True)
        out.write_text(xml, encoding="utf-8")
        n = len(fd["FinalDraft"]["Content"]["Paragraph"])
        _log.info("PdfScreenplay: wrote %d paragraphs → %s", n, out)
        return out


if __name__ == "__main__":
    import sys

    pdf = sys.argv[1]
    for w in PdfScreenplay.detect_watermarks(pdf):
        print("WATERMARK:", w.to_dict())
    container = PdfScreenplay.to_container(pdf)
    container.final_draft.print_script(limit=40)
    out = PdfScreenplay.to_fdx(pdf)
    print(f".fdx written -> {out}")
