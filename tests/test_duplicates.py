import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from formal_agent.cli import main
from formal_agent.storage import load


class DuplicateTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.source = root / "example.py"
        self.state = root / "state.json"
        self.source.write_text("def f(x, y): return x\n\ndef g(z): return z\n", encoding="utf-8")
        self.assertEqual(self.cli("start", str(self.source), str(self.state))[0], 0)

    def cli(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_explicit_duplicate_is_skipped_and_can_be_reopened(self) -> None:
        self.assertEqual(self.cli("answer", str(self.state), "U2", "--answer", "positive")[0], 0)
        self.assertEqual(self.cli(
            "mark-duplicate", str(self.state), "U3", "U2", "--reason", "Same input constraint"
        )[0], 0)
        state = load(self.state)
        self.assertEqual(state["unknowns"][2]["status"], "duplicate")
        self.assertEqual(state["unknowns"][2]["duplicate_of"], "U2")
        report = self.cli("report", str(self.state))[1]
        self.assertIn('User-marked duplicate of: "U2"', report)
        self.assertIn('reason: "Same input constraint"', report)
        self.assertEqual(self.cli("ask", str(self.state), "U3")[0], 1)
        self.assertEqual(self.cli("reopen-unknown", str(self.state), "U3")[0], 0)
        self.assertEqual(load(self.state)["unknowns"][2]["status"], "open")
        self.assertEqual(self.cli("ask", str(self.state), "U3")[0], 0)

    def test_links_are_preserved_on_canonical_question(self) -> None:
        self.assertEqual(self.cli("answer", str(self.state), "U1", "--answer", "identity")[0], 0)
        self.assertEqual(
            self.cli("add-property", str(self.state), "U1", "--statement", "identity")[0], 0
        )
        self.assertEqual(self.cli("link-property", str(self.state), "U3", "P1")[0], 0)
        self.assertEqual(self.cli(
            "mark-duplicate", str(self.state), "U3", "U2", "--reason", "Same question"
        )[0], 0)
        state = load(self.state)
        self.assertIn({"unknown_id": "U3", "property_id": "P1"}, state["links"])
        self.assertIn({"unknown_id": "U2", "property_id": "P1"}, state["links"])
        self.assertIn("U2", self.cli("ask", str(self.state))[1])

    def test_invalid_decisions_do_not_change_investigation(self) -> None:
        original = self.state.read_bytes()
        for options in [
            ("U2", "U2", "same"),
            ("U2", "U4", "different function"),
            ("U2", "U99", "missing"),
            ("U2", "U3", "  "),
        ]:
            result, _, _ = self.cli(
                "mark-duplicate", str(self.state), options[0], options[1],
                "--reason", options[2],
            )
            self.assertEqual(result, 1)
            self.assertEqual(self.state.read_bytes(), original)
        self.assertEqual(self.cli(
            "mark-duplicate", str(self.state), "U3", "U2", "--reason", "same"
        )[0], 0)
        original = self.state.read_bytes()
        self.assertEqual(self.cli(
            "mark-duplicate", str(self.state), "U2", "U1", "--reason", "chain"
        )[0], 1)
        self.assertEqual(self.state.read_bytes(), original)

    def test_corrupted_duplicate_and_old_schema_migration(self) -> None:
        state = load(self.state)
        state["schema_version"] = 5
        self.state.write_text(json.dumps(state), encoding="utf-8")
        original = self.state.read_bytes()
        self.assertEqual(load(self.state)["schema_version"], 6)
        self.assertEqual(self.state.read_bytes(), original)
        self.assertEqual(self.cli(
            "mark-duplicate", str(self.state), "U3", "U2", "--reason", "same"
        )[0], 0)
        state = load(self.state)
        state["unknowns"][2]["duplicate_of"] = "U3"
        self.state.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.cli("show", str(self.state))[0], 1)


if __name__ == "__main__":
    unittest.main()
