import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import discover_papers  # noqa: E402


class DiscoveryIdentifierTests(unittest.TestCase):
    def setUp(self):
        self.paper = {
            "title": "Published Image Restoration Agent",
            "paper_url": "https://openaccess.thecvf.com/content/example/paper.html",
            "figure": {"source_pdf_url": "https://arxiv.org/pdf/2601.01234v3"},
        }
        self.preprint = discover_papers.Candidate(
            title="An Agent for Image Restoration",
            paper_url="https://arxiv.org/abs/2601.01234v1",
            arxiv_id="2601.01234",
            sources=["arXiv"],
        )

    def test_pinned_pdf_identifies_a_renamed_preprint(self):
        self.assertTrue(discover_papers.is_listed(self.preprint, [self.paper]))
        other = discover_papers.Candidate(
            title=self.preprint.title,
            paper_url="https://arxiv.org/abs/2601.05678",
            arxiv_id="2601.05678",
        )
        self.assertFalse(discover_papers.is_listed(other, [self.paper]))

    def test_scan_excludes_known_preprint_but_keeps_new_work(self):
        new_paper = discover_papers.Candidate(
            title="Foundation Model Agent for Image Restoration",
            paper_url="https://arxiv.org/abs/2601.05678",
            arxiv_id="2601.05678",
            sources=["arXiv"],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            papers = root / "papers.json"
            candidates = root / "candidates.json"
            config = root / "config.json"
            original = json.dumps([self.paper])
            papers.write_text(original, encoding="utf-8")
            candidates.write_text("[]", encoding="utf-8")
            config.write_text(json.dumps({
                "lookback_days": 30,
                "sources": ["arxiv"],
                "queries": ["test query"],
                "max_results_per_query": 10,
                "minimum_score": 7,
                "max_candidates": 10,
            }), encoding="utf-8")

            with (
                patch.object(discover_papers, "PAPERS_PATH", papers),
                patch.object(discover_papers, "CANDIDATES_PATH", candidates),
                patch.object(discover_papers, "REPORT_DIR", root / "report"),
                patch.dict(discover_papers.FETCHERS, {
                    "arxiv": lambda *_: [self.preprint, new_paper],
                }),
                patch.dict(discover_papers.os.environ, {"GITHUB_OUTPUT": ""}),
                patch.object(sys, "argv", ["discover_papers.py", "--config", str(config)]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(discover_papers.main(), 0)

            queue = json.loads(candidates.read_text(encoding="utf-8"))
            self.assertEqual([item["arxiv_id"] for item in queue], [new_paper.arxiv_id])
            self.assertEqual(papers.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
