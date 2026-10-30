import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from formal_agent.cli import main
from formal_agent.storage import load


class ConflictTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.source = root / "example.py"
        self.state = root / "state.json"
        self.source.write_text("def f(): return 1\n", encoding="utf-8")
        self.assertEqual(self.cli("start", str(self.source), str(self.state))[0], 0)

    def cli(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_conflict_requires_explicit_decision_and_is_reported(self) -> None:
        self.assertEqual(self.cli("answer", str(self.state), "U1", "--answer", "one")[0], 0)
        self.assertEqual(self.cli(
            "record-conflict", str(self.state), "U1",
            "--description", "The documentation and test expect different results.",
        )[0], 0)
        result, _, error = self.cli("finish", str(self.state))
        self.assertEqual(result, 1)
        self.assertIn("1 disagreements", error)
        report = self.cli("report", str(self.state))[1]
        self.assertIn("C1 [open]", report)
        self.assertEqual(self.cli(
            "resolve-conflict", str(self.state), "C1", "--resolution", "Ask the maintainer."
        )[0], 0)
        state = load(self.state)
        self.assertEqual(state["conflicts"][0]["resolution"], "Ask the maintainer.")
        report = self.cli("report", str(self.state))[1]
        self.assertIn('User decision: "Ask the maintainer."; not verified', report)
        self.assertIn("Verification results: none", report)
        self.assertEqual(self.cli("finish", str(self.state))[0], 0)

    def test_invalid_decisions_and_corruption_are_rejected(self) -> None:
        original = self.state.read_bytes()
        for args in [
            ("record-conflict", str(self.state), "U99", "--description", "conflict"),
            ("record-conflict", str(self.state), "U1", "--description", " "),
            ("resolve-conflict", str(self.state), "C1", "--resolution", "decision"),
        ]:
            self.assertEqual(self.cli(*args)[0], 1)
            self.assertEqual(self.state.read_bytes(), original)
        self.assertEqual(self.cli(
            "record-conflict", str(self.state), "U1", "--description", "conflict"
        )[0], 0)
        state = load(self.state)
        state["conflicts"][0]["unknown_id"] = "U99"
        self.state.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.cli("show", str(self.state))[0], 1)

    def test_schema_six_migrates_without_writing_until_changed(self) -> None:
        state = load(self.state)
        state["schema_version"] = 6
        del state["conflicts"]
        del state["scenarios"]
        del state["scenario_suggestions"]
        self.state.write_text(json.dumps(state), encoding="utf-8")
        original = self.state.read_bytes()
        self.assertEqual(load(self.state)["conflicts"], [])
        self.assertEqual(self.state.read_bytes(), original)
        self.assertEqual(self.cli(
            "record-conflict", str(self.state), "U1", "--description", "conflict"
        )[0], 0)
        self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))["schema_version"], 9)


if __name__ == "__main__":
    unittest.main()
