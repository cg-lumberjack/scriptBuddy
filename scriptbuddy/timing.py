"""Screen-time estimation: how long a block plays, from its words and shape.

The multipliers live on :class:`ScriptTimingConfig` so a production can tune
pacing (a fast-talking comedy vs. a slow drama) without touching the rules.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ScriptTimingConfig(BaseModel):
    """
    Configuration profile tracking typographic-to-temporal multipliers
    used to calculate predictive screen durations for screenplay assets.
    """

    fps: float = 24.0
    # Base padding weights (Seconds)
    dialogue_base_pad: float = Field(
        default=1.0, description="Base padding added to any dialogue block."
    )
    action_base_min: float = Field(
        default=1.0, description="The absolute minimum duration for an action block."
    )
    transition_default: float = Field(
        default=1.0, description="Flat structural timing given to scene transitions."
    )
    fallback_default: float = Field(
        default=0.5, description="Fallback time for unidentified block structures."
    )

    # Pacing multipliers (Seconds per word)
    dialogue_word_multiplier: float = Field(
        default=0.5, description="Time allocated per word in dialogue strings."
    )
    action_word_multiplier: float = Field(
        default=0.3, description="Time allocated per word in action blocks."
    )

    # Punctuation & Performance Modifiers (Seconds added)
    ellipsis_pause_weight: float = Field(
        default=0.3,
        description="Beat pause added when trailing ellipses (...) are discovered.",
    )
    exclamation_emphasis_weight: float = Field(
        default=0.2, description="Emphasis timing added for exclamation marks (!)."
    )
    parenthetical_pause_weight: float = Field(
        default=0.5,
        description="Internal acting beat pause allocated for parentheticals.",
    )


def block_duration(
    block_type: str,
    text: str,
    parenthetical: bool = False,
    config: Optional[ScriptTimingConfig] = None,
) -> float:
    """
    Estimates the on-screen runtime duration (in seconds) of a screenplay block
    utilizing a configurable Pydantic multiplier matrix.
    """
    # Fallback to pristine schema defaults if no explicit config profile is passed downstream
    cfg = config or ScriptTimingConfig()

    clean_text = text.strip() if text else ""
    word_count = len(clean_text.split())

    if block_type == "dialogue":
        # Calculate base words pacing duration
        duration = cfg.dialogue_base_pad + (cfg.dialogue_word_multiplier * word_count)

        # Inject performance punctuation modifiers dynamically from schema traits
        if "..." in clean_text:
            duration += cfg.ellipsis_pause_weight
        if "!" in clean_text:
            duration += cfg.exclamation_emphasis_weight
        if parenthetical:
            duration += cfg.parenthetical_pause_weight

        return round(duration, 2)

    elif block_type == "action":
        calculated_action_time = cfg.action_word_multiplier * word_count
        return round(max(cfg.action_base_min, calculated_action_time), 2)

    elif block_type == "transition":
        return round(cfg.transition_default, 2)

    return round(cfg.fallback_default, 2)


def frames_from_duration(
    duration_secs: float, scr_config=ScriptTimingConfig(), fps=None
):
    if not fps:
        fps = scr_config.fps
    return int(duration_secs * fps)


