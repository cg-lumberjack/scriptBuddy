
"""
Entity type classification for the Project Auditor.

The script parser routes every all-caps token it finds into a pool, and its only
real signal is a small vocabulary lookup - everything that isn't a known SFX /
editorial / action word lands in ``props``. On a real screenplay that pool ends up
holding verbs ("GASPS"), shouted dialogue fragments ("RIGHT NOW"), mini-slugs
("A CLEARING IN THE FOREST") and, worst of all, lead characters whose name happens
to appear all-caps in an action line of a scene where they never speak.

This module re-decides the type of an audit row *after* the fact, when the whole
script is on disk and every signal is available: who speaks, what the scene
headings say, which block each mention came from and how the word is shaped.

The rules are deliberately flat, ordered and self-describing - every suggestion
carries the rule that fired and a human sentence explaining it, so the auditor UI
can show *why* and an artist can tune the vocabularies by hand when a rule misses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from scriptbuddy.script import (
    _ACTION_VOCAB,
    _EDITORIAL_MARKERS,
    _SFX_VOCAB,
)

# The eight values the resolver's Type dropdown offers.
ENTITY_TYPES = (
    "character",
    "location",
    "prop",
    "sound",
    "editorial",
    "action",
    "fx",
    "ignore",
)

# Group nouns that name people on screen. A screenplay writes crowds all-caps the
# same way it writes props, but they cast and model as characters, not set dressing.
_CROWD_VOCAB: frozenset[str] = frozenset(
    {
        "MAN",
        "MEN",
        "WOMAN",
        "WOMEN",
        "BOY",
        "BOYS",
        "GIRL",
        "GIRLS",
        "KID",
        "KIDS",
        "CHILD",
        "CHILDREN",
        "BABY",
        "GUY",
        "GUYS",
        "CROWD",
        "CROWDS",
        "MOB",
        "GANG",
        "GROUP",
        "PEOPLE",
        "PERSON",
        "VOICE",
        "VOICES",
        "NARRATOR",
        "ANNOUNCER",
        "OPERATOR",
        "DISPATCHER",
        "SOLDIER",
        "SOLDIERS",
        "AGENT",
        "AGENTS",
        "OFFICER",
        "OFFICERS",
        "COP",
        "COPS",
        "POLICE",
        "GUARD",
        "GUARDS",
        "TROOPER",
        "TROOPERS",
        "FARMER",
        "FARMERS",
        "FISHERMAN",
        "FISHERMEN",
        "SAILOR",
        "SAILORS",
        "WORKER",
        "WORKERS",
        "STUDENT",
        "STUDENTS",
        "TEACHER",
        "TEACHERS",
        "DOCTOR",
        "NURSE",
        "WAITRESS",
        "WAITER",
        "BARTENDER",
        "CLERK",
        "DRIVER",
        "PILOT",
        "CAPTAIN",
        "GENERAL",
        "SERGEANT",
        "LIEUTENANT",
        "MOTHER",
        "FATHER",
        "MOM",
        "DAD",
        "SON",
        "DAUGHTER",
        "BROTHER",
        "SISTER",
        "EVERYBODY",
        "EVERYONE",
        "SOMEONE",
        "ANYONE",
        "OTHERS",
        "LOCAL",
        "LOCALS",
        "TOWNSFOLK",
        "BYSTANDERS",
        "ONLOOKERS",
        "PASSENGERS",
        "TWINS",
    }
)

# Visual atmospherics - things a comp/FX department makes, not a modeller.
# Deliberately disjoint from _SFX_VOCAB so the sound pass can run first without
# stealing anything that is purely something you *see*.
_FX_VOCAB: frozenset[str] = frozenset(
    {
        "FIRE",
        "FLAME",
        "FLAMES",
        "FLAMING",
        "SMOKE",
        "SMOKING",
        "STEAM",
        "SPARK",
        "SPARKS",
        "SPARKING",
        "SPARKLING",
        "EMBERS",
        "ASH",
        "ELECTRICITY",
        "ELECTRICAL",
        "LIGHTNING",
        "ARC",
        "ARCS",
        "GLOW",
        "GLOWING",
        "FLASH",
        "FLASHES",
        "FLARE",
        "BEAM",
        "BEAMS",
        "RAY",
        "RAYS",
        "LASER",
        "PLASMA",
        "ENERGY",
        "SHOCKWAVE",
        "MIST",
        "FOG",
        "HAZE",
        "DUST",
        "CLOUD",
        "CLOUDS",
        "SHADOW",
        "SHADOWS",
        "SILHOUETTE",
        "REFLECTION",
    }
)

# Words that must never start or end a real asset name - a token bounded by one
# of these is a slice of a sentence the all-caps scan cut in the wrong place.
_EDGE_STOPWORDS: frozenset[str] = frozenset(
    {
        "A",
        "AN",
        "THE",
        "AND",
        "OR",
        "BUT",
        "OF",
        "TO",
        "IN",
        "ON",
        "AT",
        "BY",
        "FOR",
        "FROM",
        "WITH",
        "AS",
        "IS",
        "ARE",
        "WAS",
        "WERE",
        "BE",
        "THAT",
        "THIS",
        "THESE",
        "THOSE",
        "IT",
        "ITS",
        "HIS",
        "HER",
        "THEIR",
        "HE",
        "SHE",
        "THEY",
        "WE",
        "YOU",
        "I",
        "MY",
        "YOUR",
        "OUR",
        "NOT",
        "NO",
        "SO",
        "IF",
        "THEN",
        "THAN",
        "INTO",
        "ONTO",
        "UP",
        "DOWN",
        # apostrophe wreckage: "YOU'RE DOING" -> "RE DOING"
        "RE",
        "VE",
        "LL",
        "S",
        "T",
        "D",
        "M",
    }
)

# Prepositions that make an all-caps line read as a place rather than a thing.
_LOCATION_PREPS: frozenset[str] = frozenset(
    {
        "IN",
        "INSIDE",
        "AT",
        "ON",
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
        "TOWARD",
        "TOWARDS",
        "DEEPER",
        "BACK",
        "OFF",
        "ALONG",
        "BY",
    }
)

# Nouns that name a place even without a preposition in front of them. A screenplay
# writes an establishing shot as a bare all-caps line ("DEEP SPACE", "EARTH") with
# no INT./EXT. and no scene-heading element behind it, so the head noun is the only
# thing left to recognise it by.
_LOCATION_NOUNS: frozenset[str] = frozenset(
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
        "BEACH",
        "SHORE",
        "COAST",
        "LAKE",
        "RIVER",
        "SEA",
        "OCEAN",
        "WATER",
        "UNDERWATER",
        "SKY",
        "CAVE",
        "CRATER",
        "CANYON",
        "BASEMENT",
        "ATTIC",
        "ROOF",
        "PORCH",
        "DOORWAY",
        "STAIRS",
        "LOBBY",
        "PLATFORM",
        "DECK",
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
        # Off-planet establishing shots — the opening of a film is often nothing
        # but these, written as plain action lines.
        "SPACE",
        "EARTH",
        "PLANET",
        "MOON",
        "ORBIT",
        "STARS",
        "GALAXY",
        "ATMOSPHERE",
        "HORIZON",
    }
)

# The tail end of a slugline. On its own each is a time-of-day or camera note the
# all-caps scan tore off a heading - never an asset in its own right.
_SLUG_NOISE: frozenset[str] = frozenset(
    {
        "DAY",
        "NIGHT",
        "DAWN",
        "DUSK",
        "MORNING",
        "EVENING",
        "AFTERNOON",
        "MIDNIGHT",
        "NOON",
        "SUNSET",
        "SUNRISE",
        "MOVING",
        "SAME",
        "SAME SHOT",
        "CONTINUOUS",
        "MOMENTS",
        "PRESENT",
        "PAST",
    }
)

# "LATER", "A LITTLE LATER", "MOMENTS LATER", "CONTINUOUS", "SAME TIME" - a
# time-cut caption, which is editorial, not an asset.
_TIME_CUT_RE = re.compile(
    r"^(?:A |THE )?(?:FEW |LITTLE |SHORT |LONG |BRIEF |MOMENTS? |SECONDS? |"
    r"MINUTES? |HOURS? |DAYS? |WEEKS? |YEARS? |TIME |SOME )*"
    r"(?:LATER|AFTER|CONTINUOUS|SAME TIME|SAME MOMENT|SAME)$"
)

# INT./EXT. and the "- DAY" / "- NIGHT" tail that ends a slugline.
_SLUG_HEAD_RE = re.compile(r"^(?:INT|EXT|I/E|INT\.?/EXT)\b")
_SLUG_TAIL_RE = re.compile(
    r"[-—]\s*(?:DAY|NIGHT|DAWN|DUSK|MORNING|EVENING|AFTERNOON|"
    r"CONTINUOUS|LATER|SAME)\s*$"
)

# Single-word verb shapes: MOANING, SLAMMED, FIZZLES. Not proof on their own -
# they only fire once every stronger rule has passed on the row.
_GERUND_RE = re.compile(r"^[A-Z]{3,}(?:ING|ED)$")
_THIRD_PERSON_RE = re.compile(r"^[A-Z]{2,}(?<![AEIOUS])S$")


@dataclass
class TypeSuggestion:
    """One classifier verdict for one audit row."""

    type: str
    confidence: int
    rule: str
    reason: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "confidence": self.confidence,
            "rule": self.rule,
            "reason": self.reason,
        }


def _words(label: str) -> List[str]:
    """The label's uppercase word tokens, punctuation stripped."""
    return [w for w in re.split(r"[^A-Za-z0-9']+", label.upper()) if w]


class EntityClassifier:
    """Re-decides an audit row's type from the whole script, not just a vocabulary.

    Build it once per audit (the indexes are script-wide), then call
    :meth:`classify` per row. Every rule is pure - the same row always yields the
    same suggestion - so a miss is fixed by editing a vocabulary above, not by
    re-running anything.
    """

    def __init__(self, script_model: Any = None, manifest: Any = None) -> None:
        self.script_model = script_model
        self.manifest = manifest

        # Every name the script ever puts in front of a line of dialogue.
        self.speakers: set[str] = set()
        # Scene headings + parsed location strings, uppercased.
        self.headings: List[str] = []
        # Text of every TRANSITION block ("CUT TO:", "DISSOLVE TO:").
        self.transitions: set[str] = set()
        # Text of every ACTION block that is entirely uppercase - a mini-slug.
        self.slug_lines: set[str] = set()
        # Labels/aliases already sitting in the manifest's character pool.
        self.character_labels: set[str] = set()
        self.location_labels: set[str] = set()

        self._index_script()
        self._index_manifest()

    # -- indexing ---------------------------------------------------------

    def _index_script(self) -> None:
        for scene in getattr(self.script_model, "scenes", None) or []:
            for name in list(getattr(scene, "speakers", None) or []):
                if name:
                    self.speakers.add(name.strip().upper())
            for field in ("heading", "location", "region"):
                value = (getattr(scene, field, "") or "").strip()
                if value:
                    self.headings.append(value.upper())
            for block in getattr(scene, "blocks", None) or []:
                btype = getattr(block.block_type, "value", str(block.block_type))
                text = (getattr(block, "text", "") or "").strip()
                speaker = (getattr(block, "speaker", "") or "").strip()
                if speaker:
                    self.speakers.add(speaker.upper())
                if btype == "character" and text:
                    # A CHARACTER element IS a speaker cue, cleaned of (V.O.)/(CONT'D).
                    self.speakers.add(re.sub(r"\(.*?\)", "", text).strip().upper())
                elif btype == "transition" and text:
                    self.transitions.add(text.rstrip(":").strip().upper())
                elif btype == "scene_heading" and text:
                    self.headings.append(text.upper())
                elif btype == "action" and text and text.upper() == text:
                    self.slug_lines.add(text.strip().upper())

    def _index_manifest(self) -> None:
        for pool_name, sink in (
            ("characters", self.character_labels),
            ("locations", self.location_labels),
        ):
            pool = getattr(self.manifest, pool_name, None) or {}
            for token in pool.values():
                label = (getattr(token, "label", "") or "").strip()
                if label:
                    sink.add(label.upper())
                for alias in getattr(token, "aliases", None) or []:
                    if alias:
                        sink.add(alias.strip().upper())

    # -- row helpers ------------------------------------------------------

    @staticmethod
    def _row_names(row: Dict[str, Any]) -> List[str]:
        """Every uppercase string that names this row's entity."""
        names = [(row.get("primary_label") or "").strip().upper()]
        names += [(a or "").strip().upper() for a in (row.get("aliases") or []) if a]
        return [n for n in names if n]

    @staticmethod
    def _contexts(row: Dict[str, Any]) -> List[Dict[str, str]]:
        return list(row.get("block_contexts") or [])

    def _speaks(self, row: Dict[str, Any], names: Sequence[str]) -> Optional[str]:
        """The name this row speaks under, if the script ever gives it a line."""
        for name in names:
            if name in self.speakers:
                return name
        for ctx in self._contexts(row):
            speaker = (ctx.get("speaker") or "").strip().upper()
            if speaker and speaker in names:
                return speaker
            if ctx.get("block_type") == "character":
                cue = re.sub(r"\(.*?\)", "", ctx.get("text") or "").strip().upper()
                if cue and cue in names:
                    return cue
        return None

    def _is_named_location(self, names: Sequence[str]) -> Optional[str]:
        """The row's name *is* a place the script already names - it sits in the
        location pool, or a scene heading reads exactly like it. Checked before
        anything else because the FDX parser sometimes files a slugline
        ("HOGARTH'S BEDROOM") as a speaker cue as well as a heading."""
        for name in names:
            if len(name) < 4:
                continue
            if name in self.location_labels:
                return name
            for heading in self.headings:
                # Strip the INT./EXT. head and the "- NIGHT" tail before comparing.
                core = _SLUG_TAIL_RE.sub("", _SLUG_HEAD_RE.sub("", heading))
                if name == core.strip(" .-"):
                    return heading
        return None

    def _in_heading(self, names: Sequence[str]) -> Optional[str]:
        """The scene heading that mentions this row's name, if any. Matched on word
        boundaries so 'WOO' does not light up on 'THE WOODS', and only for names
        long enough that a coincidental hit is unlikely."""
        for name in names:
            if len(name) < 4 or name in _EDGE_STOPWORDS:
                continue
            pattern = re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)")
            for heading in self.headings:
                if pattern.search(heading):
                    return heading
        return None

    @staticmethod
    def _only_in_dialogue(row: Dict[str, Any]) -> bool:
        """True when every mention of this entity is inside a spoken line - the
        all-caps run is a character shouting, not an asset on set.

        A dialogue block with no speaker is a parser mis-type (action prose filed
        as dialogue), so it does not count as evidence either way."""
        contexts = EntityClassifier._contexts(row)
        if not contexts:
            return False
        return all(
            ctx.get("block_type") == "dialogue" and (ctx.get("speaker") or "").strip()
            for ctx in contexts
        )

    # -- the rules --------------------------------------------------------

    def classify(self, row: Dict[str, Any]) -> TypeSuggestion:
        """Decide what ``row`` really is. First rule to fire wins."""
        names = self._row_names(row)
        label = names[0] if names else ""
        words = _words(label)
        word_set = set(words)

        if not label or not words:
            return TypeSuggestion(
                "ignore", 60, "empty", "No screenplay label to judge."
            )

        # 0 -- slugline debris ("NIGHT", "MOVING") before anything tries to make
        #      an asset out of it.
        if label in _SLUG_NOISE:
            return TypeSuggestion(
                "ignore",
                85,
                "slug-noise",
                f"'{label}' is the time-of-day / camera tail of a scene heading.",
            )

        # 1 -- the name IS a slugline the script already uses. Checked first so a
        #      mis-filed heading can't be mistaken for a speaker.
        named_place = self._is_named_location(names)
        if named_place:
            return TypeSuggestion(
                "location",
                96,
                "named-location",
                f"'{label}' is a place the script heads a scene with.",
            )

        # 1 -- it speaks, so it is a character. Beats every other signal: a lead
        #      whose name shows up all-caps in an action line lands here.
        spoken_as = self._speaks(row, names)
        if spoken_as:
            return TypeSuggestion(
                "character",
                100,
                "speaks",
                f"'{spoken_as}' is credited with dialogue in the script.",
            )

        # 1b -- a multi-word label that carries a speaker's name is that character's
        #       all-caps introduction in an action line ("GEORGE WASHINGTON, 44,
        #       enters") or a titled mention ("GENERAL WARREN"): the cue says
        #       WASHINGTON, the intro says the whole name.
        if len(words) > 1:
            for speaker in self.speakers:
                parts = speaker.split()
                if not parts or any(len(p) < 3 or p in _EDGE_STOPWORDS for p in parts):
                    continue
                if all(p in word_set for p in parts):
                    return TypeSuggestion(
                        "character",
                        90,
                        "name-part",
                        f"'{label}' contains '{speaker}', who has dialogue in the script.",
                    )

        # 2 -- already a character elsewhere in the manifest (same name, other pool).
        if any(n in self.character_labels for n in names):
            return TypeSuggestion(
                "character",
                95,
                "character-pool",
                f"'{label}' is already tracked as a character in this script.",
            )

        # 3 -- editorial callouts and time cuts.
        if label in _EDITORIAL_MARKERS or label in self.transitions:
            return TypeSuggestion(
                "editorial",
                95,
                "editorial-vocab",
                f"'{label}' is a standard editorial / transition callout.",
            )
        if _TIME_CUT_RE.match(label):
            return TypeSuggestion(
                "editorial",
                90,
                "time-cut",
                f"'{label}' reads as a time-cut caption, not an asset.",
            )
        if any(c.get("block_type") == "transition" for c in self._contexts(row)):
            return TypeSuggestion(
                "editorial",
                85,
                "transition-block",
                f"'{label}' was harvested from a transition element.",
            )

        # 4 -- crowds and off-screen voices are people, not set dressing. Only the
        #      head noun counts, so "OLD FARMER" is cast but "CAPTAIN YELLS" is not.
        if words[-1] in _CROWD_VOCAB:
            return TypeSuggestion(
                "character",
                80,
                "crowd-noun",
                f"'{label}' names people on screen - cast it, don't model it.",
            )

        # 5 -- places: scene headings, location grammar, place nouns.
        heading = self._in_heading(names)
        if heading:
            return TypeSuggestion(
                "location",
                92,
                "scene-heading",
                f"'{label}' appears in the scene heading '{heading[:60]}'.",
            )
        if _SLUG_HEAD_RE.match(label) or _SLUG_TAIL_RE.search(label):
            return TypeSuggestion(
                "location",
                88,
                "slugline-shape",
                f"'{label}' is shaped like a scene slugline.",
            )
        if label in self.slug_lines and (
            words[0] in _LOCATION_PREPS or word_set & _LOCATION_NOUNS
        ):
            return TypeSuggestion(
                "location",
                80,
                "mini-slug",
                f"'{label}' is a standalone all-caps shot slug naming a place.",
            )
        if (
            len(words) > 1
            and words[0] in _LOCATION_PREPS
            and (word_set & _LOCATION_NOUNS)
        ):
            return TypeSuggestion(
                "location",
                75,
                "location-phrase",
                f"'{label}' reads as a place ('{words[0].lower()} the ...').",
            )

        # 6 -- a slice of a sentence the all-caps scan cut badly. Runs ahead of the
        #      vocabularies so "SOUND OF" is dropped instead of filed as an SFX.
        if words[0] in _EDGE_STOPWORDS or words[-1] in _EDGE_STOPWORDS:
            return TypeSuggestion(
                "ignore",
                72,
                "sentence-fragment",
                f"'{label}' starts or ends mid-sentence - a bad capture, not an asset.",
            )

        # 7 -- things you hear.
        if word_set & _SFX_VOCAB:
            hit = sorted(word_set & _SFX_VOCAB)[0]
            return TypeSuggestion(
                "sound", 88, "sfx-vocab", f"'{hit}' is a sound-effect word."
            )
        if word_set & {"SOUND", "SOUNDS", "NOISE", "NOISES", "ECHO", "SILENCE"}:
            return TypeSuggestion(
                "sound",
                85,
                "sound-noun",
                f"'{label}' names something heard rather than seen.",
            )
        if len(words) >= 2 and len(set(words)) == 1:
            return TypeSuggestion(
                "sound",
                80,
                "onomatopoeia",
                f"'{label}' is a repeated beat - onomatopoeia, not a prop.",
            )

        # 8 -- things you see but never build.
        if word_set & _FX_VOCAB:
            hit = sorted(word_set & _FX_VOCAB)[0]
            return TypeSuggestion(
                "fx", 85, "fx-vocab", f"'{hit}' is an atmospheric / effects element."
            )

        # 9 -- beats and movement.
        if word_set & _ACTION_VOCAB:
            hit = sorted(word_set & _ACTION_VOCAB)[0]
            return TypeSuggestion(
                "action",
                85,
                "action-vocab",
                f"'{hit}' is a movement cue, not an object.",
            )

        # 10 -- a bare place name. Deliberately last of the location rules: it runs
        #       after the sound / fx / action vocabularies so a phrase that merely
        #       mentions a place while doing something else ("BLASTS OFF INTO THE
        #       NIGHT SKY") stays with the verb that drives it. What reaches here is
        #       a noun and nothing more — an establishing shot the parser filed as a
        #       prop because it carried no INT./EXT. and no scene-heading element.
        #       Mirrors the crowd-noun rule above: head noun decides.
        if words[-1] in _LOCATION_NOUNS:
            return TypeSuggestion(
                "location",
                70,
                "place-noun",
                f"'{words[-1]}' names a place — this reads as an establishing shot.",
            )

        # 11 -- all-caps emphasis inside a spoken line is never an asset.
        if self._only_in_dialogue(row):
            return TypeSuggestion(
                "ignore",
                75,
                "shouted-dialogue",
                f"'{label}' only ever appears as emphasis inside dialogue.",
            )

        # 12 -- verb morphology, once nothing stronger has fired.
        if len(words) == 1 and _GERUND_RE.match(label):
            return TypeSuggestion(
                "action",
                65,
                "verb-shape",
                f"'{label}' is a verb form (-ing/-ed), so it describes a beat.",
            )
        if len(words) == 1 and _THIRD_PERSON_RE.match(label):
            return TypeSuggestion(
                "action",
                55,
                "verb-shape",
                f"'{label}' reads as a verb in the present tense, not a noun.",
            )
        if len(words) >= 4:
            return TypeSuggestion(
                "action",
                50,
                "long-phrase",
                f"'{label}' is a whole clause - a described beat, not one object.",
            )

        # 13 -- default: a physical thing on set.
        return TypeSuggestion(
            "prop",
            40,
            "default",
            f"No stronger signal - '{label}' is treated as a physical prop.",
        )

    def classify_rows(self, rows: Sequence[Dict[str, Any]]) -> List[TypeSuggestion]:
        return [self.classify(row) for row in rows]
