"""The breakdown contract: parse -> discover -> classify -> rows.

The fixture is a tiny two-scene script written to exercise each routing rule
once: a speaking lead introduced all-caps by full name (name-part), a crowd
noun, a real prop, a sound, an editorial marker, and slugline debris.
"""

from __future__ import annotations

import textwrap

import pytest

from scriptbuddy import Breakdown, ScriptContainer, load
from scriptbuddy.breakdown import POOLS

_FDX = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="UTF-8"?>
    <FinalDraft DocumentType="Script" Version="3">
      <Content>
        <Paragraph Type="Scene Heading"><Text>EXT. BUNKER HILL - DAY</Text></Paragraph>
        <Paragraph Type="Action"><Text>GEORGE WASHINGTON, 44, rides in. A CANNON fires. BOOM. SOLDIERS scatter.</Text></Paragraph>
        <Paragraph Type="Character"><Text>WASHINGTON</Text></Paragraph>
        <Paragraph Type="Dialogue"><Text>Hold the line.</Text></Paragraph>
        <Paragraph Type="Character"><Text>WARREN</Text></Paragraph>
        <Paragraph Type="Dialogue"><Text>We hold.</Text></Paragraph>
        <Paragraph Type="Transition"><Text>CUT TO:</Text></Paragraph>
        <Paragraph Type="Scene Heading"><Text>INT. WAR ROOM - NIGHT</Text></Paragraph>
        <Paragraph Type="Action"><Text>Washington studies a MAP. GENERAL WARREN enters. LATER</Text></Paragraph>
        <Paragraph Type="Character"><Text>WASHINGTON</Text></Paragraph>
        <Paragraph Type="Dialogue"><Text>Show me the river.</Text></Paragraph>
      </Content>
    </FinalDraft>
    """
)


@pytest.fixture(scope="module")
def script(tmp_path_factory):
    p = tmp_path_factory.mktemp("bd") / "two_scenes.fdx"
    p.write_text(_FDX, encoding="utf-8")
    return load(p)


@pytest.fixture(scope="module")
def bd(script):
    return Breakdown.from_script(script, discover=False)


def _labels(bd, pool):
    return [e.label.upper() for e in bd.pool(pool)]


def test_scenes(bd):
    assert [s.scene_id for s in bd.scenes] == ["010", "020"]
    first = bd.scenes[0]
    assert first.scope == "EXT" and first.time == "DAY"
    assert first.location == "BUNKER HILL"
    assert first.cast == ["WARREN", "WASHINGTON"]
    assert first.screen_seconds > 0


def test_speakers_are_characters_with_dialogue_time(bd):
    chars = {e.label.upper(): e for e in bd.characters}
    assert "WASHINGTON" in chars and "WARREN" in chars
    assert chars["WASHINGTON"].scene_ids == ["010", "020"]
    assert chars["WASHINGTON"].mentions == 2  # two dialogue lines
    assert chars["WASHINGTON"].screen_seconds > chars["WARREN"].screen_seconds


def test_full_name_introduction_is_the_character_not_a_prop(bd):
    """'GEORGE WASHINGTON' in an action line is WASHINGTON's intro."""
    assert "GEORGE WASHINGTON" in _labels(bd, "characters")
    assert "GENERAL WARREN" in _labels(bd, "characters")
    assert "GEORGE WASHINGTON" not in _labels(bd, "props")
    ent = next(e for e in bd.characters if e.label.upper() == "GEORGE WASHINGTON")
    assert ent.rule == "name-part"
    assert "WASHINGTON" in ent.reason


def test_crowd_noun_is_cast(bd):
    assert "SOLDIERS" in _labels(bd, "characters")


def test_real_props_survive(bd):
    props = _labels(bd, "props")
    assert "CANNON" in props
    assert "MAP" in props


def test_sounds_and_editorials_route(bd):
    assert "BOOM" in _labels(bd, "sounds")
    assert "CUT TO" in _labels(bd, "editorials")
    assert "LATER" in _labels(bd, "editorials")


def test_locations_from_headings(bd):
    locs = _labels(bd, "locations")
    assert "BUNKER HILL" in locs and "WAR ROOM" in locs


def test_scene_anchored_screen_time(bd):
    cannon = next(e for e in bd.props if e.label.upper() == "CANNON")
    assert cannon.screen_seconds == pytest.approx(bd.scenes[0].screen_seconds)


def test_rows_and_dict(bd):
    d = bd.to_dict()
    assert set(d) == set(POOLS)
    assert d["scenes"][0]["Scene"] == "010"
    assert {"Label", "Scenes", "Mentions", "First Appearance", "Screen Time", "Why"} <= set(
        d["characters"][0]
    )


def test_classify_off_keeps_vocabulary_routing(script):
    raw = Breakdown.from_script(script, discover=False, classify=False)
    assert "GEORGE WASHINGTON" in _labels(raw, "props")


def test_excel_roundtrip(bd, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    out = bd.to_excel(tmp_path / "bd.xlsx")
    wb = openpyxl.load_workbook(out)
    assert "Scenes" in wb.sheetnames and "Characters" in wb.sheetnames
    header = [c.value for c in wb["Characters"][1]]
    assert header[0] == "Label"
    assert "Screen Seconds" not in header


def test_fdx_roundtrip_keeps_scenes(script, tmp_path):
    out = ScriptContainer.from_script_model(script).write(tmp_path / "rt.fdx")
    again = load(out)
    assert [s.heading for s in again.scenes] == [s.heading for s in script.scenes]
