#!/usr/bin/env python3
"""Generate README tables from reviewed paper and original-figure metadata."""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "papers.json"
README = ROOT / "README.md"

SECTION_ORDER = [
    "General-Purpose Image Restoration Agents",
    "Domain-Specific Restoration Agents",
]
FIGURE_KINDS = {
    "framework", "architecture", "pipeline", "overview", "workflow",
}
OFFICIAL_PDF_HOSTS = {"arxiv.org"}


def load_papers() -> list[dict]:
    papers = json.loads(DATA.read_text(encoding="utf-8"))
    required = {
        "id", "section", "year", "venue", "method", "title", "paper_url",
        "code_url", "project_url", "figure",
    }
    ids: set[str] = set()
    paths: set[str] = set()
    for paper in papers:
        missing = required - paper.keys()
        if missing:
            raise ValueError(f"{paper.get('id', '<unknown>')} missing {sorted(missing)}")
        if paper["id"] in ids:
            raise ValueError(f"duplicate paper id: {paper['id']}")
        ids.add(paper["id"])
        if paper["section"] not in SECTION_ORDER:
            raise ValueError(f"{paper['id']} has an unknown section")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", paper["id"]):
            raise ValueError(f"invalid paper id: {paper['id']}")
        if not 2020 <= paper["year"] <= 2100:
            raise ValueError(f"{paper['id']} has an invalid year")
        if not paper["paper_url"].startswith("https://"):
            raise ValueError(f"{paper['id']} paper URL must use HTTPS")
        figure = paper["figure"]
        figure_required = {
            "path", "source_pdf_url", "source_pdf_sha256", "figure_label",
            "figure_kind", "page", "crop_pt", "license_url",
        }
        missing_figure = figure_required - figure.keys()
        if missing_figure:
            raise ValueError(f"{paper['id']} figure missing {sorted(missing_figure)}")
        expected_path = f"assets/frameworks/{paper['id']}.png"
        if figure["path"] != expected_path or figure["path"] in paths:
            raise ValueError(f"{paper['id']} has an invalid or duplicate figure path")
        paths.add(figure["path"])
        if figure["figure_kind"] not in FIGURE_KINDS:
            raise ValueError(f"{paper['id']} has an invalid figure kind")
        if not re.fullmatch(r"Figure [A-Za-z0-9.-]+", figure["figure_label"]):
            raise ValueError(f"{paper['id']} has an invalid Figure label")
        if not isinstance(figure["page"], int) or figure["page"] < 1:
            raise ValueError(f"{paper['id']} has an invalid PDF page")
        crop = figure["crop_pt"]
        if not (
            isinstance(crop, list) and len(crop) == 4
            and all(isinstance(value, (int, float)) for value in crop)
            and crop[0] < crop[2] and crop[1] < crop[3]
        ):
            raise ValueError(f"{paper['id']} has invalid crop coordinates")
        if not re.fullmatch(r"[0-9a-f]{64}", figure["source_pdf_sha256"]):
            raise ValueError(f"{paper['id']} has an invalid PDF SHA-256")
        parsed = urlparse(figure["source_pdf_url"])
        if parsed.scheme != "https" or parsed.hostname not in OFFICIAL_PDF_HOSTS:
            raise ValueError(f"{paper['id']} does not use an approved official PDF host")
    return papers


def resource_cell(paper: dict) -> str:
    resources = [f'<a href="{paper["paper_url"]}">Paper</a>']
    if paper["code_url"]:
        resources.append(f'<a href="{paper["code_url"]}">Code</a>')
    if paper["project_url"]:
        resources.append(f'<a href="{paper["project_url"]}">Project</a>')
    return " · ".join(resources)


def framework_cell(paper: dict) -> str:
    figure = paper["figure"]
    license_link = (
        f' · <a href="{figure["license_url"]}">License</a>'
        if figure["license_url"] else ""
    )
    return (
        f'<a href="{figure["path"]}"><img src="{figure["path"]}" width="320" '
        f'alt="Original {html.escape(figure["figure_label"], quote=True)} from '
        f'{html.escape(paper["title"], quote=True)}"></a><br>'
        f'<sub>Original paper figure: {html.escape(figure["figure_label"])} · '
        f'PDF page {figure["page"]} · Source: '
        f'<a href="{figure["source_pdf_url"]}">pinned PDF</a><br>'
        f'© Paper authors/publisher. All rights remain with the original owner.'
        f'{license_link}</sub>'
    )


def table_for(papers: list[dict], section: str) -> list[str]:
    rows = [
        "| Conference / Journal | Method | Title | Resources | Framework |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]
    selected = sorted(
        (paper for paper in papers if paper["section"] == section),
        key=lambda paper: (-paper["year"], paper["venue"], paper["method"]),
    )
    for paper in selected:
        title = html.escape(paper["title"]).replace("|", "&#124;")
        rows.append(
            f'| **{html.escape(paper["venue"])}** | **{html.escape(paper["method"])}** | '
            f'{title} | {resource_cell(paper)} | {framework_cell(paper)} |'
        )
    return rows


def render(papers: list[dict]) -> str:
    counts = Counter(paper["section"] for paper in papers)
    general = "General-Purpose Image Restoration Agents"
    domain = "Domain-Specific Restoration Agents"
    lines = [
        "# Awesome Image Restoration Agents", "",
        "[![Validate](https://github.com/Reflection0427/Awesome-Image-Restoration-Agents/actions/workflows/validate.yml/badge.svg)](https://github.com/Reflection0427/Awesome-Image-Restoration-Agents/actions/workflows/validate.yml)",
        "[![Discover Papers](https://github.com/Reflection0427/Awesome-Image-Restoration-Agents/actions/workflows/discover-papers.yml/badge.svg)](https://github.com/Reflection0427/Awesome-Image-Restoration-Agents/actions/workflows/discover-papers.yml)", "",
        "A curated list of papers on **agentic image restoration**, including multimodal agents that perceive degradations, plan or learn restoration trajectories, invoke specialized tools, evaluate intermediate outputs, and adapt through reflection, memory, or reinforcement learning.", "",
        "本仓库整理图像恢复 Agent 研究。表格最后一列 `Framework` 直接展示从固定版本论文 PDF 中按页裁取的作者原始 Framework / Architecture / Pipeline 图；不使用自绘图、博客截图或 AI 生成图。", "",
        f"**{len(papers)} reviewed papers · {counts[general]} general-purpose agents · {counts[domain]} domain-specific agents**", "",
        "## Scope", "",
        "The general-purpose section covers agents that handle multiple or unknown degradations through dynamic planning, tool use, feedback, learned policies, or evolving memory. The domain-specific section covers the same agentic pattern specialized for super-resolution, adverse-weather perception, deraining, or portrait restoration. Pure all-in-one restoration networks and prompt-conditioned models without an autonomous decision or tool-use loop are outside the core list.", "",
        "## General-Purpose Image Restoration Agents", "",
    ]
    lines.extend(table_for(papers, general))
    lines.extend(["", "## Domain-Specific Restoration Agents", ""])
    lines.extend(table_for(papers, domain))
    lines.extend([
        "", "## Figure policy", "",
        "Every Framework image is cropped from the linked pinned author PDF. Metadata in `data/papers.json` records the PDF URL, SHA-256, one-based page number, Figure label, and crop rectangle in PDF points. The crop preserves the paper figure and caption; only whitespace cropping and lossless PNG compression are applied. Copyright and all other rights remain with the paper authors or publishers.", "",
        "Reproduce or verify all committed figures with:", "",
        "```bash",
        "python -m pip install -r requirements.txt",
        "python scripts/extract_figure.py --all",
        "python scripts/extract_figure.py --all --verify-only",
        "python scripts/generate_readme.py --check",
        "python -m unittest discover -s tests -v",
        "```", "",
        "## Automatic paper tracking", "",
        "A scheduled workflow searches arXiv, OpenAlex, and Crossref every Monday and opens or updates a candidate pull request. Discovery never adds a paper to this table automatically: a reviewer must read the PDF, verify relevance and metadata, select an author-created Figure, and validate its crop before acceptance.", "",
        "## Contributing", "",
        "Contributions are welcome through issues and pull requests. See [the review guide](docs/PAPER_TRACKER.md).", "",
        "## License", "",
        "Repository code and curator-authored metadata are released under the MIT License. Paper figures remain the property of their respective authors or publishers.", "",
    ])
    return "\n".join(lines)


def build() -> str:
    return render(load_papers())


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = build()
    if args.check:
        if not README.exists() or README.read_text(encoding="utf-8") != output:
            print("README.md is stale; run python scripts/generate_readme.py")
            return 1
        print("README.md is up to date.")
        return 0
    README.write_text(output, encoding="utf-8")
    print(f"Rendered README.md from {len(load_papers())} reviewed papers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
