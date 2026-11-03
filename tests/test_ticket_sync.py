"""Tests for GitHub-driven ticket documents; no network or HA dependency."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import sync_tickets as sync


MILESTONE = {
    "number": 1, "title": "Sprint 1", "html_url": "https://github.com/o/r/milestone/1",
    "state": "open", "due_on": None,
}


def issue(number=2, ticket="TM-01", state="open", labels=()):
    return {
        "number": number, "title": f"{ticket}: Test | onderwerp",
        "html_url": f"https://github.com/o/r/issues/{number}", "state": state,
        "labels": [{"name": label} for label in labels],
        "milestone": deepcopy(MILESTONE),
        "body": "- **Prioriteit:** P2\n- **Fase:** 5\n"
                "- **Scope:** Keep this scope.\n"
                "- **Afhankelijkheden:** TM-02\n"
                "- **Acceptatiecriteria:**\n  - Keep this criterion.",
    }


class TestTicketSync(unittest.TestCase):
    def test_status_transitions_and_closed_precedence(self):
        value = issue()
        for labels, expected in (
            ([], "Backlog"), (["status:in-progress"], "In uitvoering"),
            (["status:review"], "Review/validatie"),
            (["status:blocked", "status:review"], "Geblokkeerd"),
        ):
            value["labels"] = [{"name": label} for label in labels]
            self.assertEqual(sync.status(value), expected)
        value["state"] = "closed"
        self.assertEqual(sync.status(value), "Klaar")
        value["state"] = "open"
        self.assertEqual(sync.status(value), "Geblokkeerd")

    def test_links_current_metadata_scope_counts_and_pr_exclusion(self):
        first = issue(labels=("P0", "phase:1", "status:review"))
        second = issue(3, "TM-02", "closed", ("status:backlog",))
        pr = {**issue(50, "TM-50"), "pull_request": {"url": "ignored"}}
        docs = sync.render_documents("o/r", [second, pr, first], [MILESTONE])
        register, board = docs["tickets.md"], docs["kanban.md"]
        self.assertIn("[TM-02](https://github.com/o/r/issues/3)", register)
        self.assertIn("[Sprint 1](https://github.com/o/r/milestone/1)", register)
        self.assertIn("**Prioriteit:** P0", register)
        self.assertNotIn("**Prioriteit:** P2", register)
        self.assertNotIn("**Fase:** 5", register)
        self.assertIn("Keep this scope.", register)
        self.assertIn("Keep this criterion.", register)
        self.assertIn('<a id="tm-01"></a>', register)
        self.assertLess(register.index("## TM-01"), register.index("## TM-02"))
        self.assertNotIn("TM-50", register + board)
        self.assertIn("| Open | 1 / 2 | Niet gepland |", board)
        self.assertIn("Test &#124; onderwerp", board)
        self.assertIn("TM-01", board.split("## Review/validatie")[1].split("## Klaar")[0])
        self.assertIn("TM-02", board.split("## Klaar")[1].split("## Geblokkeerd")[0])
        self.assertEqual(docs, sync.render_documents("o/r", [first, second, pr], [MILESTONE]))

    def test_changed_sprint_priority_and_closed_milestone(self):
        value = issue(labels=("P1", "phase:2"))
        value["milestone"].update(number=2, title="Sprint 2", state="closed",
                                   due_on="2026-12-01T00:00:00Z")
        docs = sync.render_documents("o/r", [value], [value["milestone"]])
        self.assertIn("**Prioriteit:** P1", docs["tickets.md"])
        self.assertIn("[Sprint 2]", docs["kanban.md"])
        self.assertIn("| Afgesloten | 0 / 1 | 2026-12-01 |", docs["kanban.md"])
        value["milestone"] = None
        self.assertIn("Niet ingepland", sync.render_documents("o/r", [value], [])["tickets.md"])

    def test_import_footer_is_removed_and_existing_links_preserved(self):
        value = issue()
        value["body"] = (
            "<!-- imported-from: source -->\n\n"
            "See [TM-02](https://github.com/o/r/issues/3).\n\n---\n\n"
            "Geïmporteerd uit `tickets.md` en `kanban.md` (old state)."
        )
        docs = sync.render_documents("o/r", [value, issue(3, "TM-02")], [])
        self.assertNotIn("old state", docs["tickets.md"])
        self.assertNotIn("[[TM-02]", docs["tickets.md"])

    def test_duplicate_ids_and_empty_results_fail_safely(self):
        for values in ([], [issue(), issue(3)]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                sync.render_documents("o/r", values, [])

    @patch("scripts.sync_tickets.subprocess.run")
    def test_api_fetch_includes_closed_items_and_flattens_pages(self, run):
        run.return_value.stdout = json.dumps([issue()]) + "\n" + json.dumps([issue(3, "TM-02")])
        self.assertEqual(len(sync.fetch_all("o/r", "issues")), 2)
        command = run.call_args.args[0]
        self.assertIn("--paginate", command)
        self.assertIn("state=all", command[-1])

    @patch("scripts.sync_tickets.fetch_all")
    def test_check_is_read_only_and_second_generation_is_unchanged(self, fetch):
        fetch.side_effect = lambda repo, resource: [issue()] if resource == "issues" else [MILESTONE]
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            args = ["sync_tickets.py", "--repository", "o/r", "--output-dir", directory]
            with patch("sys.argv", [*args, "--check"]):
                self.assertEqual(sync.main(), 1)
            self.assertFalse((Path(directory) / "tickets.md").exists())
            with patch("sys.argv", args):
                self.assertEqual(sync.main(), 0)
            with patch("sys.argv", [*args, "--check"]):
                self.assertEqual(sync.main(), 0)


if __name__ == "__main__":
    unittest.main()
