from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ElementLayout(BaseModel):
    """
    Defines the layout constraints for a specific screenplay element type.
    """

    left_margin: int
    wrap_width: int
    force_upper: bool = False
    prefix: str = ""
    suffix: str = ""


class ScreenplayStyle(BaseModel):
    """
    A full layout profile sheet that holds the rules for every
    script element assignment type.
    """

    model_config = ConfigDict(populate_by_name=True)

    # Standard Hollywood margins as validated defaults
    scene_heading: ElementLayout = Field(
        default=ElementLayout(left_margin=10, wrap_width=60, force_upper=True)
    )
    action: ElementLayout = Field(
        default=ElementLayout(left_margin=10, wrap_width=60, force_upper=False)
    )
    character: ElementLayout = Field(
        default=ElementLayout(left_margin=35, wrap_width=40, force_upper=True)
    )
    parenthetical: ElementLayout = Field(
        default=ElementLayout(
            left_margin=25, wrap_width=30, force_upper=False, prefix="(", suffix=")"
        )
    )
    dialogue: ElementLayout = Field(
        default=ElementLayout(left_margin=22, wrap_width=35, force_upper=False)
    )
    transition: ElementLayout = Field(
        default=ElementLayout(left_margin=60, wrap_width=20, force_upper=True)
    )
    shot: ElementLayout = Field(
        default=ElementLayout(left_margin=10, wrap_width=60, force_upper=True)
    )
    general: ElementLayout = Field(
        default=ElementLayout(left_margin=10, wrap_width=60, force_upper=False)
    )

    def get_layout(self, paragraph_type_str: str) -> ElementLayout:
        """Safe runtime fallback routing helper."""
        # Convert 'Scene Heading' -> 'scene_heading' to match model field tracking
        field_name = paragraph_type_str.lower().replace(" ", "_")
        return getattr(self, field_name, self.general)
