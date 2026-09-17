"""``scriptbuddy`` command line.

    scriptbuddy breakdown SCRIPT [--xlsx OUT] [--json OUT]
    scriptbuddy convert   SCRIPT.pdf [--out OUT.fdx] [--no-ocr]
    scriptbuddy headings  SCRIPT.fdx [--apply]
    scriptbuddy show      SCRIPT [--limit N]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _cmd_breakdown(args: argparse.Namespace) -> int:
    from scriptbuddy import Breakdown, load

    script = load(args.script, ocr=not args.no_ocr)
    bd = Breakdown.from_script(script, discover=False)
    print(bd.summary())
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(bd.to_dict(), indent=2), encoding="utf-8")
        print(f"json  -> {out}")
    if args.xlsx:
        try:
            out = bd.to_excel(args.xlsx)
        except ImportError:
            print(
                "Excel export needs pandas + openpyxl: pip install 'scriptbuddy[excel]'",
                file=sys.stderr,
            )
            return 2
        print(f"xlsx  -> {out}")
    if args.script_json:
        out = script.save(args.script_json)
        print(f"model -> {out}")
    return 0


def _cmd_convert(args: argparse.Namespace) -> int:
    from scriptbuddy.pdf import PdfScreenplay

    out = PdfScreenplay.to_fdx(
        args.script,
        out_path=args.out,
        ocr=not args.no_ocr,
        check_headings=not args.no_heading_check,
    )
    print(f".fdx -> {out}")
    return 0


def _cmd_headings(args: argparse.Namespace) -> int:
    from scriptbuddy.headings import repair_fdx_file

    result = repair_fdx_file(args.script, dry_run=not args.apply)
    print(result.summary())
    for f in result.fixes:
        print(f"  {f.now:<14} {f.text[:60]:<62} ({f.reason})")
    if result.fixes and not args.apply:
        print("\ndry run - re-run with --apply to write these back")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    from scriptbuddy import load_container

    load_container(args.script, ocr=not args.no_ocr).final_draft.print_script(
        limit=args.limit
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scriptbuddy", description="Screenplay parsing and breakdown."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("breakdown", help="entity breakdown with screen time")
    p.add_argument("script", help=".fdx or .pdf")
    p.add_argument("--xlsx", help="write the department workbook here")
    p.add_argument("--json", help="write the breakdown rows as JSON here")
    p.add_argument("--script-json", help="also write the parsed ScriptModel here")
    p.add_argument("--no-ocr", action="store_true", help="skip OCR for scanned PDFs")
    p.set_defaults(func=_cmd_breakdown)

    p = sub.add_parser("convert", help="PDF -> .fdx")
    p.add_argument("script", help=".pdf")
    p.add_argument("--out", help="output .fdx (default: beside the PDF)")
    p.add_argument("--no-ocr", action="store_true")
    p.add_argument("--no-heading-check", action="store_true")
    p.set_defaults(func=_cmd_convert)

    p = sub.add_parser("headings", help="sanity-check sluglines in an .fdx")
    p.add_argument("script", help=".fdx")
    p.add_argument("--apply", action="store_true", help="write fixes back")
    p.set_defaults(func=_cmd_headings)

    p = sub.add_parser("show", help="print the parsed paragraphs")
    p.add_argument("script", help=".fdx or .pdf")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--no-ocr", action="store_true")
    p.set_defaults(func=_cmd_show)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
