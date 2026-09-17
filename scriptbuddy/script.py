from __future__ import annotations

import json
import re
from functools import cached_property
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field

from scriptbuddy.block import ScriptBlock, ScriptBlockType
from scriptbuddy.fdx import Watermark

# ---------------------------------------------------------------------------
# Entity-extraction vocabularies
# ---------------------------------------------------------------------------

# All-caps patterns that are editorial callouts, not physical props.
# Matched against the uppercase form of an extracted all-caps token.
_EDITORIAL_MARKERS: frozenset[str] = frozenset(
    {
        "INTERCUT",
        "INTERCUT WITH",
        "INTERCUT TO",
        "END INTERCUT",
        "END OF INTERCUT",
        "LATER",
        "MOMENTS LATER",
        "SHORTLY LATER",
        "TIME LATER",
        "CONTINUOUS",
        "SAME TIME",
        "SAME MOMENT",
        "BACK TO",
        "BACK TO SCENE",
        "SMASH CUT",
        "SMASH CUT TO",
        "MATCH CUT",
        "MATCH CUT TO",
        "JUMP CUT",
        "JUMP CUT TO",
        "DISSOLVE TO",
        "DISSOLVE",
        "FADE TO",
        "FADE IN",
        "FADE OUT",
        "FADE TO BLACK",
        "CUT TO",
        "CUT BACK TO",
        "HARD CUT",
        "TITLE CARD",
        "SUPER",
        "CAPTION",
        "TIME CUT",
        "TIME LAPSE",
        "TIME JUMP",
        "FLASHBACK",
        "FLASH FORWARD",
        "END FLASHBACK",
        "MONTAGE",
        "END MONTAGE",
        "SERIES OF SHOTS",
        "END SERIES",
        "V.O.",
        "O.S.",
        "O.C.",
    }
)

# Sound-effect words that appear as all-caps in action text.
# These are routed to the SFX pool instead of props.
_SFX_VOCAB: frozenset[str] = frozenset(
    {
        "BANG",
        "BANGS",
        "BOOM",
        "BOOMS",
        "CRASH",
        "CRASHES",
        "CRACK",
        "CRACKS",
        "CREAK",
        "CREAKS",
        "CLANG",
        "CLANGS",
        "CLATTER",
        "CLATTERS",
        "CLINK",
        "CLINKS",
        "CLICK",
        "CLICKS",
        "THUD",
        "THUDS",
        "THUMP",
        "THUMPS",
        "SLAM",
        "SLAMS",
        "SLAP",
        "SLAPS",
        "SNAP",
        "SNAPS",
        "SMASH",
        "SMASHES",
        "SHATTER",
        "SHATTERS",
        "SCREECH",
        "SCREECHES",
        "SCREAM",
        "SCREAMS",
        "ROAR",
        "ROARS",
        "RUMBLE",
        "RUMBLES",
        "THUNDER",
        "BLAST",
        "BLASTS",
        "EXPLOSION",
        "EXPLOSIONS",
        "SILENCE",
        "STATIC",
        "BUZZ",
        "BUZZES",
        "WHIR",
        "WHIRS",
        "HUM",
        "HUMS",
        "DRONE",
        "BEEP",
        "BEEPS",
        "RING",
        "RINGS",
        "CHIME",
        "CHIMES",
        "DING",
        "GUNSHOT",
        "GUNFIRE",
        "SHOTS",
        "SPLASH",
        "SPLASHES",
        "SPLAT",
        "DRIP",
        "DRIPS",
        "GROWL",
        "GROWLS",
        "HOWL",
        "HOWLS",
        "BARK",
        "BARKS",
        "RATTLE",
        "RATTLES",
        "GROAN",
        "GROANS",
        "CRUNCH",
        "CRUNCHES",
        "POP",
        "POPS",
        "HISS",
        "HISSES",
        "WHOOSH",
        "SWOOSH",
        "ZAP",
        "ZING",
        "WHIRR",
        "WHACK",
        "THWACK",
        "CRACKLE",
        "CRACKLES",
        "TINKLE",
        "TINKLES",
        "CLUNK",
        "CLUNKS",
        "CLOP",
        "CLOPS",
        "SIZZLE",
        "SIZZLES",
        "GURGLE",
        "GURGLES",
    }
)

# Action / movement emphasis verbs that show up all-caps in action lines
# ("a hand BURSTS UP", "she LUNGES"). Routed to the ACTION pool — beats/animation
# cues, distinct from physical props. Checked per-word, so multiword runs like
# "BURSTS UP" match on the verb. SFX is matched first, so overlaps stay sound.
_ACTION_VOCAB: frozenset[str] = frozenset(
    {
        "BURST",
        "BURSTS",
        "RISE",
        "RISES",
        "FALL",
        "FALLS",
        "LUNGE",
        "LUNGES",
        "LEAP",
        "LEAPS",
        "JUMP",
        "JUMPS",
        "CHARGE",
        "CHARGES",
        "GRAB",
        "GRABS",
        "DRAW",
        "DRAWS",
        "SWING",
        "SWINGS",
        "KICK",
        "KICKS",
        "PUNCH",
        "PUNCHES",
        "SPIN",
        "SPINS",
        "DUCK",
        "DUCKS",
        "DODGE",
        "DODGES",
        "COLLAPSE",
        "COLLAPSES",
        "STAGGER",
        "STAGGERS",
        "STUMBLE",
        "STUMBLES",
        "SPRINT",
        "SPRINTS",
        "DASH",
        "DASHES",
        "BOLT",
        "BOLTS",
        "DIVE",
        "DIVES",
        "ROLL",
        "ROLLS",
        "TUMBLE",
        "TUMBLES",
        "WHIRL",
        "WHIRLS",
        "HURL",
        "HURLS",
        "YANK",
        "YANKS",
        "SHOVE",
        "SHOVES",
        "SCRAMBLE",
        "SCRAMBLES",
        "CLIMB",
        "CLIMBS",
        "CRAWL",
        "CRAWLS",
        "TWIST",
        "TWISTS",
        "EXPLODE",
        "EXPLODES",
        "FLEE",
        "FLEES",
        "POUNCE",
        "POUNCES",
        "STRIKE",
        "STRIKES",
        "RIP",
        "RIPS",
        "SNATCH",
        "SNATCHES",
        "BURROW",
        "BURROWS",
        "EMERGE",
        "EMERGES",
    }
)


class ScriptScene(BaseModel):
    """A chronological location block container tracking structural text blocks."""

    model_config = ConfigDict(extra="allow")

    scene_id: str
    heading: str
    location: Optional[str] = None
    time: Optional[str] = None  # DAY, NIGHT, etc...
    scope: Optional[str] = None  # "INT", "EXT"
    region: Optional[str] = None  # PACIFIC NORTHWEST
    duration_secs: float = 0.0
    duration_mins: float = 0.0
    start_frame: Optional[int] = None
    speakers: List[str] = Field(default_factory=list)
    characters: List[str] = Field(default_factory=list)
    props: List[str] = Field(default_factory=list)
    sounds: List[str] = Field(default_factory=list)
    editorials: List[str] = Field(default_factory=list)
    actions: List[str] = Field(default_factory=list)
    fx: List[str] = Field(default_factory=list)
    blocks: List[ScriptBlock] = Field(default_factory=list)

    @property
    def id(self):
        return self.scene_id

    @computed_field
    @property
    def name(self) -> str:
        return f"{self.scene_id}_SCN"

    def get_block(self, block_id: str) -> Optional[ScriptBlock]:
        """
        Safely scans the chronological blocks array matrix to look up an individual
        ScriptBlock by its unique ID string primitive.

        Returns:
            ScriptBlock: The matching model instance if discovered, else None.
        """
        # Strip string white-spaces to ensure zero-overhead formatting clean lookups
        target_id = block_id.strip()

        # Fast search loop check across our structural elements
        for block in self.blocks:
            if block.id == target_id:
                return block

        return None

    def discover_and_hydrate_props(self) -> None:
        """
        Scans all text blocks for capitalized words/phrases and routes them to
        the correct entity pool: props, sounds (SFX), or editorial markers (EDT).
        Transition blocks are harvested as editorial markers directly.
        """
        existing_chars = {c.strip().upper() for c in self.characters + self.speakers}

        existing_locations: set[str] = set()
        if self.location:
            existing_locations.add(self.location.strip().upper())
        if self.region:
            existing_locations.add(self.region.strip().upper())

        existing_props = {p.strip().upper() for p in self.props}
        existing_sounds = {s.strip().upper() for s in self.sounds}
        existing_editorial = {e.strip().upper() for e in self.editorials}
        existing_actions = {a.strip().upper() for a in self.actions}

        for block in self.blocks:
            # Transition blocks are editorial callouts by definition — harvest directly.
            if block.block_type == ScriptBlockType.TRANSITION:
                text = block.text.strip().rstrip(":").strip()
                text_upper = text.upper()
                if text_upper and text_upper not in existing_editorial:
                    self.editorials.append(text)
                    existing_editorial.add(text_upper)
                continue

            if block.block_type not in (
                ScriptBlockType.ACTION,
                ScriptBlockType.DIALOGUE,
            ):
                continue

            block_text = getattr(block, "text", "")
            if not block_text:
                continue

            candidates = re.findall(r"\b[A-Z]{2,}(?:\s+[A-Z]{2,})*\b", block_text)
            for candidate in candidates:
                candidate_clean = candidate.strip()
                candidate_upper = candidate_clean.upper()

                if len(candidate_clean) <= 1 or candidate_clean.isdigit():
                    continue
                if candidate_upper in existing_chars:
                    continue
                if candidate_upper in existing_locations:
                    continue

                # Route: editorial marker
                if candidate_upper in _EDITORIAL_MARKERS:
                    if candidate_upper not in existing_editorial:
                        self.editorials.append(candidate_clean)
                        existing_editorial.add(candidate_upper)

                # Route: sound effect
                elif candidate_upper in _SFX_VOCAB:
                    if candidate_upper not in existing_sounds:
                        self.sounds.append(candidate_clean)
                        existing_sounds.add(candidate_upper)

                # Route: action callout — any word in the run is an action verb
                # (catches multiword emphasis like "BURSTS UP").
                elif any(word in _ACTION_VOCAB for word in candidate_upper.split()):
                    if candidate_upper not in existing_actions:
                        self.actions.append(candidate_clean)
                        existing_actions.add(candidate_upper)

                # Route: prop (default)
                elif candidate_upper not in existing_props:
                    self.props.append(candidate_clean)
                    existing_props.add(candidate_upper)


class ScriptModel(BaseModel):
    """The complete structural, validated representation of a screenplay."""

    model_config = ConfigDict(extra="allow")

    id: str = "production_script"
    title: str = "Untitled Project"
    version: str = "1.0.0"
    scenes: List[ScriptScene] = Field(default_factory=list)
    # Overlay watermarks detected in the source PDF (ownership stamps, etc.),
    # preserved for provenance. Empty for scripts ingested from a native .fdx.
    watermarks: List[Watermark] = Field(default_factory=list)

    @classmethod
    def load(cls, path_to_json: Path | str) -> ScriptModel:
        """Open a ``script.json`` and map it into the model. A missing file
        yields an empty script rather than raising."""
        path = Path(path_to_json)
        if not path.exists():
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(**data)

    # Alias kept so a caller written against the original name still works.
    load_msd = load

    def save(self, path_to_json: Path | str, indent: int = 2) -> Path:
        path = Path(path_to_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=indent), encoding="utf-8")
        return path

    @cached_property
    def scene_map(self) -> Dict[str, Any]:
        return {s.scene_id: s for s in self.scenes}

    def get_scene(self, scene_id: str) -> Optional[Any]:
        padded_id = str(scene_id).zfill(3)
        return self.scene_map.get(padded_id)

    def discover_all_assets(self) -> None:
        """
        Iterates through all scenes to discover unmapped capitalized entities
        and push them into each scene's prop / sound / editorial / action pools.
        """
        for scene in self.scenes:
            scene.discover_and_hydrate_props()
