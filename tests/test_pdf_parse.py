"""Tests for screenplay PDF parsing (``scriptbuddy.pdf``).

These are hermetic: we synthesize screenplay PDFs at known Final-Draft margins
with PyMuPDF, so the suite needs no checked-in binaries. The contract under test
is that a PDF is classified into the *same* ``FinalDraft`` model an .fdx parses
into, and therefore flows through the identical downstream pipeline — so the key
assertions are (1) margin-driven classification, (2) round-trip through
``ScriptContainer.load`` of the written .fdx, and (3) PDF↔FDX parity.

A final, opt-in test exercises the parser against real local scripts (the clean
Tarnation trailer and the scanned Incredibles) when they happen to be present on
the dev machine; it is skipped in CI / on other machines.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import List, Tuple

import fitz  # PyMuPDF
import pytest

from scriptbuddy.fdx import ScriptContainer
from scriptbuddy.block import ScriptBlockType
from scriptbuddy.pdf import (
    PdfScreenplay,
    calibrate_bands,
    classify,
    extract_lines,
    extract_lines_and_watermarks,
    detect_title_page,
)

# Standard US-Letter Final Draft left margins (points).
PAGE_W, PAGE_H = 612, 792
X_ACTION = 108
X_DIALOGUE = 180
X_PAREN = 216
X_CHARACTER = 252
X_TRANSITION = 430
LINE_STEP = 14.0


# ---------------------------------------------------------------------------
# Synthetic PDF builder
# ---------------------------------------------------------------------------
# A "draw op" is (x, text, gap_before_in_line_steps). gap_before lets us inject
# the wider vertical space PDFs use to mark a paragraph break (no blank line is
# emitted — that's exactly the real-world condition the parser must handle).
DrawOp = Tuple[int, str, float]


def _render_page(page, ops: List[DrawOp], start_y: float = 72.0) -> None:
    y = start_y
    for x, text, gap in ops:
        y += LINE_STEP * gap
        page.insert_text((x, y), text, fontsize=12, fontname="cour")
        y += LINE_STEP


def build_pdf(path: Path, pages: List[List[DrawOp]]) -> Path:
    doc = fitz.open()
    for ops in pages:
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        _render_page(page, ops)
    doc.save(str(path))
    doc.close()
    return path


# A title page (front matter, no slugline) + two scenes covering every type.
TITLE_PAGE: List[DrawOp] = [
    (250, "MY TEST SCRIPT", 6),
    (270, "Written by", 4),
    (260, "A. Writer", 2),
]
BODY_PAGE: List[DrawOp] = [
    (X_ACTION, "INT. KITCHEN - DAY", 0),
    (X_ACTION, "A child enters the room holding a RED BALL.", 1),
    (X_CHARACTER, "KID", 1),
    (X_PAREN, "(excited)", 0),
    (X_DIALOGUE, "Hello, world. This is my very", 0),
    (X_DIALOGUE, "first spoken line of dialogue.", 0),
    (X_ACTION, "The kid runs out of the room.", 1),
    (X_ACTION, "A separate beat happens here later.", 2),  # wide gap → new action block
    (X_TRANSITION, "CUT TO:", 1),
    (X_ACTION, "EXT. PARK - NIGHT", 1),
    (X_CHARACTER, "NARRATOR", 1),
    (X_DIALOGUE, "The story begins now.", 0),
]


# A single token so it stays one span and on-page at a large size (a longer
# phrase overflows the page width and fitz clips/splits it — a fixture quirk, not
# a parser one; the real IMMT test covers the multi-phrase case).
_WATERMARK_TEXT = "CONFIDENTIAL"


def build_watermarked_pdf(path: Path, pages: List[List[DrawOp]]) -> Path:
    """Same body, but with a big overlay watermark stamped over each page — and
    deliberately placed in the dialogue column / body y-band to prove that a
    watermark word sharing a baseline with real text is still removed cleanly."""
    doc = fitz.open()
    for ops in pages:
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        _render_page(page, ops)
        page.insert_text(
            (X_DIALOGUE, 264), _WATERMARK_TEXT, fontsize=48, fontname="helv"
        )
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture(scope="module")
def script_pdf(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("pdf") / "test_script.pdf"
    return build_pdf(p, [TITLE_PAGE, BODY_PAGE])


@pytest.fixture(scope="module")
def watermarked_pdf(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("wm") / "wm_script.pdf"
    return build_watermarked_pdf(p, [BODY_PAGE])


@pytest.fixture(scope="module")
def container(script_pdf) -> ScriptContainer:
    return PdfScreenplay.to_container(script_pdf, ocr=False)


# ---------------------------------------------------------------------------
# Extraction & calibration
# ---------------------------------------------------------------------------
def test_title_page_detected_and_dropped(script_pdf):
    lines = extract_lines(script_pdf)
    assert detect_title_page(lines) is True


def test_bands_calibrate_to_standard_margins(script_pdf):
    lines = [ln for ln in extract_lines(script_pdf) if ln.page != 0]
    bands = calibrate_bands(lines)
    # Action anchor near 108; thresholds fall between the real columns.
    assert abs(bands.action_x - X_ACTION) <= 6
    assert X_ACTION < bands.dialogue_min < X_DIALOGUE
    assert X_DIALOGUE < bands.character_min < X_CHARACTER


def test_classify_by_margin(script_pdf):
    lines = [ln for ln in extract_lines(script_pdf) if ln.page != 0]
    bands = calibrate_bands(lines)
    by_text = {}
    prev = None
    for ln in lines:
        c = classify(ln, bands, prev)
        if c != "__skip__":
            prev = c
        by_text[ln.text] = c
    assert by_text["INT. KITCHEN - DAY"] == "Scene Heading"
    assert by_text["A child enters the room holding a RED BALL."] == "Action"
    assert by_text["KID"] == "Character"
    assert by_text["(excited)"] == "Parenthetical"
    assert by_text["The story begins now."] == "Dialogue"
    assert by_text["CUT TO:"] == "Transition"


# ---------------------------------------------------------------------------
# Paragraph assembly into the FinalDraft model
# ---------------------------------------------------------------------------
def test_two_scenes_parsed(container):
    headings = [
        p.clean_text
        for p in container.final_draft.content.paragraphs
        if p.paragraph_type == "Scene Heading"
    ]
    assert headings == ["INT. KITCHEN - DAY", "EXT. PARK - NIGHT"]


def test_wrapped_dialogue_merges_to_one_paragraph(container):
    dialogues = [
        p.clean_text
        for p in container.final_draft.content.paragraphs
        if p.paragraph_type == "Dialogue"
    ]
    # The two visual dialogue lines under KID collapse into a single speech.
    assert "Hello, world. This is my very first spoken line of dialogue." in dialogues


def test_action_paragraph_split_on_vertical_gap(container):
    actions = [
        p.clean_text
        for p in container.final_draft.content.paragraphs
        if p.paragraph_type == "Action"
    ]
    # The wide gap keeps these two beats as separate action paragraphs
    # (each becomes its own shot downstream).
    assert "The kid runs out of the room." in actions
    assert "A separate beat happens here later." in actions


# ---------------------------------------------------------------------------
# Downstream: ScriptModel
# ---------------------------------------------------------------------------
def test_to_script_model_structure(container):
    model = container.to_script_model("tst", "My Test Script", "000.000")
    assert len(model.scenes) == 2
    kitchen = model.scenes[0]
    assert kitchen.location == "KITCHEN"
    assert (
        kitchen.heading == "INT. KITCHEN - DAY"
    )  # heading is a scene attr, not a block
    assert "KID" in kitchen.speakers
    types = {b.block_type for b in kitchen.blocks}
    assert ScriptBlockType.ACTION in types
    assert ScriptBlockType.DIALOGUE in types
    assert ScriptBlockType.CHARACTER in types


def test_dialogue_attribution(container):
    lines = container.dialogue_lines()
    speakers = {ln["speaker"] for ln in lines}
    assert "KID" in speakers
    assert "NARRATOR" in speakers


# ---------------------------------------------------------------------------
# Written .fdx round-trips through the SAME loader a native .fdx uses
# ---------------------------------------------------------------------------
def test_fdx_artifact_round_trips(script_pdf, tmp_path):
    out = tmp_path / "out.fdx"
    PdfScreenplay.to_fdx(script_pdf, out_path=out, ocr=False)
    assert out.exists()
    reloaded = ScriptContainer.load(out)  # native .fdx code path
    direct = PdfScreenplay.to_container(script_pdf, ocr=False)
    # The serialized-then-reloaded model matches the in-memory parse exactly.
    a = [
        (p.paragraph_type, p.clean_text)
        for p in reloaded.final_draft.content.paragraphs
    ]
    b = [
        (p.paragraph_type, p.clean_text) for p in direct.final_draft.content.paragraphs
    ]
    assert a == b


# ---------------------------------------------------------------------------
# PDF ↔ FDX parity: the same screenplay, two source formats, one ScriptModel
# ---------------------------------------------------------------------------
_EQUIVALENT_FDX = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="UTF-8"?>
    <FinalDraft DocumentType="Script" Version="5">
      <Content>
        <Paragraph Type="Scene Heading"><Text>INT. KITCHEN - DAY</Text></Paragraph>
        <Paragraph Type="Action"><Text>A child enters the room holding a RED BALL.</Text></Paragraph>
        <Paragraph Type="Character"><Text>KID</Text></Paragraph>
        <Paragraph Type="Parenthetical"><Text>(excited)</Text></Paragraph>
        <Paragraph Type="Dialogue"><Text>Hello, world. This is my very first spoken line of dialogue.</Text></Paragraph>
        <Paragraph Type="Action"><Text>The kid runs out of the room.</Text></Paragraph>
        <Paragraph Type="Action"><Text>A separate beat happens here later.</Text></Paragraph>
        <Paragraph Type="Transition"><Text>CUT TO:</Text></Paragraph>
        <Paragraph Type="Scene Heading"><Text>EXT. PARK - NIGHT</Text></Paragraph>
        <Paragraph Type="Character"><Text>NARRATOR</Text></Paragraph>
        <Paragraph Type="Dialogue"><Text>The story begins now.</Text></Paragraph>
      </Content>
    </FinalDraft>
    """
)


def _projection(model) -> list:
    """A format-agnostic view of a ScriptModel: per scene, its location and the
    ordered (block_type, speaker) beats. The right level to compare two source
    formats — exact paragraph ids/durations are not the contract; structure is."""
    out = []
    for sc in model.scenes:
        out.append(
            (
                sc.location,
                [(b.block_type.value, b.speaker) for b in sc.blocks],
            )
        )
    return out


def test_pdf_matches_equivalent_fdx(script_pdf, tmp_path):
    fdx_path = tmp_path / "equiv.fdx"
    fdx_path.write_text(_EQUIVALENT_FDX, encoding="utf-8")

    from_fdx = ScriptContainer.load(fdx_path).to_script_model("p", "t", "000.000")
    from_pdf = PdfScreenplay.to_container(script_pdf, ocr=False).to_script_model(
        "p", "t", "000.000"
    )
    assert _projection(from_pdf) == _projection(from_fdx)


# ---------------------------------------------------------------------------
# Watermark detection & stripping
# ---------------------------------------------------------------------------
def test_watermark_detected(watermarked_pdf):
    wms = PdfScreenplay.detect_watermarks(watermarked_pdf)
    assert any(_WATERMARK_TEXT in w.text for w in wms)
    wm = next(w for w in wms if _WATERMARK_TEXT in w.text)
    assert wm.size > 40  # the oversized overlay, not the 12pt body
    assert wm.count >= 1


def test_watermark_stripped_from_content(watermarked_pdf):
    # The overlay was placed in the dialogue column on a body baseline; it must
    # not survive anywhere in the parsed paragraphs...
    container = PdfScreenplay.to_container(watermarked_pdf, ocr=False)
    paras = [p.clean_text for p in container.final_draft.content.paragraphs]
    assert all(_WATERMARK_TEXT not in t for t in paras)
    # ...and the real script must still parse intact underneath it.
    assert "The story begins now." in paras
    assert "INT. KITCHEN - DAY" in paras


def test_watermark_does_not_corrupt_shared_baseline_line(watermarked_pdf):
    # A body line that shares the watermark's baseline stays clean (proves span-
    # level filtering happens BEFORE baseline grouping).
    lines, _ = extract_lines_and_watermarks(watermarked_pdf)
    assert all(_WATERMARK_TEXT not in ln.text for ln in lines)


def test_watermark_stored_on_fdx_model(watermarked_pdf):
    # The watermark is carried on the FinalDraft model itself...
    container = PdfScreenplay.to_container(watermarked_pdf, ocr=False)
    wm_texts = [w.text for w in container.final_draft.watermarks]
    assert any(_WATERMARK_TEXT in t for t in wm_texts)


def test_watermark_flows_into_script_model(watermarked_pdf):
    # ...and propagates onto the ScriptModel (which serializes to script.json).
    model = PdfScreenplay.to_container(watermarked_pdf, ocr=False).to_script_model(
        "wm", "WM", "000.000"
    )
    assert any(_WATERMARK_TEXT in w.text for w in model.watermarks)
    dumped = json.loads(model.model_dump_json())
    assert any(_WATERMARK_TEXT in w["text"] for w in dumped["watermarks"])


def test_watermark_round_trips_through_fdx(watermarked_pdf, tmp_path):
    # Embedded as a <Watermark> element, it survives write + native .fdx reload —
    # and no sidecar file is produced.
    out = tmp_path / "wm.fdx"
    PdfScreenplay.to_fdx(watermarked_pdf, out_path=out, ocr=False)
    assert not out.with_suffix(".watermarks.json").exists()
    reloaded = ScriptContainer.load(out)
    assert any(_WATERMARK_TEXT in w.text for w in reloaded.final_draft.watermarks)


def test_clean_script_has_no_watermarks(script_pdf):
    container = PdfScreenplay.to_container(script_pdf, ocr=False)
    assert container.final_draft.watermarks == []


# ---------------------------------------------------------------------------
# Opt-in: real local scripts (skipped when absent)
# ---------------------------------------------------------------------------
_TARNATION = Path.home() / "Downloads/tarnation/Tarnation Narrator Trailer REV.pdf"
_INCREDIBLES = Path.home() / "Downloads/the-incredibles-2004.pdf"
_1776 = Path.home() / "Documents/StoryForge/1776/1776_script.pdf"
_IMMT = Path.home() / "Downloads/2026_07_03/2026_07_03/IMMT Scriptment v6_TM.pdf"


@pytest.mark.skipif(not _IMMT.exists(), reason="local IMMT watermarked PDF absent")
def test_real_watermarked_pdf_immt():
    """A real 55-page script with an oversized 'Property of …' ownership overlay
    on every page (font size far above the 12pt body)."""
    wms = PdfScreenplay.detect_watermarks(_IMMT)
    texts = " | ".join(w.text for w in wms)
    assert "Base Media" in texts  # the ownership stamp is captured for provenance

    model = PdfScreenplay.to_container(_IMMT, ocr=False).to_script_model(
        "immt", "IMMT", "000.000"
    )
    # The watermark must not leak into any block text or speaker field.
    for scene in model.scenes:
        for b in scene.blocks:
            assert "Base Media" not in (b.text or "")
            assert "Mikota" not in (b.speaker or "")
    assert len(model.scenes) >= 30


@pytest.mark.skipif(not _TARNATION.exists(), reason="local Tarnation PDF absent")
def test_real_clean_pdf_tarnation():
    model = PdfScreenplay.to_container(_TARNATION, ocr=False).to_script_model(
        "tnt", "Tarnation", "000.000"
    )
    assert len(model.scenes) == 1
    speakers = {b.speaker for s in model.scenes for b in s.blocks if b.speaker}
    assert {"NARRATOR", "SKEETER", "DODGE"} <= speakers


@pytest.mark.skipif(not _1776.exists(), reason="local 1776 PDF absent")
def test_real_clean_pdf_1776():
    """A long, structurally varied clean script (117 pp, 100+ scenes) — exercises
    far more scene-heading / cue variety than the Tarnation teaser, with a clean
    cast (no OCR noise), so it can assert exactness."""
    from collections import Counter

    model = PdfScreenplay.to_container(_1776, ocr=False).to_script_model(
        "1776", "1776", "000.000"
    )
    # Many scenes resolve; allow drift but pin the order of magnitude.
    assert len(model.scenes) >= 100
    counts = Counter(b.speaker for s in model.scenes for b in s.blocks if b.speaker)
    # Clean digital source → a small, real cast (not a long OCR-noise tail).
    assert len(counts) <= 70
    top = {name for name, _ in counts.most_common(6)}
    assert {"PHIN", "BLAIR", "WASHINGTON"} <= top


@pytest.mark.skipif(not _INCREDIBLES.exists(), reason="local Incredibles PDF absent")
def test_real_scanned_pdf_incredibles():
    from collections import Counter

    model = PdfScreenplay.to_container(_INCREDIBLES, ocr=False).to_script_model(
        "inc", "The Incredibles", "000.000"
    )
    assert len(model.scenes) >= 30
    counts = Counter(b.speaker for s in model.scenes for b in s.blocks if b.speaker)
    # The principal cast should dominate the dialogue line counts even through
    # a noisy scan (a robustness, not exactness, assertion).
    top = {name for name, _ in counts.most_common(8)}
    assert len({"BOB", "HELEN", "DASH", "VIOLET"} & top) >= 3
