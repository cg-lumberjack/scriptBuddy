"""Tests for FDX parsing with custom / non-standard paragraph element types."""

from __future__ import annotations

import logging
import textwrap

import pytest

from scriptbuddy.fdx import (
    ScriptContainer,
    ParagraphType,
    _KNOWN_TYPES,
)
from scriptbuddy.block import ScriptBlockType

# ---------------------------------------------------------------------------
# Minimal FDX fixture covering all required cases
# ---------------------------------------------------------------------------
_MULTI_EPISODE_FDX = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="UTF-8"?>
    <FinalDraft DocumentType="Script" Version="3">
      <ElementSettings Type="Scene Heading"/>
      <ElementSettings Type="Action"/>
      <ElementSettings Type="Character"/>
      <ElementSettings Type="Dialogue"/>
      <ElementSettings Type="Parenthetical"/>
      <ElementSettings Type="Transition"/>
      <ElementSettings Type="Shot"/>
      <ElementSettings Type="General"/>
      <ElementSettings Type="Episode"/>
      <ElementSettings Type="New Act"/>
      <ElementSettings Type="End of Act"/>
      <ElementSettings Type="Zorp"/>
      <Content>
        <Paragraph Type="Episode">
          <Text>Episode 31</Text>
        </Paragraph>
        <Paragraph Type="Scene Heading">
          <Text>INT. HOUSE - DAY</Text>
        </Paragraph>
        <Paragraph Type="Action">
          <Text>A child enters the room.</Text>
        </Paragraph>
        <Paragraph Type="Character">
          <Text>KID</Text>
        </Paragraph>
        <Paragraph Type="Dialogue">
          <Text>Hello, world.</Text>
        </Paragraph>
        <Paragraph Type="New Act">
          <Text>Act three</Text>
        </Paragraph>
        <Paragraph Type="End of Act">
          <Text Style="Underline+AllCaps">EPISODE 31</Text>
        </Paragraph>
        <Paragraph Type="Zorp">
          <Text>Unknown custom element type</Text>
        </Paragraph>
      </Content>
    </FinalDraft>
"""
)


@pytest.fixture(scope="module")
def container(tmp_path_factory) -> ScriptContainer:
    """Parse the multi-episode FDX fixture into a ScriptContainer."""
    p = tmp_path_factory.mktemp("fdx") / "test.fdx"
    p.write_text(_MULTI_EPISODE_FDX, encoding="utf-8")
    return ScriptContainer.load(p)


# ---------------------------------------------------------------------------
# 1. Parsing succeeds without errors
# ---------------------------------------------------------------------------
def test_parse_succeeds(container):
    assert container is not None
    assert len(container.final_draft.content.paragraphs) == 8


# ---------------------------------------------------------------------------
# 2. Standard paragraph is unaffected
# ---------------------------------------------------------------------------
def test_standard_action_paragraph(container):
    action = container.final_draft.content.paragraphs[2]
    assert action.paragraph_type == ParagraphType.ACTION
    assert action.clean_text == "A child enters the room."


def test_standard_dialogue_paragraph(container):
    dlg = container.final_draft.content.paragraphs[4]
    assert dlg.paragraph_type == ParagraphType.DIALOGUE
    assert dlg.clean_text == "Hello, world."


# ---------------------------------------------------------------------------
# 3. First-class structural types round-trip intact
# ---------------------------------------------------------------------------
def test_episode_paragraph(container):
    ep = container.final_draft.content.paragraphs[0]
    assert ep.paragraph_type == "Episode"
    assert ep.paragraph_type == ParagraphType.EPISODE
    assert ep.clean_text == "Episode 31"


def test_new_act_paragraph(container):
    na = container.final_draft.content.paragraphs[5]
    assert na.paragraph_type == ParagraphType.NEW_ACT
    assert na.clean_text == "Act three"


def test_end_of_act_paragraph(container):
    eoa = container.final_draft.content.paragraphs[6]
    assert eoa.paragraph_type == ParagraphType.END_OF_ACT
    assert eoa.clean_text == "EPISODE 31"


# ---------------------------------------------------------------------------
# 4. Styling is preserved on text runs
# ---------------------------------------------------------------------------
def test_end_of_act_style_preserved(container):
    eoa = container.final_draft.content.paragraphs[6]
    assert len(eoa.text_elements) == 1
    assert eoa.text_elements[0].style == "Underline+AllCaps"


def test_unstyled_text_has_none_style(container):
    action = container.final_draft.content.paragraphs[2]
    assert action.text_elements[0].style is None


# ---------------------------------------------------------------------------
# 5. Unknown custom type passes through; warning is logged
# ---------------------------------------------------------------------------
def test_unknown_type_passes_through(container):
    zorp = container.final_draft.content.paragraphs[7]
    assert zorp.paragraph_type == "Zorp"
    assert "Zorp" not in _KNOWN_TYPES


def test_unknown_type_logs_warning(tmp_path, caplog):
    fdx_path = tmp_path / "warn.fdx"
    fdx_path.write_text(
        textwrap.dedent(
            """\
            <?xml version="1.0" encoding="UTF-8"?>
            <FinalDraft DocumentType="Script" Version="3">
              <Content>
                <Paragraph Type="WeirdCustomType">
                  <Text>Something odd</Text>
                </Paragraph>
              </Content>
            </FinalDraft>
        """
        ),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="scriptbuddy.fdx"):
        c = ScriptContainer.load(fdx_path)

    assert any("WeirdCustomType" in r.message for r in caplog.records)
    assert c.final_draft.content.paragraphs[0].paragraph_type == "WeirdCustomType"


# ---------------------------------------------------------------------------
# 6. ElementSettings declares the custom types
# ---------------------------------------------------------------------------
def test_declared_element_types(container):
    types = container.final_draft.declared_element_types
    assert "Episode" in types
    assert "New Act" in types
    assert "End of Act" in types


def test_declared_custom_types(container):
    # Episode / New Act / End of Act are now first-class KNOWN types, so they
    # don't appear here. Only truly unrecognized types surfaced in ElementSettings do.
    custom = container.final_draft.declared_custom_types
    assert "Zorp" in custom
    assert "Episode" not in custom
    assert "New Act" not in custom
    assert "End of Act" not in custom


# ---------------------------------------------------------------------------
# 7. to_script_model maps structural types into ScriptBlockType
# ---------------------------------------------------------------------------
def test_to_script_model_structural_types(container):
    model = container.to_script_model(
        project_id="test", title="Test Script", version="001.000"
    )
    all_blocks = [b for scene in model.scenes for b in scene.blocks]
    block_types = {b.block_type for b in all_blocks}
    assert ScriptBlockType.EPISODE in block_types
    assert ScriptBlockType.NEW_ACT in block_types
    assert ScriptBlockType.END_OF_ACT in block_types


def test_to_script_model_unknown_type_becomes_structural(container):
    model = container.to_script_model(
        project_id="test", title="Test Script", version="001.000"
    )
    all_blocks = [b for scene in model.scenes for b in scene.blocks]
    assert any(b.block_type == ScriptBlockType.STRUCTURAL for b in all_blocks)


def test_to_script_model_standard_types_unchanged(container):
    model = container.to_script_model(
        project_id="test", title="Test Script", version="001.000"
    )
    all_blocks = [b for scene in model.scenes for b in scene.blocks]
    assert any(b.block_type == ScriptBlockType.ACTION for b in all_blocks)
    assert any(b.block_type == ScriptBlockType.DIALOGUE for b in all_blocks)
