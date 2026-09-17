# scriptbuddy

Screenplay parsing and breakdown for Python. Final Draft `.fdx` or a
screenplay PDF in; scenes, cast, locations, props, sounds and editorial
markers out — per scene, with estimated screen time — as Python objects, JSON,
or the department workbook a line producer expects.

```bash
pip install scriptbuddy[excel]
scriptbuddy breakdown my_script.pdf --xlsx my_script_breakdown.xlsx
```

```
my_script: 116 scenes, ~113m 42.5s estimated
  characters   60   PHIN, WASHINGTON, BLAIR, LEE, CHARLES LEE ...
  locations    68   WASHINGTON'S WAR ROOM, HARPER HOUSE, FORT WASHINGTON ...
  props         5   SPEAR, PEARL NECKLACE, FIFE ...
  sounds        4   CLICK, BOOM, THUMP ...
  editorials    2   CUT TO, SMASH CUT TO
```

## What it does

- **Reads `.fdx` natively.** The Final Draft XML becomes a validated pydantic
  model (`FinalDraft` / `Paragraph`), and writes back out again, so a script can
  round-trip through your tooling and land back in Final Draft.
- **Reads screenplay PDFs.** A PDF has no structure, only glyphs at (x, y). The
  adapter classifies each line into a Final Draft paragraph type by its
  left-margin band (calibrated per document), assembles paragraphs, and emits a
  real `.fdx`. Scanned scripts go through `ocrmypdf` when it is installed.
  Overlay watermarks are detected and preserved.
- **Normalizes to one model.** `ScriptModel` → `ScriptScene` → `ScriptBlock`:
  scenes split on sluglines with INT/EXT, location and time parsed; dialogue
  attributed to its speaker across Final Draft's hard-return splits; a screen
  time estimate per block.
- **Breaks it down.** Every all-caps run in action and dialogue is routed to a
  pool (character, prop, sound, action cue, editorial marker), then a
  whole-script classifier re-decides each one with everything in view — who
  speaks, what the sluglines say, which block a mention came from. A lead
  introduced as `GEORGE WASHINGTON` in an action line lands in the cast, not
  the prop list, and the row says why.
- **Sanity-checks headings.** A post-parse pass catches sluglines the PDF
  classifier missed or over-called, as a dry-run report or applied in place.

## Python

```python
from scriptbuddy import load, Breakdown

script = load("my_script.pdf")          # or .fdx
for scene in script.scenes:
    print(scene.scene_id, scene.heading, f"{scene.duration_mins:.1f} min", scene.speakers)

bd = Breakdown.from_script(script)
for c in bd.characters[:10]:
    print(c.label, len(c.scene_ids), "scenes", c.screen_time, c.reason)

bd.to_excel("breakdown.xlsx")           # one sheet per pool
rows = bd.to_dict()                     # the same thing as plain dicts
script.save("script.json")              # the normalized model
```

Lower-level pieces are all importable: `ScriptContainer.load(fdx)`,
`PdfScreenplay.to_fdx(pdf)`, `EntityClassifier`, `repair_headings`,
`ScriptTimingConfig` / `block_duration` for tuning the pacing model.

## Command line

```
scriptbuddy breakdown SCRIPT [--xlsx OUT] [--json OUT] [--script-json OUT]
scriptbuddy convert   SCRIPT.pdf [--out OUT.fdx] [--no-ocr]
scriptbuddy headings  SCRIPT.fdx [--apply]
scriptbuddy show      SCRIPT [--limit N]
```

## Extending the models

`ScriptModel`, `ScriptScene` and `ScriptBlock` allow extra fields and are meant
to be subclassed. `ScriptContainer.to_script_model(..., model_cls=, scene_cls=,
block_cls=)` builds your subclasses directly, so a pipeline can hang its own
fields and methods off the same objects without re-implementing the parse.

## Install

```bash
pip install scriptbuddy            # parsing + breakdown
pip install scriptbuddy[excel]     # + workbook export (pandas, openpyxl)
```

Requires Python 3.10+. PDF parsing uses PyMuPDF. For image-only PDFs, install
[ocrmypdf](https://ocrmypdf.readthedocs.io/) and it is picked up automatically.

## Development

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"     # .venv/bin/pip on macOS / Linux
.venv/Scripts/python -m pytest
```

## License

TBD.
