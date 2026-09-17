"""Screen-time estimation, and the thing that was quietly broken: the config drives the parse.

``ScriptTimingConfig`` and ``block_duration`` were exported and documented as the place to
tune pacing while ``to_script_model`` computed its own durations from hardcoded divisors.
Tuning the config changed nothing. The last test in this file is the one that would have
caught it, so it is the one worth keeping.
"""

from __future__ import annotations

import textwrap

import pytest

from scriptbuddy import ScriptTimingConfig, block_duration, frames_from_duration
from scriptbuddy.fdx import ScriptContainer

FDX = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="UTF-8"?>
    <FinalDraft DocumentType="Script" Version="3">
      <Content>
        <Paragraph Type="Scene Heading"><Text>INT. PLAZA - DAY</Text></Paragraph>
        <Paragraph Type="Action"><Text>one two three four five six</Text></Paragraph>
        <Paragraph Type="Character"><Text>ELOISE</Text></Paragraph>
        <Paragraph Type="Parenthetical"><Text>(delighted)</Text></Paragraph>
        <Paragraph Type="Dialogue"><Text>one two three four</Text></Paragraph>
        <Paragraph Type="Transition"><Text>SMASH CUT TO</Text></Paragraph>
      </Content>
    </FinalDraft>
    """
)


@pytest.fixture
def fdx_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("timing") / "t.fdx"
    path.write_text(FDX, encoding="utf-8")
    return path


def parse(path, timing=None):
    return ScriptContainer.load(path).to_script_model(project_id="t", timing=timing)


def blocks_by_type(script):
    return {b.block_type.value: b for scene in script.scenes for b in scene.blocks}


# ---- the rules ------------------------------------------------------------------------


def test_a_character_cue_is_not_screen_time():
    """The name above the line costs nothing; the dialogue under it carries the seconds."""
    assert block_duration("character", "ELOISE") == 0.0
    assert block_duration("character", "A VERY LONG CHARACTER NAME INDEED") == 0.0


def test_dialogue_and_action_take_their_own_rates():
    cfg = ScriptTimingConfig()
    assert block_duration("dialogue", "one two three four") == round(4 * cfg.dialogue_word_multiplier, 2)
    assert block_duration("action", "one two three four") == round(4 * cfg.action_word_multiplier, 2)
    # Dialogue is the slower of the two: prose is read faster than it is played.
    assert cfg.dialogue_word_multiplier > cfg.action_word_multiplier


def test_a_transition_is_flat_rather_than_per_word():
    cfg = ScriptTimingConfig()
    assert block_duration("transition", "CUT TO") == cfg.transition_default
    assert block_duration("transition", "SMASH CUT TO BLACK AND THEN SOME") == cfg.transition_default


def test_everything_else_falls_to_the_action_rate_rather_than_a_rule_nobody_chose():
    cfg = ScriptTimingConfig()
    expected = round(2 * cfg.action_word_multiplier, 2)
    for kind in ("parenthetical", "scene_heading", "structural", "new_act", "zorp"):
        assert block_duration(kind, "two words") == expected, kind


def test_the_pads_are_off_by_default_and_honoured_when_set():
    assert block_duration("dialogue", "one two") == round(2 * 0.55, 2)  # no pad in the default

    padded = ScriptTimingConfig(dialogue_base_pad=1.0, parenthetical_pause_weight=0.5,
                                ellipsis_pause_weight=0.3, exclamation_emphasis_weight=0.2)
    assert block_duration("dialogue", "one two", config=padded) == round(1.0 + 2 * 0.55, 2)
    assert block_duration("dialogue", "one two!", config=padded) == round(1.0 + 2 * 0.55 + 0.2, 2)
    assert block_duration("dialogue", "one two...", config=padded) == round(1.0 + 2 * 0.55 + 0.3, 2)
    assert block_duration("dialogue", "one two", parenthetical=True, config=padded) == round(
        1.0 + 2 * 0.55 + 0.5, 2
    )


def test_an_action_floor_applies_only_when_it_binds():
    floored = ScriptTimingConfig(action_base_min=1.0)
    assert block_duration("action", "one two", config=floored) == 1.0          # floor wins
    assert block_duration("action", " ".join(["w"] * 40), config=floored) == 4.0  # rate wins


def test_frames_follow_the_configured_rate():
    assert frames_from_duration(2.0) == 48                        # 24 fps default
    assert frames_from_duration(2.0, fps=30) == 60
    assert frames_from_duration(2.0, ScriptTimingConfig(fps=25)) == 50


# ---- the regression: the config has to reach the parse ---------------------------------


def test_the_parse_uses_block_duration_for_every_block(fdx_path):
    blocks = blocks_by_type(parse(fdx_path))
    cfg = ScriptTimingConfig()
    assert blocks["character"].duration_secs == 0.0
    assert blocks["transition"].duration_secs == cfg.transition_default
    assert blocks["dialogue"].duration_secs == round(4 * cfg.dialogue_word_multiplier, 2)
    assert blocks["action"].duration_secs == round(6 * cfg.action_word_multiplier, 2)


def test_tuning_the_config_retimes_the_script(fdx_path):
    """The whole point of the profile — and exactly what used to do nothing."""
    default = parse(fdx_path)
    slower = parse(fdx_path, ScriptTimingConfig(dialogue_word_multiplier=1.1, action_word_multiplier=0.2))

    assert slower.scenes[0].duration_secs > default.scenes[0].duration_secs
    assert blocks_by_type(slower)["dialogue"].duration_secs == round(4 * 1.1, 2)
    assert blocks_by_type(slower)["action"].duration_secs == round(6 * 0.2, 2)
    # A cue stays free however the profile is tuned.
    assert blocks_by_type(slower)["character"].duration_secs == 0.0


def test_a_scene_is_the_sum_of_its_blocks(fdx_path):
    script = parse(fdx_path)
    for scene in script.scenes:
        assert scene.duration_secs == sum(b.duration_secs for b in scene.blocks)
        assert scene.duration_mins == scene.duration_secs / 60.0


def test_merged_dialogue_is_retimed_through_the_config_too(tmp_path):
    """Final Draft splits a speech on hard returns; the merged block is timed as one line."""
    split = FDX.replace(
        '<Paragraph Type="Dialogue"><Text>one two three four</Text></Paragraph>',
        '<Paragraph Type="Dialogue"><Text>one two</Text></Paragraph>\n'
        '    <Paragraph Type="Dialogue"><Text>three four</Text></Paragraph>',
    )
    path = tmp_path / "split.fdx"
    path.write_text(split, encoding="utf-8")
    cfg = ScriptTimingConfig(dialogue_word_multiplier=0.9)
    script = ScriptContainer.load(path).to_script_model(project_id="t", timing=cfg)
    merged = blocks_by_type(script)["dialogue"]
    assert merged.text == "one two three four"
    assert merged.duration_secs == round(4 * 0.9, 2)
