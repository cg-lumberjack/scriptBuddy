
"""
Heading sanity check for freshly parsed screenplays.

The PDF line classifier (:func:`scriptbuddy.pdf.classify`) decides a
slugline by one test: does the line open with ``INT.`` / ``EXT.`` / ``EST.``?
That is authoritative when it fires, but plenty of real sluglines never say
INT. or EXT. at all — the sub-headings a writer drops mid-scene to move the
camera to a new place::

    DEEP SPACE
    A SHIP ON A STORM-TOSSED SEA - NIGHT
    IN THE WATER, NEAR THE CLIFFS - NIGHT
    AT THE FAR END OF THE JUNKYARD
    FLASHBACK: WASHINGTON D.C. - MORNING

None of those match SCENE_RE, and none carry a camera keyword, so each one falls
through to Action. That is not a cosmetic problem: the entity extractor scans
Action blocks for all-caps runs and files anything it doesn't recognise as a
prop — which is how "DEEP SPACE" and "EARTH" end up in a modelling pool.

This module runs *after* paragraph assembly, where the single-line classifier's
blind spot is easy to see: by then a slugline is a short, standalone, all-caps
paragraph of its own, and the surrounding paragraphs are known. It only ever
promotes ``Action`` — it never demotes anything the parser was sure about — and
it returns a report of every change so a bad promotion is visible rather than
silent.

The same pass repairs an existing .fdx (see :func:`repair_fdx_file`), so scripts
ingested before this check existed can be fixed in place.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from scriptbuddy.fdx import ParagraphType

_log = logging.getLogger(__name__)

# A slugline is short. Anything longer is prose that happens to be shouted.
_MAX_SLUG_WORDS = 12

# "- NIGHT", "- CONTINUOUS", "- A LITTLE LATER" — the time tail that ends a
# slugline. Its presence is the single strongest hint we have.
_TIME_TAIL_RE = re.compile(
    r"[-–—]\s*(?:A\s+)?(?:LITTLE\s+|FEW\s+|MOMENTS?\s+|SECONDS?\s+|MINUTES?\s+|"
    r"HOURS?\s+|DAYS?\s+|\d+\s+)*"
    r"(?:DAY|NIGHT|DAWN|DUSK|MORNING|EVENING|AFTERNOON|MIDNIGHT|NOON|"
    r"CONTINUOUS|LATER|SAME|MOMENTS LATER|THAT DAY|THAT NIGHT|ESTABLISHING)"
    r"\s*$"
)

# "FLASHBACK:", "DREAM:", "MONTAGE:" — a context prefix that opens a new place.
_CONTEXT_PREFIX_RE = re.compile(
    r"^(FLASHBACK|FLASH FORWARD|DREAM|FANTASY|MONTAGE|SERIES OF SHOTS|"
    r"LATER THAT (DAY|NIGHT)|MEANWHILE)\s*[:\-]"
)

# Camera / continuation language. These describe where the lens is, not where the
# story is, so they become Shot elements rather than Scene Headings.
# Deliberately excludes the bare prepositions "ON", "WITH" and "FROM": they open
# far more sub-sluglines than camera moves ("ON THE DECK - NIGHT" is a place),
# and letting them through here costs a real scene.
_CAMERA_PREFIXES: tuple[str, ...] = (
    "ANGLE ON",
    "ANGLE",
    "ANGLES",
    "CLOSE ON",
    "CLOSE UP",
    "CLOSE",
    "TIGHT ON",
    "WIDE ON",
    "WIDE",
    "INSERT",
    "POV",
    "REVERSE",
    "FAVORING",
    "FAVOURING",
    "TRACKING",
    "PAN",
    "DOLLY",
    "ZOOM",
    "CRANE",
    "AERIAL",
    "OVERHEAD",
    "THE CAMERA",
    "CAMERA",
    "LOOKING",
    "PUSH IN",
    "PULL BACK",
    "RESUME",
    "BACK TO",
    "SAME SHOT",
    "SAME ANGLE",
    "INTERCUT",
    "QUICK INTERCUTS",
    "MOVING WITH",
    "MATCH ON",
    "SPLIT SCREEN",
)

# Words that make a standalone all-caps line a sound burst, not a place.
_SOUND_HINTS: frozenset[str] = frozenset(
    {
        "BANG",
        "BOOM",
        "CRASH",
        "CRACK",
        "CLANG",
        "THUD",
        "SLAM",
        "SMASH",
        "SHATTER",
        "SHATTERS",
        "SCREECH",
        "SCREAM",
        "SCREAMS",
        "ROAR",
        "ROARS",
        "RUMBLE",
        "THUNDER",
        "BLAST",
        "WHIRR",
        "WHIR",
        "HISS",
        "BUZZ",
        "BEEP",
        "RING",
        "CHOMP",
        "DING",
        "POP",
        "ZAP",
        "SPLASH",
        "HOWL",
        "HOWLS",
        "SOUND",
        "NOISE",
        "SILENCE",
        "STATIC",
    }
)

# Prepositions that open a place phrase.
_PLACE_PREPS: frozenset[str] = frozenset(
    {
        "IN",
        "INSIDE",
        "AT",
        "NEAR",
        "OUTSIDE",
        "BEHIND",
        "BENEATH",
        "UNDER",
        "ABOVE",
        "OVER",
        "ACROSS",
        "BESIDE",
        "AROUND",
        "THROUGH",
        "DEEPER",
        "ALONG",
        "UNDERWATER",
        "TOWARD",
        "TOWARDS",
    }
)

# Nouns that name a place. Kept in step with the auditor's own list so the two
# passes agree about what counts as somewhere you can stand.
_PLACE_NOUNS: frozenset[str] = frozenset(
    {
        "ROOM",
        "BEDROOM",
        "BATHROOM",
        "KITCHEN",
        "HALL",
        "HALLWAY",
        "OFFICE",
        "HOUSE",
        "HOME",
        "BARN",
        "SHED",
        "GARAGE",
        "DINER",
        "CAFE",
        "BAR",
        "SCHOOL",
        "CLASSROOM",
        "LIBRARY",
        "STATION",
        "YARD",
        "JUNKYARD",
        "STREET",
        "ROAD",
        "HIGHWAY",
        "ALLEY",
        "BRIDGE",
        "TUNNEL",
        "TOWER",
        "FOREST",
        "WOODS",
        "CLEARING",
        "FIELD",
        "MEADOW",
        "HILL",
        "MOUNTAIN",
        "MOUNTAINS",
        "VALLEY",
        "DESERT",
        "ISLAND",
        "BAY",
        "HARBOR",
        "HARBOUR",
        "CLIFF",
        "CLIFFS",
        "FACE",
        "BEACH",
        "SHORE",
        "COAST",
        "LAKE",
        "RIVER",
        "SEA",
        "OCEAN",
        "WATER",
        "SKY",
        "CAVE",
        "CRATER",
        "CANYON",
        "SUB",
        "SUBMARINE",
        "SHIP",
        "BOAT",
        "DECK",
        "TRAIN",
        "CAR",
        "TRUCK",
        "PLANE",
        "BASEMENT",
        "ATTIC",
        "ROOF",
        "PORCH",
        "DOORWAY",
        "STAIRS",
        "LOBBY",
        "PLATFORM",
        "ENTRANCE",
        "COCKPIT",
        "LAB",
        "PLANT",
        "FACTORY",
        "WAREHOUSE",
        "STORE",
        "SHOP",
        "MARKET",
        "CHURCH",
        "PARK",
        "TOWN",
        "CITY",
        "VILLAGE",
        "COUNTRYSIDE",
        "FARM",
        "CAMP",
        "BASE",
        "END",
        "MIDDLE",
        "TOP",
        "BOTTOM",
        "SPACE",
        "EARTH",
        "PLANET",
        "MOON",
        "ORBIT",
        "STARS",
        "GALAXY",
        "ATMOSPHERE",
        "HORIZON",
        "WORLD",
    }
)


@dataclass
class HeadingFix:
    """One paragraph the sanity check retyped."""

    index: int
    text: str
    was: str
    now: str
    reason: str

    def __str__(self) -> str:
        return f"[{self.index}] {self.was} -> {self.now}  ({self.reason})  {self.text}"


@dataclass
class HeadingRepairReport:
    """What the sanity check saw and what it changed."""

    scanned: int = 0
    candidates: int = 0
    fixes: List[HeadingFix] = field(default_factory=list)

    @property
    def promoted_headings(self) -> List[HeadingFix]:
        return [f for f in self.fixes if f.now == ParagraphType.SCENE_HEADING.value]

    @property
    def promoted_shots(self) -> List[HeadingFix]:
        return [f for f in self.fixes if f.now == ParagraphType.SHOT.value]

    def summary(self) -> str:
        return (
            f"heading check: {len(self.fixes)} of {self.candidates} standalone "
            f"all-caps action lines retyped "
            f"({len(self.promoted_headings)} scene headings, "
            f"{len(self.promoted_shots)} shots) across {self.scanned} paragraphs"
        )


def _words(text: str) -> List[str]:
    return [w for w in re.split(r"[^A-Za-z0-9']+", text.upper()) if w]


def _is_allcaps(text: str) -> bool:
    """True when the line carries letters and none of them are lowercase."""
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def _camera_prefix(upper: str) -> Optional[str]:
    """The camera keyword this line opens with, if any."""
    for kw in _CAMERA_PREFIXES:
        if upper == kw or upper.startswith(kw + " ") or upper.startswith(kw + ":"):
            return kw
    return None


def _classify_action_line(text: str) -> tuple[Optional[str], str]:
    """Decide what a standalone all-caps Action line really is.

    Returns ``(paragraph_type, reason)``; a ``None`` type means leave it alone.
    Conservative by construction — every ambiguous shape stays Action, because a
    wrong promotion silently invents a scene, while a missed one just leaves the
    status quo the auditor already handles.
    """
    stripped = text.strip()
    upper = stripped.upper()
    words = _words(stripped)

    if not words:
        return None, ""
    if len(words) > _MAX_SLUG_WORDS:
        return None, "too long to be a slugline"
    # A slugline is a label, not a sentence. Prose that happens to be shouted
    # ends in a full stop; sound bursts end in ! or ?.
    if stripped.endswith((".", "!", "?")) and not re.search(r"\b[A-Z]\.$", stripped):
        return None, "ends like a sentence"
    # "MEMORABILIA, THE WINDS HOWL GROWS LOUDER" — a clause, not a place.
    if set(words) & _SOUND_HINTS:
        return None, "reads as a sound cue"

    # Camera direction → Shot. Checked before the place tests because
    # "AT THE FAR END OF THE JUNKYARD" is a place but "LOOKING IN A
    # SECOND-STORY WINDOW" is a lens move that merely mentions one.
    kw = _camera_prefix(upper)
    if kw:
        return ParagraphType.SHOT.value, f"opens with camera direction '{kw}'"

    # Slugline shapes, strongest signal first.
    if _CONTEXT_PREFIX_RE.match(upper):
        return ParagraphType.SCENE_HEADING.value, "carries a context prefix"
    if _TIME_TAIL_RE.search(upper):
        return ParagraphType.SCENE_HEADING.value, "ends with a time-of-day tail"
    if words[0] in _PLACE_PREPS:
        return ParagraphType.SCENE_HEADING.value, f"opens with '{words[0].lower()}'"
    # A slugline names its place at one end or the other — "THE CLIFF FACE" leads
    # with the article, "COUNTRYSIDE - MOVING WITH THE GIANT" leads with the place.
    # Checking both ends (rather than every word) keeps a long prose line that
    # merely contains "ROOM" from being mistaken for a heading.
    head = words[1] if words[0] in ("A", "AN", "THE") and len(words) > 1 else words[0]
    for candidate in (words[-1], head):
        if candidate in _PLACE_NOUNS:
            return (
                ParagraphType.SCENE_HEADING.value,
                f"names a place ('{candidate}')",
            )

    return None, "no slugline signal"


def _para_type(para: Dict[str, Any]) -> str:
    return str(para.get("@Type") or para.get("Type") or "")


def _text_key(para: Dict[str, Any]) -> str:
    return "Text" if "Text" in para else "text"


def repair_headings(paragraphs: List[Dict[str, Any]]) -> HeadingRepairReport:
    """Retype misclassified sluglines in an assembled paragraph list, in place.

    Only ``Action`` paragraphs are ever touched, and only ones that are short,
    standalone and entirely upper-case. Returns the report; the caller decides
    whether to log it, show it, or fail on it.
    """
    report = HeadingRepairReport(scanned=len(paragraphs))

    for i, para in enumerate(paragraphs):
        if _para_type(para) != ParagraphType.ACTION.value:
            continue
        key = _text_key(para)
        text = str(para.get(key) or "").strip()
        # Paragraph assembly has already merged wrapped lines, so anything still
        # sitting alone here is its own beat — the shape of the text is the only
        # evidence left, and a slugline is normally *followed* by action prose.
        if not text or not _is_allcaps(text):
            continue

        report.candidates += 1
        new_type, reason = _classify_action_line(text)
        if not new_type:
            continue

        para["@Type" if "@Type" in para else "Type"] = new_type
        report.fixes.append(
            HeadingFix(
                index=i,
                text=text,
                was=ParagraphType.ACTION.value,
                now=new_type,
                reason=reason,
            )
        )

    if report.fixes:
        _log.info("PdfScreenplay: %s", report.summary())
        for fix in report.fixes:
            _log.debug("  %s", fix)
    return report


def repair_fdx_file(
    fdx_path: str | Path,
    out_path: Optional[str | Path] = None,
    dry_run: bool = False,
) -> HeadingRepairReport:
    """Run the same sanity check over an existing ``.fdx`` and rewrite it.

    For scripts ingested before this check existed. With ``dry_run=True`` nothing
    is written — use it to see what would change before committing to it.
    """
    import xmltodict

    src = Path(fdx_path)
    raw = xmltodict.parse(src.read_text(encoding="utf-8"))
    content = raw.get("FinalDraft", {}).get("Content", {})
    paragraphs = content.get("Paragraph") or []
    if isinstance(paragraphs, dict):  # a one-paragraph document
        paragraphs = [paragraphs]

    report = repair_headings(paragraphs)

    if report.fixes and not dry_run:
        content["Paragraph"] = paragraphs
        dest = Path(out_path) if out_path else src
        dest.write_text(
            xmltodict.unparse(raw, pretty=True, full_document=True), encoding="utf-8"
        )
        _log.info("Heading repair: rewrote %s", dest)
    return report


if __name__ == "__main__":
    # python -m scriptbuddy.headings <script.fdx> [--apply]
    # Defaults to a dry run so you always see the calls before they land.
    import argparse

    parser = argparse.ArgumentParser(
        description="Sanity-check the scene headings in an existing .fdx."
    )
    parser.add_argument("fdx", help="path to the .fdx to check")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the fixes back (default: dry run, report only)",
    )
    args = parser.parse_args()

    result = repair_fdx_file(args.fdx, dry_run=not args.apply)
    print(result.summary())
    for f in result.fixes:
        print(f"  {f.now:<14} {f.text[:60]:<62} ({f.reason})")
    if result.fixes and not args.apply:
        print("\ndry run — re-run with --apply to write these back")
