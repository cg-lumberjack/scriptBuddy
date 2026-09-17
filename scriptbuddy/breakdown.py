"""The breakdown: every entity the script names, where it appears, how long it
is on screen.

A :class:`Breakdown` is computed from a :class:`~scriptbuddy.script.ScriptModel`
that has had :meth:`~scriptbuddy.script.ScriptModel.discover_all_assets` run
(so its per-scene prop / sound / editorial / action pools are populated). Each
entity becomes an :class:`Entity` row: which scenes it is in, how many blocks
mention it, and an estimated screen time. Characters get precise block-level
time (the seconds of dialogue they speak plus action blocks that name them);
scene-anchored entities (locations, sounds, fx, editorials) get the sum of the
scenes they appear in - a producer's "how much of the cut involves this".

The rows are plain data. :meth:`Breakdown.to_excel` writes the classic
department workbook (one sheet per pool); :meth:`Breakdown.to_dict` is the
same thing as JSON.
"""

from __future__ import annotations

import io
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from scriptbuddy.block import ScriptBlockType
from scriptbuddy.classify import EntityClassifier
from scriptbuddy.script import ScriptModel

# Classifier verdict -> breakdown pool.
_TYPE_TO_POOL = {
    "character": "characters",
    "location": "locations",
    "prop": "props",
    "sound": "sounds",
    "action": "actions",
    "editorial": "editorials",
    "fx": "fx",
}

# The pools a breakdown reports, in the order the workbook's sheets appear.
POOLS = (
    "scenes",
    "characters",
    "locations",
    "props",
    "sounds",
    "actions",
    "editorials",
    "fx",
)


def _readable(seconds: float) -> str:
    if seconds < 60:
        return f"{round(seconds, 1)}s"
    return f"{int(seconds // 60)}m {round(seconds % 60, 1)}s"


class Entity(BaseModel):
    """One row of the breakdown: a named thing and where the script puts it."""

    label: str
    pool: str
    scene_ids: List[str] = Field(default_factory=list)
    # Blocks that mention the entity (dialogue lines for a character).
    mentions: int = 0
    screen_seconds: float = 0.0
    # Why the classifier moved it here (empty when it arrived by vocabulary and
    # the classifier agreed).
    rule: str = ""
    reason: str = ""

    @property
    def first_appearance(self) -> str:
        return min(self.scene_ids) if self.scene_ids else ""

    @property
    def screen_time(self) -> str:
        return _readable(self.screen_seconds)

    def as_row(self) -> Dict[str, object]:
        return {
            "Label": self.label,
            "Scenes": len(self.scene_ids),
            "Mentions": self.mentions,
            "First Appearance": self.first_appearance,
            "Screen Time": self.screen_time,
            "Screen Seconds": round(self.screen_seconds, 1),
            "Scene IDs": ", ".join(self.scene_ids),
            "Why": self.reason,
        }


class SceneRow(BaseModel):
    scene_id: str
    heading: str
    scope: Optional[str] = None
    time: Optional[str] = None
    location: Optional[str] = None
    blocks: int = 0
    cast: List[str] = Field(default_factory=list)
    screen_seconds: float = 0.0

    def as_row(self) -> Dict[str, object]:
        return {
            "Scene": self.scene_id,
            "Heading": self.heading,
            "INT/EXT": self.scope or "",
            "Time": self.time or "",
            "Location": self.location or "",
            "Blocks": self.blocks,
            "Cast": ", ".join(self.cast) if self.cast else "NO DIALOGUE",
            "Screen Time": _readable(self.screen_seconds),
            "Screen Seconds": round(self.screen_seconds, 1),
        }


class Breakdown(BaseModel):
    title: str = ""
    scenes: List[SceneRow] = Field(default_factory=list)
    characters: List[Entity] = Field(default_factory=list)
    locations: List[Entity] = Field(default_factory=list)
    props: List[Entity] = Field(default_factory=list)
    sounds: List[Entity] = Field(default_factory=list)
    actions: List[Entity] = Field(default_factory=list)
    editorials: List[Entity] = Field(default_factory=list)
    fx: List[Entity] = Field(default_factory=list)

    # -- construction -----------------------------------------------------

    @classmethod
    def from_script(
        cls, script: ScriptModel, discover: bool = True, classify: bool = True
    ) -> Breakdown:
        """Build the breakdown.

        ``discover`` runs the all-caps entity pass first; pass ``False`` when the
        script's pools were already curated by hand. ``classify`` then re-decides
        every discovered entity's pool with the whole script in view (see
        :class:`~scriptbuddy.classify.EntityClassifier`): slugline debris and
        shouted dialogue are dropped, a lead whose name only appears all-caps in
        action lines moves from props to characters, and so on. A moved row
        keeps the rule and reason that moved it.
        """
        if discover:
            script.discover_all_assets()

        scene_secs: Dict[str, float] = {}
        rows: List[SceneRow] = []
        # pool -> UPPER label -> Entity
        pools: Dict[str, Dict[str, Entity]] = {p: {} for p in POOLS if p != "scenes"}
        char_secs: Dict[str, float] = defaultdict(float)
        char_mentions: Dict[str, int] = defaultdict(int)
        # (pool, UPPER label) -> the blocks that mention it, for the classifier.
        contexts: Dict[tuple, List[Dict[str, str]]] = defaultdict(list)

        def touch(pool: str, label: str, scene_id: str) -> Entity:
            key = label.strip().upper()
            ent = pools[pool].get(key)
            if ent is None:
                ent = pools[pool][key] = Entity(label=label.strip(), pool=pool)
            if scene_id not in ent.scene_ids:
                ent.scene_ids.append(scene_id)
            ent.mentions += 1
            return ent

        for scene in script.scenes:
            sid = scene.scene_id
            secs = scene.duration_secs or sum(
                (b.duration_secs or 0.0) for b in scene.blocks
            )
            scene_secs[sid] = secs
            cast: set[str] = set()

            for block in scene.blocks:
                b_secs = block.duration_secs or 0.0
                if block.speaker:
                    key = block.speaker.strip().upper()
                    cast.add(key)
                    if block.block_type == ScriptBlockType.DIALOGUE:
                        char_secs[key] += b_secs
                        char_mentions[key] += 1
                for name in block.characters:
                    key = name.strip().upper()
                    if key != (block.speaker or "").strip().upper():
                        char_secs[key] += b_secs
                        char_mentions[key] += 1
                    cast.add(key)

            for name in sorted(cast | {c.strip().upper() for c in scene.characters}):
                ent = touch("characters", name, sid)
                ent.mentions = char_mentions[name]
                ent.screen_seconds = char_secs[name]

            for loc in (scene.location, scene.region):
                if loc:
                    touch("locations", loc, sid)
            for pool in ("props", "sounds", "actions", "editorials", "fx"):
                for label in getattr(scene, pool):
                    touch(pool, label, sid)
                    key = label.strip().upper()
                    for block in scene.blocks:
                        if key in (block.text or "").upper():
                            contexts[(pool, key)].append(
                                {
                                    "block_type": block.block_type.value,
                                    "speaker": block.speaker or "",
                                    "text": block.text or "",
                                }
                            )

            rows.append(
                SceneRow(
                    scene_id=sid,
                    heading=(scene.heading or "").upper().strip(),
                    scope=scene.scope,
                    time=scene.time,
                    location=scene.location,
                    blocks=len(scene.blocks),
                    cast=sorted(cast),
                    screen_seconds=secs,
                )
            )

        if classify:
            cls._refine(script, pools, contexts)

        # Scene-anchored pools: screen time is the sum of the scenes they're in.
        for pool in ("locations", "props", "sounds", "actions", "editorials", "fx"):
            for ent in pools[pool].values():
                ent.screen_seconds = sum(scene_secs.get(s, 0.0) for s in ent.scene_ids)

        # A character the classifier promoted out of an action-line mention has
        # no dialogue time; give it the scene-anchored figure so it still ranks.
        for ent in pools["characters"].values():
            if ent.rule and not ent.screen_seconds:
                ent.screen_seconds = sum(scene_secs.get(s, 0.0) for s in ent.scene_ids)

        def ranked(pool: str) -> List[Entity]:
            return sorted(
                pools[pool].values(),
                key=lambda e: (-e.screen_seconds, -e.mentions, e.label),
            )

        return cls(
            title=script.title,
            scenes=sorted(rows, key=lambda r: r.scene_id),
            **{pool: ranked(pool) for pool in pools},
        )

    @staticmethod
    def _refine(
        script: ScriptModel,
        pools: Dict[str, Dict[str, Entity]],
        contexts: Dict[tuple, List[Dict[str, str]]],
    ) -> None:
        """Re-route the vocabulary-discovered pools through the classifier."""
        clf = EntityClassifier(script_model=script)
        for pool in ("props", "sounds", "actions", "editorials", "fx"):
            for key, ent in list(pools[pool].items()):
                verdict = clf.classify(
                    {
                        "primary_label": ent.label,
                        "aliases": [],
                        "block_contexts": contexts.get((pool, key), []),
                    }
                )
                target = _TYPE_TO_POOL.get(verdict.type)
                if target == pool:
                    continue
                del pools[pool][key]
                if target is None:  # "ignore"
                    continue
                ent.pool = target
                ent.rule = verdict.rule
                ent.reason = verdict.reason
                existing = pools[target].get(key)
                if existing is None:
                    pools[target][key] = ent
                    continue
                for sid in ent.scene_ids:
                    if sid not in existing.scene_ids:
                        existing.scene_ids.append(sid)
                existing.mentions += ent.mentions

    # -- output -----------------------------------------------------------

    def pool(self, name: str):
        if name not in POOLS:
            raise KeyError(f"unknown pool {name!r}; one of {POOLS}")
        return getattr(self, name)

    def rows(self, name: str) -> List[Dict[str, object]]:
        return [r.as_row() for r in self.pool(name)]

    def to_dict(self) -> Dict[str, List[Dict[str, object]]]:
        return {name: self.rows(name) for name in POOLS}

    def to_excel(self, out_path: Optional[str | Path] = None):
        """One sheet per pool. Returns the written path, or an in-memory
        ``BytesIO`` when ``out_path`` is omitted. Needs the ``[excel]`` extra
        (pandas + openpyxl)."""
        import pandas as pd

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            for name in POOLS:
                rows = self.rows(name)
                if not rows:
                    continue
                sheet_name = name.capitalize()
                df = pd.DataFrame(rows).drop(columns=["Screen Seconds"], errors="ignore")
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                sheet = writer.sheets[sheet_name]
                for col_cells in sheet.columns:
                    width = max(
                        len(str(c.value)) if c.value is not None else 0
                        for c in col_cells
                    )
                    letter = col_cells[0].column_letter
                    sheet.column_dimensions[letter].width = min(max(10, width + 2), 80)
        if out_path is None:
            buffer.seek(0)
            return buffer
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(buffer.getvalue())
        return out

    def summary(self) -> str:
        total = sum(r.screen_seconds for r in self.scenes)
        lines = [
            f"{self.title or 'Untitled'}: {len(self.scenes)} scenes, "
            f"~{_readable(total)} estimated"
        ]
        for name in POOLS[1:]:
            ents = self.pool(name)
            if not ents:
                continue
            top = ", ".join(e.label for e in ents[:5])
            more = " ..." if len(ents) > 5 else ""
            lines.append(f"  {name:<11}{len(ents):>4}   {top}{more}")
        return "\n".join(lines)
