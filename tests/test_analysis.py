from __future__ import annotations

import json
import unittest
from pathlib import Path

from tg_stories.analysis import AnalysisConfig, build_analysis
from tg_stories.html_report import render_html


FIXTURE = Path(__file__).parent / "fixtures" / "sample_full_export.json"


class AnalysisTests(unittest.TestCase):
    def test_builds_core_metrics(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        analysis = build_analysis(payload, AnalysisConfig(top_limit=10, min_reaction_rate_views=1))

        self.assertEqual(analysis["summary"]["story_count"], 3)
        self.assertEqual(analysis["summary"]["unique_viewers"], 3)
        self.assertEqual(analysis["summary"]["view_events"], 6)
        self.assertEqual(analysis["summary"]["reaction_events"], 3)

        top = analysis["regularity"]["all_time_top"][0]
        self.assertEqual(top["user"]["username"], "alice")
        self.assertEqual(top["viewed_story_count"], 3)
        self.assertEqual(top["view_rate"], 1.0)
        self.assertEqual(top["reaction_count"], 2)

        reaction_top = analysis["reactions"]["top_by_absolute_count"][0]
        self.assertEqual(reaction_top["user"]["username"], "alice")

    def test_renders_html(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        analysis = build_analysis(payload, AnalysisConfig(top_limit=10, min_reaction_rate_views=1))
        html = render_html(analysis, source_file="sample.json")

        self.assertIn("Telegram Stories Analytics", html)
        self.assertIn("All-Time Regularity", html)
        self.assertIn("Alice", html)


if __name__ == "__main__":
    unittest.main()
