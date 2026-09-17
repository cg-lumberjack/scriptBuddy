from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field

from scriptbuddy.layout import ElementLayout, ScreenplayStyle


class ScriptBlockType(str, Enum):
    SCENE_HEADING = "scene_heading"
    ACTION = "action"
    DIALOGUE = "dialogue"
    # A visual-only block — carries a visual over a time span with no dialogue
    # (a music-only intro / instrumental break).
    VISUAL = "visual"
    PARENTHETICAL = "parenthetical"
    TRANSITION = "transition"
    CHARACTER = "character"
    # First-class structural types (multi-episode / act structure)
    EPISODE = "episode"
    NEW_ACT = "new_act"
    END_OF_ACT = "end_of_act"
    # Catch-all for any other unrecognized custom element type
    STRUCTURAL = "structural"


class ScriptBlock(BaseModel):
    """
    One typographic beat inside a scene: an action paragraph, a line of dialogue,
    a transition. The atomic unit every breakdown and timing pass works on.

    Extra keys are allowed so a ``script.json`` written by a downstream tool that
    subclasses this model (adding its own per-block fields) round-trips through
    here without loss.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    scene_id: str
    block_type: ScriptBlockType
    text: str
    location: Optional[str] = None
    speaker: Optional[str] = None
    parenthetical: Optional[str] = None
    characters: List[str] = Field(default_factory=list)
    props: List[str] = Field(default_factory=list)
    sounds: List[str] = Field(default_factory=list)
    editorials: List[str] = Field(default_factory=list)
    actions: List[str] = Field(default_factory=list)
    fx: List[str] = Field(default_factory=list)

    # Estimated screen time (see :mod:`scriptbuddy.timing`).
    duration_secs: float = 0.0
    duration_frames: int = 0
    notes: Optional[str] = None

    @computed_field
    @property
    def name(self) -> str:
        return f"{self.scene_id}_{self.id}"

    # -- layout -----------------------------------------------------------

    @property
    def layout(self) -> ElementLayout:
        style_sheet = ScreenplayStyle()
        type_str = (
            self.block_type.value
            if hasattr(self.block_type, "value")
            else str(self.block_type)
        )
        return style_sheet.get_layout(type_str)

    @property
    def display_text(self) -> str:
        """The block's text as it would sit on the page: upper-cased where the
        element type demands it, parentheticals wrapped in their parens."""
        if not self.text:
            return ""
        raw_text = self.text.strip().lstrip('\t" ').rstrip('\t" ')
        if self.layout.force_upper:
            raw_text = raw_text.upper()
        prefix = self.layout.prefix
        suffix = self.layout.suffix
        if prefix and not raw_text.startswith(prefix):
            raw_text = f"{prefix}{raw_text}"
        if suffix and not raw_text.endswith(suffix):
            raw_text = f"{raw_text}{suffix}"
        return raw_text
