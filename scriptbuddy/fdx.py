from __future__ import annotations
import logging
import textwrap
from pathlib import Path
from enum import Enum
from typing import List, Optional, Any, Dict, TYPE_CHECKING
import re
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict
from scriptbuddy.layout import ScreenplayStyle

_log = logging.getLogger(__name__)

# 🚀 IMPORT YOUR BRAND NEW PURE DOMAIN MODELS CLEANLY
if TYPE_CHECKING:
    from scriptbuddy.script import (
        ScriptModel,
        ScriptScene,
    )
    from scriptbuddy.block import ScriptBlock

# Define standard text parsing constants directly within the module scope
KNOWN_TIMES = {
    "DAY",
    "NIGHT",
    "-NIGHT",
    "MORNING",
    "AFTERNOON",
    "EVENING",
    "SUNSET",
    "SUNRISE",
    "DUSK",
    "DAWN",
    "CONTINUOUS",
    "LATER",
    "SAME TIME",
}


# Scene headings the pipeline mints when a script has no real slugline yet: the
# forward pass names text before the first heading ``PRE-ROLL``; the transcript ->
# script pass names its single scene ``TRANSCRIPT``. Neither is a location, so the
# .fdx writer derives sluglines from the blocks for these instead of echoing them.
_PLACEHOLDER_HEADINGS: frozenset[str] = frozenset({"PRE-ROLL", "TRANSCRIPT"})

# Sentence-terminal punctuation, allowing a closing quote/bracket after it.
_SENTENCE_END = re.compile(r"""[.!?…:]["'”’)\]]*$""")
# Explicit "the thought continues" markers: a trailing comma or semicolon. A
# trailing dash is NOT one — screenwriters end a paragraph on "--" as a beat.
_CONTINUES = re.compile(r"[,;]$")


def _continues_into(prev: str, nxt: str) -> bool:
    """True when *nxt* is the rest of the sentence *prev* started: *prev* ends on
    a comma, or *nxt* opens lowercase. A bare missing period is not
    evidence — beat rows and title cards both lack one — so it never joins."""
    prev = prev.rstrip()
    if _SENTENCE_END.search(prev):
        return False
    return bool(_CONTINUES.search(prev)) or nxt[:1].islower()


def _parse_heading_metadata(heading: str) -> Dict[str, Optional[str]]:
    """Breaks down a scene heading into explicit scene layout metadata attributes."""
    result = {"scope": None, "location": None, "region": None, "time": None}
    clean_heading = (
        heading.replace("\u2013", "-").replace("\u2014", "-").upper().strip()
    )

    parts = [p.strip() for p in re.split(r" -+ ", clean_heading)]
    match = re.match(r"^(INT\.|EXT\.|INT\./EXT\.)\s*(.*)", parts[0], re.IGNORECASE)

    if match:
        result["scope"] = match.group(1).upper().rstrip(".")
        result["location"] = match.group(2).strip().upper()
    else:
        result["location"] = parts[0].upper()

    if len(parts) == 2:
        part = parts[1].upper()
        if part in KNOWN_TIMES:
            result["time"] = part
        else:
            result["region"] = part
    elif len(parts) >= 3:
        result["region"] = parts[1].upper()
        result["time"] = parts[2].upper()
    return result


def _parse_character_metadata(raw: str):
    """Splits performance extensions off character headers safely."""
    match = re.match(r"^(.*?)\s*\((.*?)\)$", raw.strip())
    if match:
        return match.group(1).upper().strip(), match.group(2).upper().strip()
    return raw.upper().strip(), None


class ParagraphType(str, Enum):
    """Final Draft paragraph types: the standard 8 plus first-class structural markers."""

    SCENE_HEADING = "Scene Heading"
    ACTION = "Action"
    CHARACTER = "Character"
    DIALOGUE = "Dialogue"
    PARENTHETICAL = "Parenthetical"
    TRANSITION = "Transition"
    SHOT = "Shot"
    GENERAL = "General"
    # First-class structural types for multi-episode / act-break scripts
    EPISODE = "Episode"
    NEW_ACT = "New Act"
    END_OF_ACT = "End of Act"


# Fast lookup set — used by the field validator and exported for downstream tests
_KNOWN_TYPES: frozenset[str] = frozenset(t.value for t in ParagraphType)

# Structural types the app renders specially (switch on these downstream)
STRUCTURAL_PARAGRAPH_TYPES: frozenset[str] = frozenset(
    {
        ParagraphType.EPISODE,
        ParagraphType.NEW_ACT,
        ParagraphType.END_OF_ACT,
    }
)


class Text(BaseModel):
    """
    Captures individual text fragments inside a paragraph block.
    Handles raw strings, empty XML elements, and structured dictionary nodes seamlessly.
    """

    text: str = Field(alias="#text", default="")
    style: Optional[str] = Field(alias="@Style", default=None)

    @model_validator(mode="before")
    @classmethod
    def normalize_raw_strings_and_nulls(cls, v: Any) -> Any:
        """
        Catches plain strings and XML null elements (None), wrapping them
        into the standardized dict structure Pydantic expects.
        """
        if v is None:
            return {"#text": ""}
        if isinstance(v, str):
            return {"#text": v}
        return v

    @field_validator("text", mode="before")
    @classmethod
    def handle_xml_text_nodes(cls, v: Any) -> str:
        """Ensures incoming null structures normalize down safely to standard strings."""
        if v is None:
            return ""
        if isinstance(v, dict):
            return v.get("#text", "") or ""
        return str(v)


class Paragraph(BaseModel):
    """
    Maps a single narrative element inside the screenplay body.
    """

    # Final Draft is inconsistent: some exports stamp paragraphs with @id, others
    # with @Number, others with neither. None are needed downstream, so accept any
    # / none rather than rejecting the whole document.
    id: Optional[str] = Field(alias="@id", default=None)
    number: Optional[str] = Field(alias="@Number", default=None)
    # Stored as str so unknown custom types pass through; validated below.
    paragraph_type: str = Field(alias="@Type")
    text_elements: List[Text] = Field(alias="Text", default_factory=list)

    @field_validator("paragraph_type", mode="before")
    @classmethod
    def coerce_paragraph_type(cls, v: Any) -> str:
        """Accept any @Type string. Known types are normalised via the enum;
        unknown ones log a warning and pass through so the document never hard-fails."""
        s = str(v).strip() if v is not None else ""
        if s not in _KNOWN_TYPES:
            _log.warning(
                "FDX: unrecognized paragraph @Type %r — preserving as-is. "
                "Add it to ParagraphType if first-class support is needed.",
                s,
            )
        return s

    @field_validator("text_elements", mode="before")
    @classmethod
    def force_list_wrap(cls, v: Any) -> List[Any]:
        """XML parsers flatten single child tags into dicts; this guarantees an array.
        An empty paragraph (``<Paragraph/>``) yields None — normalize to []."""
        if v is None:
            return []
        if isinstance(v, (dict, str)):
            return [v]
        return v

    @property
    def clean_text(self) -> str:
        # Ensure there's a space when joining separate text chunks or lines
        return " ".join([t.text.strip() for t in self.text_elements if t.text])

    def format_screenplay(self, style: Optional[ScreenplayStyle] = None) -> str:
        """
        Formats text elements using an injected, data-driven style configuration profile.
        """
        text = self.clean_text
        if not text:
            return ""

        # 1. Inject Style Profile (Fallback to default standard margins if none provided)
        style_sheet = style or ScreenplayStyle()
        layout = style_sheet.get_layout(self.paragraph_type)

        # 2. Process typographical transformations dynamically
        if layout.force_upper:
            text = text.upper()

        if layout.prefix and not text.startswith(layout.prefix):
            text = f"{layout.prefix}{text}"
        if layout.suffix and not text.endswith(layout.suffix):
            text = f"{text}{layout.suffix}"

        # 3. Handle line wrap blocks safely
        wrapped_lines = textwrap.wrap(text, width=layout.wrap_width)
        indent_spaces = " " * layout.left_margin
        formatted_block = "\n".join(
            [f"{indent_spaces}{line}" for line in wrapped_lines]
        )

        # 4. Spacing rules padding
        if self.paragraph_type in (
            ParagraphType.CHARACTER,
            ParagraphType.PARENTHETICAL,
        ):
            return formatted_block
        else:
            return f"{formatted_block}\n"


class Content(BaseModel):
    """
    The heart of the FDX file—houses the linear array of script paragraphs.
    """

    paragraphs: List[Paragraph] = Field(alias="Paragraph", default_factory=list)

    @field_validator("paragraphs", mode="before")
    @classmethod
    def _wrap_single_paragraph(cls, v: Any) -> List[Any]:
        """xmltodict flattens a single <Paragraph> into a dict; guarantee a list."""
        if v is None:
            return []
        if isinstance(v, dict):
            return [v]
        return v


class _ElementSetting(BaseModel):
    """One <ElementSettings Type="..."> entry from the FDX header."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    element_type: str = Field(alias="@Type")


class Watermark(BaseModel):
    """A stamped overlay watermark carried on the script (e.g. an ownership stamp
    "Property of Base Media"). Detected + stripped when parsing a source PDF and
    preserved here for provenance rather than dropped. Serializes to a
    ``<Watermark Text="..." .../>`` element so it round-trips through .fdx and
    flows into script.json via :meth:`ScriptContainer.to_script_model`."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    text: str = Field(alias="@Text", default="")
    font: Optional[str] = Field(alias="@Font", default=None)
    size: Optional[float] = Field(alias="@Size", default=None)
    count: Optional[int] = Field(alias="@Count", default=None)


class FinalDraft(BaseModel):
    """
    The master structure for the root level xml wrap of an FDX document.
    """

    model_config = ConfigDict(populate_by_name=True)

    document_type: str = Field(alias="@DocumentType")
    version: str = Field(alias="@Version")
    content: Content = Field(alias="Content")
    element_settings: List[_ElementSetting] = Field(
        alias="ElementSettings", default_factory=list
    )
    # Overlay watermarks detected in a source PDF (empty for native .fdx).
    watermarks: List[Watermark] = Field(alias="Watermark", default_factory=list)

    @field_validator("element_settings", mode="before")
    @classmethod
    def _wrap_single_element_setting(cls, v: Any) -> List[Any]:
        """xmltodict flattens a single child into a dict; guarantee a list."""
        if v is None:
            return []
        if isinstance(v, dict):
            return [v]
        return v

    @field_validator("watermarks", mode="before")
    @classmethod
    def _wrap_single_watermark(cls, v: Any) -> List[Any]:
        """xmltodict flattens a single <Watermark> into a dict; guarantee a list."""
        if v is None:
            return []
        if isinstance(v, dict):
            return [v]
        return v

    @property
    def declared_element_types(self) -> List[str]:
        """All element types declared in this FDX's <ElementSettings> block."""
        return [es.element_type for es in self.element_settings]

    @property
    def declared_custom_types(self) -> List[str]:
        """Element types declared in this FDX that are not standard FD types."""
        return [t for t in self.declared_element_types if t not in _KNOWN_TYPES]

    def print_script(self, limit: Optional[int] = None):
        """
        Iterates over the entire script element tree, formatting and
        printing each paragraph in real-time to the active terminal.
        """
        print("\n" + "=" * 80)
        print(
            f"🎬 SCREENPLAY PREVIEW LAYER: {self.document_type} (v{self.version})".center(
                80
            )
        )
        print("=" * 80 + "\n")

        paragraphs_to_print = (
            self.content.paragraphs[:limit] if limit else self.content.paragraphs
        )

        for p in paragraphs_to_print:
            formatted_element = p.format_screenplay()
            if formatted_element:
                print(formatted_element)

        print("\n" + "=" * 80)
        print(
            f"🏁 END OF SCRIPT PREVIEW [{len(paragraphs_to_print)} Paragraphs Listed]".center(
                80
            )
        )
        print("=" * 80 + "\n")


class ScriptContainer(BaseModel):
    """
    The true execution target that interfaces with python-xmltodict parsing engines.
    """

    final_draft: FinalDraft = Field(alias="FinalDraft")
    # Where this container was loaded from, when it was (None for a container
    # built in memory). Used to default a title; never serialized.
    source_path: Optional[Path] = Field(default=None, exclude=True)

    @classmethod
    def load(cls, fdx_path: str | Path) -> ScriptContainer:
        """
        Reads a Final Draft .fdx file from disk, parses its raw XML structure,
        and hydrates the complete validated nested model hierarchy.
        """
        path = Path(fdx_path)
        if not path.exists():
            raise FileNotFoundError(
                f"Cannot load script: {path} does not exist on disk."
            )

        # Local import for the same reason as pandas in ``script/query.py``: this
        # module is reachable from the asset manifests, and a module-level import
        # drags a Final Draft dependency into every consumer — including an Unreal
        # rig build, whose Python env is a curated subset without it.
        import xmltodict

        raw_xml = path.read_text(encoding="utf-8")
        parsed_dict = xmltodict.parse(raw_xml, dict_constructor=dict)

        container = cls.model_validate(parsed_dict)
        container.source_path = path
        return container

    @classmethod
    def from_script_model(
        cls, script: ScriptModel, join_fragments: bool = True
    ) -> ScriptContainer:
        """
        Inverse of :meth:`to_script_model` — rebuild a Final Draft document from a
        ``script.json`` ScriptModel so an edited script can leave the pipeline as a
        ``.fdx`` again (Final Draft, WriterDuet, a producer's inbox).

        Block → paragraph mapping mirrors the forward pass: a scene's heading becomes
        a Scene Heading paragraph, dialogue is re-attributed by emitting a Character
        cue (name + performance extension) whenever the speaker changes, and the
        first-class structural types (Episode / New Act / End of Act) keep their
        Final Draft names. The synthetic ``PRE-ROLL`` scene the forward pass mints for
        text before the first slugline is written without a heading, so a round trip
        does not invent one.

        ``join_fragments`` re-joins sentences the script tool split across beat
        blocks (see ``emit`` below); pass ``False`` for one paragraph per block.
        """
        from scriptbuddy.block import ScriptBlockType

        # Block types that carry straight across to a Final Draft paragraph type.
        direct = {
            ScriptBlockType.ACTION: ParagraphType.ACTION.value,
            ScriptBlockType.VISUAL: ParagraphType.ACTION.value,
            ScriptBlockType.DIALOGUE: ParagraphType.DIALOGUE.value,
            ScriptBlockType.PARENTHETICAL: ParagraphType.PARENTHETICAL.value,
            ScriptBlockType.TRANSITION: ParagraphType.TRANSITION.value,
            # A scene-heading block *inside* a scene is a Shot (the forward pass maps
            # Final Draft "Shot" onto SCENE_HEADING); the scene's own slugline is
            # emitted from ``scene.heading`` above the blocks.
            ScriptBlockType.SCENE_HEADING: ParagraphType.SHOT.value,
            ScriptBlockType.EPISODE: ParagraphType.EPISODE.value,
            ScriptBlockType.NEW_ACT: ParagraphType.NEW_ACT.value,
            ScriptBlockType.END_OF_ACT: ParagraphType.END_OF_ACT.value,
            ScriptBlockType.STRUCTURAL: ParagraphType.GENERAL.value,
        }

        paragraphs: List[Dict[str, Any]] = []

        def emit(kind: str, text: str) -> None:
            text = (text or "").strip()
            if not text:
                return
            # Beat-authored scripts (the transcript tool splits rows by the picture
            # each one gets) leave a sentence spread over several blocks —
            # "raises the cup," / "takes a sip of his morning coffee". Join a
            # block that visibly continues the previous one (see _continues_into)
            # onto it, and consecutive dialogue into the one delivery it is, so the
            # .fdx reads as prose. A writer's own paragraphs stay separate, so a
            # parsed .fdx round-trips unchanged.
            if join_fragments and paragraphs and paragraphs[-1]["@Type"] == kind:
                prev = paragraphs[-1]["Text"]
                if kind == ParagraphType.DIALOGUE.value or _continues_into(prev, text):
                    paragraphs[-1]["Text"] = f"{prev} {text}"
                    return
            paragraphs.append({"@Type": kind, "Text": text})

        def cue(name: str, extension: Optional[str]) -> str:
            name = (name or "").strip().upper()
            return f"{name} ({extension.strip().upper()})" if extension else name

        def slugline(scope: Optional[str], location: str, time: Optional[str]) -> str:
            """Compose a Final Draft slugline from the parts that are actually known:
            ``EXT. HOMESTEAD - MORNING`` when all three are, ``HOMESTEAD`` when only
            the location is. Never invents a scope or time the model doesn't hold."""
            loc = location.replace("_", " ").strip().upper()
            head = f"{scope.strip().upper().rstrip('.')}. {loc}" if scope else loc
            return f"{head} - {time.strip().upper()}" if time else head

        for scene in script.scenes:
            heading = (scene.heading or "").strip()
            # A real slugline owns its scene. The placeholders the pipeline mints —
            # ``PRE-ROLL`` for text before the first heading, ``TRANSCRIPT`` for a
            # transcript-derived script — carry no location, so those scenes take
            # their sluglines from the blocks instead (see below).
            placeholder = heading.upper() in _PLACEHOLDER_HEADINGS or not heading
            if not placeholder:
                emit(ParagraphType.SCENE_HEADING.value, heading)
            elif scene.location:
                emit(
                    ParagraphType.SCENE_HEADING.value,
                    slugline(scene.scope, scene.location, scene.time),
                )

            # The speaker the last emitted Character cue opened; a dialogue block
            # whose speaker differs re-emits a cue, so speech stays attributed.
            open_speaker: Optional[str] = None
            # The location the current slugline covers. In a placeholder scene the
            # blocks carry the location (transcript rows tag one each), so a change
            # of block location is a new slugline — that is what makes a transcript
            # read as a screenplay once it lands in Final Draft.
            open_location: Optional[str] = (
                None if placeholder and not scene.location else (scene.location or "")
            )
            for block in scene.blocks:
                block_loc = (block.location or "").strip()
                if placeholder and block_loc and block_loc != open_location:
                    emit(
                        ParagraphType.SCENE_HEADING.value,
                        slugline(scene.scope, block_loc, scene.time),
                    )
                    open_location = block_loc
                    open_speaker = None

                kind = block.block_type
                if kind == ScriptBlockType.CHARACTER:
                    open_speaker = (block.text or block.speaker or "").strip().upper()
                    emit(
                        ParagraphType.CHARACTER.value,
                        cue(open_speaker, block.parenthetical),
                    )
                    continue

                if kind == ScriptBlockType.DIALOGUE:
                    speaker = (block.speaker or "").strip().upper()
                    if speaker and speaker != open_speaker:
                        emit(
                            ParagraphType.CHARACTER.value,
                            cue(speaker, block.parenthetical),
                        )
                        open_speaker = speaker
                    emit(ParagraphType.DIALOGUE.value, block.text)
                    continue

                if kind == ScriptBlockType.PARENTHETICAL:
                    text = (block.text or "").strip()
                    if text and not text.startswith("("):
                        text = f"({text})"
                    emit(ParagraphType.PARENTHETICAL.value, text)
                    continue

                # Anything else ends the speaker run, exactly as the forward pass does.
                open_speaker = None
                emit(direct.get(kind, ParagraphType.GENERAL.value), block.text)

        final_draft: Dict[str, Any] = {
            "@DocumentType": "Script",
            "@Version": "5",
            "Content": {"Paragraph": paragraphs},
        }
        if script.watermarks:
            final_draft["Watermark"] = [
                w.model_dump(by_alias=True, exclude_none=True)
                for w in script.watermarks
            ]
        return cls.model_validate({"FinalDraft": final_draft})

    def to_fdx_dict(self) -> Dict[str, Any]:
        """The ``{"FinalDraft": {...}}`` dict :func:`xmltodict.unparse` serializes —
        the same shape :meth:`load` parses, so a written file re-loads identically."""
        fd = self.final_draft.model_dump(by_alias=True, exclude_none=True)
        # xmltodict writes an empty list as nothing, but keep the header tidy.
        for key in ("ElementSettings", "Watermark"):
            if not fd.get(key):
                fd.pop(key, None)
        return {"FinalDraft": fd}

    def write(self, out_path: str | Path) -> Path:
        """Write this document as a Final Draft ``.fdx`` and return its path."""
        import xmltodict  # local, for the same reason as in :meth:`load`

        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        xml = xmltodict.unparse(self.to_fdx_dict(), pretty=True, full_document=True)
        out.write_text(xml, encoding="utf-8")
        _log.info(
            "ScriptContainer: wrote %d paragraphs -> %s",
            len(self.final_draft.content.paragraphs),
            out,
        )
        return out

    def dialogue_lines(self) -> List[Dict[str, Optional[str]]]:
        """Flat, ordered list of spoken lines: ``[{"speaker","text"}, ...]``.

        The minimal projection for script↔transcript alignment / subtitling —
        every Dialogue paragraph in document order, tagged with the most recent
        Character header (parenthetical extension stripped). Skips empties."""
        lines: List[Dict[str, Optional[str]]] = []
        speaker: Optional[str] = None
        for p in self.final_draft.content.paragraphs:
            kind = p.paragraph_type
            if kind == "Character":
                speaker, _ = _parse_character_metadata(p.clean_text)
            elif kind == "Dialogue":
                text = p.clean_text
                if text:
                    lines.append({"speaker": speaker, "text": text})
        return lines

    def to_script_model(
        self,
        project_id: str = "script",
        title: Optional[str] = None,
        version: str = "1.0.0",
        *,
        model_cls: Optional[type] = None,
        scene_cls: Optional[type] = None,
        block_cls: Optional[type] = None,
    ) -> ScriptModel:
        """
        Walk the Final Draft paragraphs and emit a normalized :class:`ScriptModel`:
        scenes split on sluglines, dialogue attributed to its speaker, per-block
        screen-time estimates, per-scene cast lists.

        ``model_cls`` / ``scene_cls`` / ``block_cls`` let a caller substitute
        subclasses of the three models (a pipeline that hangs its own methods
        and fields off them) without re-implementing the walk.
        """
        from scriptbuddy.script import ScriptModel as _ScriptModel
        from scriptbuddy.script import ScriptScene as _ScriptScene
        from scriptbuddy.block import ScriptBlock as _ScriptBlock
        from scriptbuddy.block import ScriptBlockType

        ScriptModel = model_cls or _ScriptModel
        ScriptScene = scene_cls or _ScriptScene
        ScriptBlock = block_cls or _ScriptBlock
        if title is None:
            title = self.source_path.stem if self.source_path else "Untitled"

        raw_paragraphs = self.final_draft.content.paragraphs

        processed_scenes: List[ScriptScene] = []
        current_scene_id: Optional[str] = None
        current_scene_heading: Optional[str] = None
        current_scene_blocks: List[ScriptBlock] = []

        scene_counter = 10
        block_counter = 10
        current_location = None
        # Running speaker context: a Character paragraph sets it; the dialogue that
        # follows (and any continuation paragraphs) inherits it. Final Draft splits one
        # speech across multiple Dialogue paragraphs on hard returns, so this is what
        # lets us keep them attributed and merged.
        current_speaker: Optional[str] = None
        current_parenthetical: Optional[str] = None

        for idx, p in enumerate(raw_paragraphs):
            p_text = p.clean_text
            if not p_text:
                continue

            # A. ENCOUNTERED A NEW SCENE HEADING NODE
            if p.paragraph_type == "Scene Heading":
                if current_scene_id:
                    # Flush the active scene out into our array list
                    processed_scenes.append(
                        ScriptScene(
                            scene_id=current_scene_id,
                            heading=current_scene_heading,
                            blocks=current_scene_blocks,
                            location=current_location,
                        )
                    )

                # Reset pointers and track headings parameters
                current_scene_heading = p_text.upper().strip()
                meta = _parse_heading_metadata(current_scene_heading)
                current_location = meta.get("location")

                current_scene_id = f"{scene_counter:03d}"
                current_scene_blocks = []
                scene_counter += 10
                block_counter = 10
                current_speaker = None
                current_parenthetical = None
                continue

            # Handle safety fallback initialization boundaries
            if not current_scene_id:
                current_scene_id = "000"
                current_scene_heading = "PRE-ROLL"

            block_name = f"{current_scene_id}_{block_counter:04d}"

            # B. EXECUTE ELEMENT TYPE CORRELATION MATRICES
            type_mapping = {
                "Action": ScriptBlockType.ACTION,
                "Character": ScriptBlockType.CHARACTER,
                "Dialogue": ScriptBlockType.DIALOGUE,
                "Parenthetical": ScriptBlockType.PARENTHETICAL,
                "Transition": ScriptBlockType.TRANSITION,
                "Shot": ScriptBlockType.SCENE_HEADING,
                "General": ScriptBlockType.ACTION,
                "Episode": ScriptBlockType.EPISODE,
                "New Act": ScriptBlockType.NEW_ACT,
                "End of Act": ScriptBlockType.END_OF_ACT,
            }
            # Unknown custom types become STRUCTURAL so they round-trip in the manifest
            assigned_type = type_mapping.get(
                p.paragraph_type, ScriptBlockType.STRUCTURAL
            )

            # Fallback: un-tagged Action text immediately before Dialogue is a character cue
            if assigned_type == ScriptBlockType.ACTION and idx + 1 < len(
                raw_paragraphs
            ):
                if raw_paragraphs[idx + 1].paragraph_type == "Dialogue":
                    assigned_type = ScriptBlockType.CHARACTER

            # C. CHARACTER CUE — set the running speaker; show the clean name on the block.
            if assigned_type == ScriptBlockType.CHARACTER:
                current_speaker, current_parenthetical = _parse_character_metadata(
                    p_text
                )
                p_text = current_speaker

            # C2. DIALOGUE CONTINUATION — Final Draft splits one speech across consecutive
            # Dialogue paragraphs on hard returns. Merge into the previous dialogue block
            # so it stays a single delivery (one voice line) with its speaker intact,
            # instead of becoming an orphan block with no speaker.
            if (
                assigned_type == ScriptBlockType.DIALOGUE
                and current_scene_blocks
                and current_scene_blocks[-1].block_type == ScriptBlockType.DIALOGUE
            ):
                prev = current_scene_blocks[-1]
                prev.text = f"{prev.text} {p_text}".strip()
                prev.duration_secs = round(len(prev.text.split()) / 2.5, 2)
                continue

            # Speaker/parenthetical for this beat (dialogue inherits the running speaker).
            speaker_name = (
                current_speaker if assigned_type == ScriptBlockType.DIALOGUE else None
            )
            parenthetical_text = (
                current_parenthetical
                if assigned_type
                in (
                    ScriptBlockType.DIALOGUE,
                    ScriptBlockType.CHARACTER,
                )
                else None
            )

            # Calculate pacing speeds benchmarks
            words = len(p_text.split())
            duration_secs = (
                (words / 2.5)
                if assigned_type == ScriptBlockType.DIALOGUE
                else (words / 4.0)
            )
            # Build our pristine ScriptBlock node element
            current_scene_blocks.append(
                ScriptBlock(
                    id=block_name.split("_")[-1],
                    scene_id=current_scene_id,
                    block_type=ScriptBlockType(assigned_type),
                    text=p_text,
                    speaker=speaker_name,
                    parenthetical=parenthetical_text,
                    location=current_location,
                    duration_secs=round(duration_secs, 2),
                )
            )
            block_counter += 10

            # A non-dialogue beat (action / transition / scene shot) ends the speaker run.
            if assigned_type not in (
                ScriptBlockType.CHARACTER,
                ScriptBlockType.DIALOGUE,
                ScriptBlockType.PARENTHETICAL,
            ):
                current_speaker = None
                current_parenthetical = None

        # Flush the final trailing block sequence out of cache loops
        if current_scene_id:
            processed_scenes.append(
                ScriptScene(
                    scene_id=current_scene_id,
                    heading=current_scene_heading,
                    blocks=current_scene_blocks,
                    location=current_location,
                )
            )

        # 🚀 Compile and deliver the beautifully packed ScriptModel!
        script_model = ScriptModel(
            id=project_id,
            title=title,
            version=version,
            scenes=processed_scenes,
            watermarks=self.final_draft.watermarks,
        )

        # Trigger automatic rolling metrics calculations ( DAY/NIGHT settings aggregation, etc. )
        for scene in script_model.scenes:
            # Safely parse structural text metadata variables
            heading_meta = _parse_heading_metadata(scene.heading)
            scene.scope = heading_meta.get("scope")
            scene.time = heading_meta.get("time")
            scene.region = heading_meta.get("region")

            # Aggregate dynamic running counts safely
            scene.duration_secs = sum((b.duration_secs or 0.0) for b in scene.blocks)
            scene.duration_mins = scene.duration_secs / 60.0

            # Collect unique character list parameters actively on screen
            for b in scene.blocks:
                if b.speaker and b.speaker not in scene.speakers:
                    scene.speakers.append(b.speaker)
                if b.speaker and b.speaker not in scene.characters:
                    scene.characters.append(b.speaker)

        return script_model


if __name__ == "__main__":
    import sys

    ScriptContainer.load(sys.argv[1]).final_draft.print_script(limit=25)
