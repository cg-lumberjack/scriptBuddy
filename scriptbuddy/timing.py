"""Screen-time estimation: how long a block plays, from its words and shape.

:func:`block_duration` is the **only** place a duration is decided. The parse calls it
for every block (``ScriptContainer.to_script_model``), so tuning
:class:`ScriptTimingConfig` changes what a script measures — which is the whole point of
the multipliers living on a model rather than in the walk.

Where the defaults come from
----------------------------

Fitted against two feature scripts with known runtimes, solved together:

====================  =========  ===========  ===============  ==========
script                dialogue   action-ish   action/dialogue  theatrical
====================  =========  ===========  ===============  ==========
The Incredibles        8,734 w     17,271 w        1.98          115 min
Barbie                10,071 w      9,815 w        0.97          114 min
====================  =========  ===========  ===============  ==========

Two films a minute apart in reality, with opposite prose habits, are what make the fit
mean anything: a rate pair that suits only one of them is fitting a writer's style
rather than screen time. At these defaults both land near 108.7 min, against roughly
107 min of feature once end credits come off. The previous hardcoded rates put the same
two films 22 minutes apart.

It is an exact fit — two equations, two unknowns — so it is a fit and not yet a
validation. A third script with a known runtime would make it one.

**A note on the pads.** ``dialogue_base_pad``, ``action_base_min`` and the three
punctuation weights all default to zero. They are real knobs and the rules honour them,
but the fitted per-word rates already absorb the average cost of a beat, a pause and an
exclamation across thousands of blocks — switching one on adds time on top of a rate
that already counted it. Raise them when tuning a show against its own cut, not to make
one script's number look better.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ScriptTimingConfig(BaseModel):
    """Typographic-to-temporal multipliers: what a word of each kind costs in screen time.

    A production tunes pacing here — a fast-talking comedy against a slow drama — and
    hands the result to the parse. See the module docstring for where the defaults come
    from, and why the pads start at zero.
    """

    fps: float = 24.0

    # Pacing multipliers (seconds per word), fitted — see the module docstring.
    dialogue_word_multiplier: float = Field(
        default=0.55,
        description="Seconds per word of dialogue. 0.55 is ~109 wpm: screen delivery, "
                    "which is slower than conversation.",
    )
    action_word_multiplier: float = Field(
        default=0.10,
        description="Seconds per word of action, and of every other described block. "
                    "Prose is read far faster than it is played.",
    )
    transition_default: float = Field(
        default=1.0,
        description="Flat structural time for a transition; its words are not played.",
    )

    # Pads and minimums. Zero by default: the rates above already carry the average.
    dialogue_base_pad: float = Field(
        default=0.0, description="Seconds added to any dialogue block, before its words."
    )
    action_base_min: float = Field(
        default=0.0, description="The floor under an action block, however few words it has."
    )

    # Punctuation and performance modifiers (seconds added to a dialogue block).
    ellipsis_pause_weight: float = Field(
        default=0.0, description="Beat added when a line carries a trailing ellipsis."
    )
    exclamation_emphasis_weight: float = Field(
        default=0.0, description="Emphasis added when a line carries an exclamation mark."
    )
    parenthetical_pause_weight: float = Field(
        default=0.0, description="Acting beat added when a line is played under a parenthetical."
    )


def block_duration(
    block_type: str,
    text: str,
    parenthetical: bool = False,
    config: Optional[ScriptTimingConfig] = None,
) -> float:
    """Estimated screen seconds for one block. The single source of every duration.

    A **character cue** is not screen time — it is the name above the line, and the
    dialogue that follows carries the seconds — so it returns zero. A **transition** is
    structural and gets a flat cost rather than one per word. Everything that is neither
    dialogue nor one of those is described prose, read far faster than it plays, and
    takes the action rate: deliberately including parentheticals, scene headings and any
    custom element type, so that no block falls through to a rule nobody chose.
    """
    cfg = config or ScriptTimingConfig()
    clean = text.strip() if text else ""
    words = len(clean.split())

    if block_type == "character":
        return 0.0

    if block_type == "transition":
        return round(cfg.transition_default, 2)

    if block_type == "dialogue":
        duration = cfg.dialogue_base_pad + cfg.dialogue_word_multiplier * words
        if "..." in clean:
            duration += cfg.ellipsis_pause_weight
        if "!" in clean:
            duration += cfg.exclamation_emphasis_weight
        if parenthetical:
            duration += cfg.parenthetical_pause_weight
        return round(duration, 2)

    return round(max(cfg.action_base_min, cfg.action_word_multiplier * words), 2)


def frames_from_duration(duration_secs: float, scr_config=None, fps=None):
    """Whole frames a duration occupies, at ``fps`` or the config's own."""
    cfg = scr_config or ScriptTimingConfig()
    return int(duration_secs * (fps or cfg.fps))
