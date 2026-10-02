#!/usr/bin/env python3
"""Promote one human-reviewed candidate, including an original paper Figure."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import generate_readme


ROOT = Path(__file__).resolve().parents[1]
PAPERS = ROOT / "data" / "papers.json"
CANDIDATES = ROOT / "data" / "candidates.json"


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:70].rstrip("-")


def crop_coordinates(value: str) -> list[float]:
    try:
        coordinates = [float(item.strip()) for item in value.split(",")]
    except ValueError as error:
        raise argparse.ArgumentTypeError("crop must contain four comma-separated numbers") from error
    if len(coordinates) != 4 or coordinates[0] >= coordinates[2] or coordinates[1] >= coordinates[3]:
        raise argparse.ArgumentTypeError("crop must be x0,y0,x1,y1 with positive width and height")
    return coordinates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="unique title substring in data/candidates.json")
    parser.add_argument("--section", required=True, choices=generate_readme.SECTION_ORDER)
    parser.add_argument("--method", required=True, help="short method name shown in README")
    parser.add_argument("--year", type=int)
    parser.add_argument("--venue")
    parser.add_argument("--paper-url", help="override the discovered landing page")
    parser.add_argument("--code-url", default="")
    parser.add_argument("--project-url", default="")
    parser.add_argument("--source-pdf-url", required=True, help="pinned official PDF URL")
    parser.add_argument("--source-pdf-sha256", required=True)
    parser.add_argument("--figure-label", required=True, help='for example "Figure 2"')
    parser.add_argument("--figure-kind", required=True, choices=sorted(generate_readme.FIGURE_KINDS))
    parser.add_argument("--figure-page", required=True, type=int, help="one-based PDF page number")
    parser.add_argument("--figure-crop", required=True, type=crop_coordinates, help="x0,y0,x1,y1 in PDF points")
    parser.add_argument("--license-url", default=None)
    args = parser.parse_args()

    if not re.fullmatch(r"[0-9a-fA-F]{64}", args.source_pdf_sha256):
        parser.error("--source-pdf-sha256 must be 64 hexadecimal characters")
    if not re.fullmatch(r"Figure [A-Za-z0-9.-]+", args.figure_label):
        parser.error('--figure-label must look like "Figure 2"')
    if args.figure_page < 1:
        parser.error("--figure-page must be one-based and positive")

    papers = json.loads(PAPERS.read_text(encoding="utf-8"))
    candidates = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    matches = [row for row in candidates if args.query.lower() in row["title"].lower()]
    if len(matches) != 1:
        parser.error(f"expected exactly one candidate match, found {len(matches)}")
    candidate = matches[0]
    paper_id = slug(args.method)
    if any(paper["id"] == paper_id for paper in papers):
        parser.error(f"paper id already exists: {paper_id}")
    year_match = re.search(r"20\d{2}", candidate.get("published", ""))
    year = args.year or (int(year_match.group()) if year_match else 0)
    if not 2020 <= year <= 2100:
        parser.error("a valid --year is required when the candidate has no publication year")

    paper = {
        "id": paper_id,
        "section": args.section,
        "year": year,
        "venue": args.venue or candidate.get("venue") or "To appear",
        "method": args.method,
        "title": candidate["title"],
        "paper_url": args.paper_url or candidate["paper_url"],
        "code_url": args.code_url,
        "project_url": args.project_url,
        "figure": {
            "path": f"assets/frameworks/{paper_id}.png",
            "source_pdf_url": args.source_pdf_url,
            "source_pdf_sha256": args.source_pdf_sha256.lower(),
            "figure_label": args.figure_label,
            "figure_kind": args.figure_kind,
            "page": args.figure_page,
            "crop_pt": args.figure_crop,
            "license_url": args.license_url,
        },
    }
    original_papers = PAPERS.read_text(encoding="utf-8")
    original_candidates = CANDIDATES.read_text(encoding="utf-8")
    papers.append(paper)
    candidates.remove(candidate)
    PAPERS.write_text(json.dumps(papers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    CANDIDATES.write_text(json.dumps(candidates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        generate_readme.load_papers()
        subprocess.run([sys.executable, str(ROOT / "scripts" / "extract_figure.py"), "--paper-id", paper_id], check=True)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_readme.py")], check=True)
    except Exception:
        PAPERS.write_text(original_papers, encoding="utf-8")
        CANDIDATES.write_text(original_candidates, encoding="utf-8")
        (ROOT / paper["figure"]["path"]).unlink(missing_ok=True)
        raise
    print(f"Accepted: {paper['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
