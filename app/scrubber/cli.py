#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli.py — Command-line interface for the metadata engine.

This is primarily for developers and CI tests, but it also documents exactly
what the GUI does. Usage:

    python -m app.scrubber.cli photo.jpg shot.png \\
        --mode ai | all \\
        [--location] [--copyright] [--icc] \\
        [--out DIR] [--report]

Behaviour mirrors the web UI:
    * mode "ai"  -> only AI/C2PA/generative metadata is removed
    * mode "all" -> every metadata block is removed
    * original files are NEVER modified; cleaned copies get a "-clean" suffix
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.scrubber.scrubber import clean_image          # noqa: E402
from app.scrubber.scan import scan_file, report_markdown  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ai-metadata-remover",
                                 description=__doc__)
    ap.add_argument("inputs", nargs="+", help="image files to clean")
    ap.add_argument("--mode", choices=("ai", "all"), default="ai")
    ap.add_argument("--location", action="store_true",
                    help="(mode=ai) also remove GPS/geolocation")
    ap.add_argument("--copyright", action="store_true",
                    help="(mode=ai) also remove author/copyright fields")
    ap.add_argument("--icc", action="store_true",
                    help="also remove the ICC colour profile")
    ap.add_argument("--out", default="", help="output directory")
    ap.add_argument("--report", action="store_true",
                    help="write a <name>-report.txt next to each output")
    ap.add_argument("--tag", default="-clean", help="output filename suffix")
    args = ap.parse_args(argv)

    out_dir = Path(args.out) if args.out else None
    options = {"location": args.location, "copyright": args.copyright,
               "icc": args.icc}

    failures = 0
    for path_s in args.inputs:
        src = Path(path_s)
        if not src.is_file():
            print(f"[skip]  {path_s}: not a file")
            failures += 1
            continue
        raw = src.read_bytes()

        pre = scan_file(src)
        print(f"\n=== {src.name} ({len(raw)} bytes) ===")
        print(report_markdown(pre))

        fr = clean_image(raw, filename=src.name, mode=args.mode,
                         options=options, out_tag=args.tag)

        if fr.status == "error":
            print(f"[ERROR] {src.name}: {fr.message}")
            failures += 1
            continue
        if fr.status == "skipped":
            print(f"[skip]  {src.name}: {fr.message}")
            continue

        dest = (out_dir or src.parent) / fr.output_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(fr.output_data or raw)
        print(f"[OK]    {src.name} -> {dest.name} "
              f"({fr.output_bytes} bytes, {fr.elapsed_ms} ms)")
        print(f"        status : {fr.status}")
        for r in fr.removed[:20]:
            print(f"        removed: {r}")
        for w in fr.warnings:
            print(f"        warn   : {w}")

        if args.report:
            rp = dest.with_name(dest.stem + "-report.txt")
            rp.write_text(_build_report(fr), encoding="utf-8")
            print(f"        report : {rp.name}")

    print("\nDone." + ("" if failures == 0 else f"  ({failures} failed)"))
    return 1 if failures else 0


def _build_report(fr) -> str:
    lines = [
        "AI Metadata Remover — per-file report",
        "=" * 46,
        f"File   : {fr.original_name}",
        f"Type   : {fr.extension}",
        f"Mode   : {fr.mode}",
        f"Status : {fr.status}",
        f"Bytes  : {fr.original_bytes} -> {fr.output_bytes}",
        "",
        "Removed / changed:",
    ]
    if fr.removed:
        lines += [f"  - {r}" for r in fr.removed]
    else:
        lines.append("  (nothing to remove)")
    for w in fr.warnings:
        lines.append(f"  ! {w}")
    v = fr.verified_after or {}
    if v:
        lines += ["", "Verified after cleaning:",
                  f"  EXIF    : {'present' if v.get('has_exif') else 'absent'}",
                  f"  GPS     : {'present' if v.get('has_gps') else 'absent'}",
                  f"  XMP     : {'present' if v.get('has_xmp') else 'absent'}",
                  f"  IPTC    : {'present' if v.get('has_iptc') else 'absent'}",
                  f"  AI/C2PA : "
                  f"{'STILL PRESENT' if v.get('ai_metadata_found') else 'none detected'}"]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
