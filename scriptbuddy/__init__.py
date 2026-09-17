"""scriptbuddy - screenplay parsing and breakdown.

Read a Final Draft ``.fdx`` or a screenplay PDF into one normalized model,
estimate screen time per beat, and pull out the breakdown every department
asks for: cast, locations, props, sounds, editorial markers - per scene, with
screen time.

    >>> from scriptbuddy import load, Breakdown
    >>> script = load("my_script.pdf")        # .fdx or .pdf
    >>> bd = Breakdown.from_script(script)
    >>> print(bd.summary())
    >>> bd.to_excel("my_script_breakdown.xlsx")
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from scriptbuddy.block import ScriptBlock, ScriptBlockType
from scriptbuddy.breakdown import Breakdown, Entity, SceneRow
from scriptbuddy.classify import EntityClassifier, TypeSuggestion
from scriptbuddy.fdx import FinalDraft, Paragraph, ParagraphType, ScriptContainer, Watermark
from scriptbuddy.headings import repair_fdx_file, repair_headings
from scriptbuddy.layout import ElementLayout, ScreenplayStyle
from scriptbuddy.script import ScriptModel, ScriptScene
from scriptbuddy.timing import ScriptTimingConfig, block_duration, frames_from_duration

__version__ = "0.1.0"

__all__ = [
    "load",
    "load_container",
    "Breakdown",
    "Entity",
    "SceneRow",
    "ScriptModel",
    "ScriptScene",
    "ScriptBlock",
    "ScriptBlockType",
    "ScriptContainer",
    "FinalDraft",
    "Paragraph",
    "ParagraphType",
    "Watermark",
    "EntityClassifier",
    "TypeSuggestion",
    "repair_headings",
    "repair_fdx_file",
    "ElementLayout",
    "ScreenplayStyle",
    "ScriptTimingConfig",
    "block_duration",
    "frames_from_duration",
]


def load_container(path: str | Path, **pdf_options) -> ScriptContainer:
    """Read a ``.fdx`` or ``.pdf`` into a :class:`ScriptContainer` - the Final
    Draft document model. ``pdf_options`` (``ocr``, ``watermark``,
    ``ignore_pages``, ``check_headings``) are passed through to the PDF adapter
    and ignored for an ``.fdx``."""
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        from scriptbuddy.pdf import PdfScreenplay

        return PdfScreenplay.to_container(p, **pdf_options)
    return ScriptContainer.load(p)


def load(
    path: str | Path,
    title: Optional[str] = None,
    discover: bool = True,
    timing: Optional[ScriptTimingConfig] = None,
    **pdf_options,
) -> ScriptModel:
    """Read a ``.fdx`` or ``.pdf`` straight into a :class:`ScriptModel`. With
    ``discover`` (the default) the all-caps entity pass runs so every scene's
    prop / sound / editorial / action pools are populated.

    ``timing`` is the pacing profile screen time is measured with; omitted, it is the
    fitted default (:mod:`scriptbuddy.timing`)."""
    p = Path(path)
    model = load_container(p, **pdf_options).to_script_model(
        project_id=p.stem, title=title or p.stem, timing=timing
    )
    if discover:
        model.discover_all_assets()
    return model
