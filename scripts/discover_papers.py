#!/usr/bin/env python3
"""Discover image-restoration-agent papers for the human-review queue."""

from __future__ import annotations

import argparse
import html
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "paper-tracker.json"
PAPERS_PATH = ROOT / "data" / "papers.json"
CANDIDATES_PATH = ROOT / "data" / "candidates.json"
REPORT_DIR = ROOT / ".paper-tracker"
USER_AGENT = "awesome-image-restoration-agents/1.0 (+https://github.com/Reflection0427/Awesome-Image-Restoration-Agents)"
ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:)(\d{4}\.\d{4,5})(?:v\d+)?", re.I)


@dataclass
class Candidate:
    title: str
    paper_url: str
    published: str = ""
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    venue: str = ""
    doi: str = ""
    arxiv_id: str = ""
    sources: list[str] = field(default_factory=list)
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    suggested_section: str = "General-Purpose Image Restoration Agents"
    status: str = "needs-review"


def compact(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", compact(value).lower())


def normalize_doi(value: str) -> str:
    value = compact(value).lower()
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value).rstrip(".,;)")


def normalize_arxiv(value: str) -> str:
    match = ARXIV_RE.search(value or "")
    return match.group(1) if match else ""


def request(url: str, params: dict[str, Any], accept: str) -> bytes:
    target = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(target, headers={"User-Agent": USER_AGENT, "Accept": accept})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                return response.read()
        except (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError) as error:
            if attempt == 2:
                raise
            delay = 10 * (attempt + 1) if isinstance(error, urllib.error.HTTPError) and error.code == 429 else 2**attempt
            time.sleep(delay)
    raise RuntimeError("unreachable")


def get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    return json.loads(request(url, params, "application/json").decode("utf-8"))


def fetch_arxiv(query: str, since: str, limit: int) -> list[Candidate]:
    search = " AND ".join(f'all:"{term}"' for term in query.split())
    xml = request("https://export.arxiv.org/api/query", {
        "search_query": search, "start": 0, "max_results": limit,
        "sortBy": "submittedDate", "sortOrder": "descending",
    }, "application/atom+xml").decode("utf-8")
    root = ET.fromstring(xml)
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    rows: list[Candidate] = []
    for entry in root.findall("a:entry", ns):
        published = compact(entry.findtext("a:published", default="", namespaces=ns))[:10]
        if published and published < since:
            continue
        arxiv_id = normalize_arxiv(compact(entry.findtext("a:id", default="", namespaces=ns)))
        rows.append(Candidate(
            title=compact(entry.findtext("a:title", default="", namespaces=ns)),
            paper_url=f"https://arxiv.org/abs/{arxiv_id}",
            published=published,
            authors=[compact(a.findtext("a:name", default="", namespaces=ns)) for a in entry.findall("a:author", ns)],
            abstract=compact(entry.findtext("a:summary", default="", namespaces=ns)),
            venue=compact(entry.findtext("x:journal_ref", default="", namespaces=ns)),
            doi=normalize_doi(entry.findtext("x:doi", default="", namespaces=ns)),
            arxiv_id=arxiv_id,
            sources=["arXiv"],
        ))
    return rows


def reconstruct_abstract(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    return " ".join(word for _, word in sorted((position, word) for word, positions in index.items() for position in positions))


def fetch_openalex(query: str, since: str, limit: int) -> list[Candidate]:
    params: dict[str, Any] = {
        "search": query,
        "filter": f"from_publication_date:{since}",
        "sort": "publication_date:desc",
        "per_page": min(limit, 100),
    }
    if os.getenv("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    if os.getenv("PAPER_TRACKER_EMAIL"):
        params["mailto"] = os.environ["PAPER_TRACKER_EMAIL"]
    rows: list[Candidate] = []
    for item in get_json("https://api.openalex.org/works", params).get("results", []):
        ids = item.get("ids") or {}
        location = item.get("best_oa_location") or item.get("primary_location") or {}
        arxiv_id = normalize_arxiv(ids.get("arxiv", ""))
        doi = normalize_doi(ids.get("doi", ""))
        rows.append(Candidate(
            title=compact(item.get("display_name") or item.get("title")),
            paper_url=ids.get("arxiv") or ids.get("doi") or location.get("landing_page_url") or item.get("id", ""),
            published=compact(item.get("publication_date"))[:10],
            authors=[compact(x.get("author", {}).get("display_name")) for x in item.get("authorships", [])],
            abstract=reconstruct_abstract(item.get("abstract_inverted_index")),
            venue=compact((location.get("source") or {}).get("display_name", "")),
            doi=doi, arxiv_id=arxiv_id, sources=["OpenAlex"],
        ))
    return rows


def crossref_date(item: dict[str, Any]) -> str:
    for key in ("published-online", "published-print", "issued", "created"):
        parts = (item.get(key) or {}).get("date-parts") or []
        if parts:
            return "-".join(str(value).zfill(2) for value in parts[0][:3])
    return ""


def fetch_crossref(query: str, since: str, limit: int) -> list[Candidate]:
    params: dict[str, Any] = {"query.bibliographic": query, "filter": f"from-pub-date:{since}", "rows": limit}
    if os.getenv("PAPER_TRACKER_EMAIL"):
        params["mailto"] = os.environ["PAPER_TRACKER_EMAIL"]
    rows: list[Candidate] = []
    for item in get_json("https://api.crossref.org/works", params).get("message", {}).get("items", []):
        doi = normalize_doi(item.get("DOI", ""))
        rows.append(Candidate(
            title=compact((item.get("title") or [""])[0]),
            paper_url=item.get("URL") or (f"https://doi.org/{doi}" if doi else ""),
            published=crossref_date(item),
            authors=[compact(" ".join(filter(None, [a.get("given"), a.get("family")]))) for a in item.get("author", [])],
            abstract=compact(re.sub(r"<[^>]+>", " ", item.get("abstract", ""))),
            venue=compact((item.get("container-title") or [""])[0]),
            doi=doi, arxiv_id=normalize_arxiv(doi), sources=["Crossref"],
        ))
    return rows


FETCHERS: dict[str, Callable[[str, str, int], list[Candidate]]] = {
    "arxiv": fetch_arxiv,
    "openalex": fetch_openalex,
    "crossref": fetch_crossref,
}


def classify(text: str) -> str:
    domain_terms = (
        "super-resolution", "super resolution", "derain", "rainy image",
        "portrait", "face restoration", "autonomous driving", "adverse weather",
    )
    return (
        "Domain-Specific Restoration Agents"
        if any(term in text for term in domain_terms)
        else "General-Purpose Image Restoration Agents"
    )


def score(candidate: Candidate) -> None:
    title = candidate.title.lower()
    text = f" {candidate.title} {candidate.abstract} {candidate.venue} ".lower()
    restoration = (
        "image restoration", "super-resolution", "super resolution", "deraining",
        "dehazing", "denoising", "deblurring", "low-light enhancement",
    )
    agentic = (
        "agentic", " agent", "multi-agent", "tool use", "tool-use", "tool calling",
        "tool-calling", "task planning", "restoration plan", "restoration trajectory",
        "reflection", "rollback", "memory", "reinforcement learning",
    )
    multimodal = (
        "multimodal large language model", "vision-language model", "vision language model",
        "large language model", "mllm", "vlm",
    )
    points = 0
    reasons: list[str] = []
    if any(term in text for term in restoration):
        points += 6 if any(term in title for term in restoration) else 4
        reasons.append("image-restoration evidence")
    if any(term in text for term in agentic):
        points += 5 if any(term in title for term in agentic) else 3
        reasons.append("agentic planning, tool-use, learning, or memory evidence")
    if any(term in text for term in multimodal):
        points += 2
        reasons.append("multimodal/language-model evidence")
    if not any(term in text for term in restoration):
        points -= 6
        reasons.append("missing image-restoration evidence")
    if not any(term in text for term in agentic):
        points -= 5
        reasons.append("missing agentic decision or tool-use evidence")
    candidate.score = points
    candidate.reasons = reasons
    candidate.suggested_section = classify(text)


def same_paper(left: Candidate, right: Candidate) -> bool:
    if left.arxiv_id and left.arxiv_id == right.arxiv_id:
        return True
    if left.doi and left.doi == right.doi:
        return True
    a, b = normalize_title(left.title), normalize_title(right.title)
    return bool(a and b and (a == b or SequenceMatcher(None, a, b).ratio() >= 0.94))


def merge(left: Candidate, right: Candidate) -> Candidate:
    preferred = left if len(left.abstract) >= len(right.abstract) else right
    other = right if preferred is left else left
    for name in ("paper_url", "published", "venue", "doi", "arxiv_id", "abstract"):
        if not getattr(preferred, name):
            setattr(preferred, name, getattr(other, name))
    if not preferred.authors:
        preferred.authors = other.authors
    preferred.sources = sorted(set(preferred.sources + other.sources))
    return preferred


def existing_candidates() -> list[Candidate]:
    if not CANDIDATES_PATH.exists():
        return []
    return [Candidate(**row) for row in json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))]


def is_listed(candidate: Candidate, papers: list[dict]) -> bool:
    for paper in papers:
        # Published landing pages may omit the preprint ID retained in the figure source.
        arxiv_id = normalize_arxiv(paper["paper_url"]) or normalize_arxiv(
            paper.get("figure", {}).get("source_pdf_url", "")
        )
        listed = Candidate(
            title=paper["title"], paper_url=paper["paper_url"],
            doi=paper.get("doi", ""), arxiv_id=arxiv_id,
        )
        if same_paper(candidate, listed):
            return True
    return False


def write_outputs(candidates: list[Candidate], failures: list[str]) -> None:
    rows = [asdict(item) for item in sorted(candidates, key=lambda item: (-item.score, item.title.lower()))]
    CANDIDATES_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPORT_DIR.mkdir(exist_ok=True)
    lines = [
        "# Image restoration agent candidate report", "",
        f"Candidates awaiting human review: **{len(rows)}**", "",
        "Discovery never edits the formal collection. A paper enters README only after a reviewer verifies it and selects an original Figure from a pinned official PDF.", "",
    ]
    for item in rows:
        lines += [
            f"## [{item['title']}]({item['paper_url']})", "",
            f"- Score: `{item['score']}`",
            f"- Suggested section: `{item['suggested_section']}`",
            f"- Published: `{item['published'] or 'unknown'}`",
            f"- Sources: {', '.join(item['sources'])}",
            f"- Reasons: {', '.join(item['reasons'])}", "",
            compact(item["abstract"])[:700] + ("…" if len(compact(item["abstract"])) > 700 else ""), "",
        ]
    if failures:
        lines += ["## Source failures", ""] + [f"- {failure}" for failure in failures] + [""]
    (REPORT_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")
    if output := os.getenv("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"candidate_count={len(rows)}\n")
            handle.write(f"failure_count={len(failures)}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    since = (date.today() - timedelta(days=int(config["lookback_days"]))).isoformat()
    collected: list[Candidate] = []
    failures: list[str] = []
    for source in config["sources"]:
        consecutive_failures = 0
        for index, query in enumerate(config["queries"]):
            try:
                if source == "arxiv" and index:
                    time.sleep(3.5)  # arXiv asks automated clients to leave a gap between calls.
                collected.extend(FETCHERS[source](query, since, int(config["max_results_per_query"])))
                consecutive_failures = 0
            except Exception as exc:
                failures.append(f"{source} / {query}: {type(exc).__name__}: {exc}")
                consecutive_failures += 1
                if consecutive_failures >= 2:
                    failures.append(f"{source}: remaining queries skipped after two consecutive failures")
                    break
    merged: list[Candidate] = []
    for candidate in collected:
        if not candidate.title:
            continue
        match = next((item for item in merged if same_paper(candidate, item)), None)
        if match:
            merged[merged.index(match)] = merge(match, candidate)
        else:
            merged.append(candidate)
    papers = json.loads(PAPERS_PATH.read_text(encoding="utf-8"))
    ignored = {normalize_title(title) for title in config.get("ignored_titles", [])}
    fresh: list[Candidate] = []
    for candidate in merged:
        score(candidate)
        if candidate.score >= int(config["minimum_score"]) and normalize_title(candidate.title) not in ignored and not is_listed(candidate, papers):
            fresh.append(candidate)
    queue = existing_candidates()
    for candidate in fresh:
        match = next((item for item in queue if same_paper(candidate, item)), None)
        if match:
            queue[queue.index(match)] = merge(match, candidate)
        else:
            queue.append(candidate)
    queue = sorted(queue, key=lambda item: (-item.score, item.title.lower()))[: int(config["max_candidates"])]
    write_outputs(queue, failures)
    print(f"Discovered {len(fresh)} relevant unlisted papers; candidate queue contains {len(queue)}.")
    if failures:
        print(f"Completed with {len(failures)} source-query failures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
