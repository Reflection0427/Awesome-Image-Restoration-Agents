#!/usr/bin/env python3
"""Reproduce reviewed paper-figure crops from pinned official PDFs."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

import pymupdf
from PIL import Image, ImageStat


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "papers.json"
CACHE = ROOT / ".figure-work" / "pdfs"
TARGET_DPI = 220
MAX_EDGE = 2400
MAX_BYTES = 5 * 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_pdf(paper: dict) -> Path:
    figure = paper["figure"]
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{paper['id']}.pdf"
    if not path.exists():
        request = urllib.request.Request(
            figure["source_pdf_url"],
            headers={"User-Agent": "awesome-image-restoration-agents/1.0"},
        )
        temporary = path.with_suffix(".pdf.part")
        with urllib.request.urlopen(request, timeout=180) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        temporary.replace(path)
    actual = sha256(path)
    if actual != figure["source_pdf_sha256"]:
        raise ValueError(f"{paper['id']}: PDF SHA-256 mismatch; expected {figure['source_pdf_sha256']}, got {actual}")
    return path


def validate_image(path: Path) -> None:
    if not path.exists():
        raise ValueError(f"missing image: {path.relative_to(ROOT)}")
    if path.suffix.lower() != ".png":
        raise ValueError(f"figure must be PNG: {path.relative_to(ROOT)}")
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"figure exceeds 5 MiB: {path.relative_to(ROOT)}")
    with Image.open(path) as image:
        image.load()
        if image.format != "PNG" or image.mode not in {"RGB", "RGBA", "L"}:
            raise ValueError(f"invalid PNG: {path.relative_to(ROOT)}")
        if min(image.size) < 180 or max(image.size) < 600 or max(image.size) > MAX_EDGE:
            raise ValueError(f"unexpected dimensions {image.size}: {path.relative_to(ROOT)}")
        if ImageStat.Stat(image.convert("L")).var[0] < 15:
            raise ValueError(f"image appears blank: {path.relative_to(ROOT)}")


def render(paper: dict) -> Path:
    figure = paper["figure"]
    pdf_path = download_pdf(paper)
    document = pymupdf.open(pdf_path)
    if figure["page"] > len(document):
        raise ValueError(f"{paper['id']}: PDF page {figure['page']} does not exist")
    page = document[figure["page"] - 1]
    crop = pymupdf.Rect(*figure["crop_pt"])
    if crop.is_empty or crop not in page.rect:
        raise ValueError(f"{paper['id']}: crop {list(crop)} is outside page {list(page.rect)}")
    dpi = min(TARGET_DPI, int(MAX_EDGE * 72 / max(crop.width, crop.height)))
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), clip=crop, alpha=False)
    target = ROOT / figure["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".png.part")
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    image.save(temporary, format="PNG", optimize=True, compress_level=9)
    temporary.replace(target)
    validate_image(target)
    return target


def load_selected(paper_id: str | None) -> list[dict]:
    papers = json.loads(DATA.read_text(encoding="utf-8"))
    if paper_id is None:
        return papers
    selected = [paper for paper in papers if paper["id"] == paper_id]
    if not selected:
        raise ValueError(f"unknown paper id: {paper_id}")
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--paper-id")
    group.add_argument("--all", action="store_true")
    parser.add_argument("--verify-only", action="store_true", help="verify committed PNGs without downloading PDFs")
    args = parser.parse_args()
    for paper in load_selected(args.paper_id):
        path = ROOT / paper["figure"]["path"]
        if args.verify_only:
            validate_image(path)
            print(f"verified {path.relative_to(ROOT)}")
        else:
            rendered = render(paper)
            print(f"rendered {rendered.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
