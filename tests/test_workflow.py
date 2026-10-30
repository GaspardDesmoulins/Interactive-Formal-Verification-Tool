import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from formal_agent.cli import main
from formal_agent.storage import load


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.source = root / "example.py"
        self.state = root / "state.json"
        self.source.write_text("def f(x, y): return x\n", encoding="utf-8")
        self.assertEqual(self.cli("start", str(self.source), str(self.state))[0], 0)

    def cli(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def import_question_repeat(
        self, answered_id: str, function: str = "f", line: int = 1
    ) -> None:
        state = load(self.state)
        original = next(item for item in state["unknowns"] if item["id"] == answered_id)
        proposal = {
            "source_sha256": state["model"]["sha256"],
            "properties": [],
            "unknowns": [{
                "function": function, "line": line,
                "description": "A different detail to review.",
                "question": "  " + original["question"].upper() + "  ",
                "impact": "Another impact to review.",
            }],
        }
        path = self.state.parent / "repeat.json"
        path.write_text(json.dumps(proposal), encoding="utf-8")
        self.assertEqual(self.cli(
            "import-proposals", str(self.state), str(path), "--reasoner", "test"
        )[0], 0)

    def test_suspend_resume_after_answer_and_finish(self) -> None:
        code, output, _ = self.cli("ask", str(self.state), "U2")
        self.assertEqual(code, 0)
        self.assertIn("U2", output)
        self.assertEqual(load(self.state)["workflow"], {
            "phase": "awaiting_answer", "pending_unknown_id": "U2"
        })
        original = self.state.read_bytes()
        self.assertEqual(self.cli("answer", str(self.state), "U1", "--answer", "a")[0], 1)
        self.assertEqual(self.state.read_bytes(), original)
        with patch("builtins.input", side_effect=EOFError):
            self.assertEqual(self.cli("answer", str(self.state), "U2")[0], 1)
        self.assertEqual(self.state.read_bytes(), original)
        self.assertEqual(self.cli("answer", str(self.state), "U2", "--answer", "positive")[0], 0)
        self.assertEqual(load(self.state)["workflow"]["phase"], "active")
        self.assertEqual(self.cli("finish", str(self.state))[0], 1)
        self.assertEqual(self.cli("finish", str(self.state), "--allow-open")[0], 0)
        report = self.cli("report", str(self.state))[1]
        self.assertIn("Investigation phase: finished", report)
        self.assertIn('"U1" [open]', report)
        self.assertIn("Verification results: none", report)
        self.assertEqual(self.cli("answer", str(self.state), "U1", "--answer", "a")[0], 1)
        self.assertEqual(self.cli("resume", str(self.state))[0], 0)
        self.assertEqual(self.cli("answer", str(self.state), "U1", "--answer", "a")[0], 0)

    def test_prioritize_questions_linked_to_accepted_properties(self) -> None:
        self.assertEqual(self.cli("answer", str(self.state), "U1", "--answer", "identity")[0], 0)
        self.assertEqual(
            self.cli("add-property", str(self.state), "U1", "--statement", "identity")[0], 0
        )
        self.assertEqual(self.cli("accept-property", str(self.state), "P1")[0], 0)
        self.assertEqual(self.cli("link-property", str(self.state), "U3", "P1")[0], 0)
        result, output, _ = self.cli("ask", str(self.state))
        self.assertEqual(result, 0)
        self.assertIn("U3", output)
        self.assertEqual(self.cli("resume", str(self.state))[0], 0)
        self.assertEqual(load(self.state)["workflow"]["phase"], "active")

    def test_auto_selection_skips_answered_question_matches_but_warns(self) -> None:
        self.assertEqual(self.cli("answer", str(self.state), "U2", "--answer", "positive")[0], 0)
        self.import_question_repeat("U2")
        result, output, warning = self.cli("ask", str(self.state))
        self.assertEqual(result, 0)
        self.assertIn("Awaiting answer for U1:", output)
        self.assertIn("U4 matches U2", warning)
        self.assertIn("equivalence is not assumed", warning)
        self.assertEqual(load(self.state)["unknowns"][3]["status"], "open")
        self.assertEqual(self.cli("resume", str(self.state))[0], 0)

        original = self.state.read_bytes()
        result, _, error = self.cli("ask", str(self.state), "U4")
        self.assertEqual(result, 1)
        self.assertIn("same normalized question as answered U2", error)
        self.assertEqual(self.state.read_bytes(), original)
        result, _, warning = self.cli("ask", str(self.state), "U4", "--allow-reask")
        self.assertEqual(result, 0)
        self.assertIn("U4 matches U2", warning)
        with patch("builtins.input", return_value="different context"):
            self.assertEqual(self.cli("answer", str(self.state), "U4")[0], 0)
        state = load(self.state)
        self.assertEqual(state["unknowns"][1]["answer"], "positive")
        self.assertEqual(state["unknowns"][3]["answer"], "different context")

    def test_all_repeated_questions_require_explicit_review(self) -> None:
        for unknown_id in ("U1", "U2", "U3"):
            self.assertEqual(self.cli("answer", str(self.state), unknown_id, "--answer", "ok")[0], 0)
        self.import_question_repeat("U2")
        original = self.state.read_bytes()
        result, _, error = self.cli("ask", str(self.state))
        self.assertEqual(result, 1)
        self.assertIn("U4 matches U2", error)
        self.assertIn("--allow-reask", error)
        self.assertEqual(self.state.read_bytes(), original)
        result, _, error = self.cli("ask", str(self.state), "--allow-reask")
        self.assertEqual(result, 1)
        self.assertIn("requires an explicit unknown ID", error)
        self.assertEqual(self.state.read_bytes(), original)
        self.assertEqual(self.cli(
            "mark-duplicate", str(self.state), "U4", "U2", "--reason", "reviewed"
        )[0], 0)
        self.assertEqual(load(self.state)["unknowns"][3]["status"], "duplicate")

    def test_direct_interactive_answer_requires_explicit_reask(self) -> None:
        self.assertEqual(self.cli("answer", str(self.state), "U2", "--answer", "positive")[0], 0)
        self.import_question_repeat("U2")
        original = self.state.read_bytes()
        with patch("builtins.input", return_value="different") as input_mock:
            result, _, error = self.cli("answer", str(self.state), "U4")
            self.assertEqual(result, 1)
            self.assertIn("--allow-reask", error)
            input_mock.assert_not_called()
        self.assertEqual(self.state.read_bytes(), original)
        with patch("builtins.input", return_value="different"):
            self.assertEqual(self.cli("answer", str(self.state), "U4", "--allow-reask")[0], 0)
        self.assertEqual(load(self.state)["unknowns"][3]["answer"], "different")

    def test_supplied_answer_does_not_prompt_or_require_reask_override(self) -> None:
        self.assertEqual(self.cli("answer", str(self.state), "U2", "--answer", "positive")[0], 0)
        self.import_question_repeat("U2")
        with patch("builtins.input") as input_mock:
            self.assertEqual(
                self.cli("answer", str(self.state), "U4", "--answer", "different")[0], 0
            )
            input_mock.assert_not_called()
        self.assertEqual(load(self.state)["unknowns"][3]["answer"], "different")

    def test_same_question_on_different_function_is_not_blocked(self) -> None:
        self.source.write_text("def f(x, y): return x\n\ndef g(z): return z\n", encoding="utf-8")
        self.state = self.state.parent / "other-state.json"
        self.assertEqual(self.cli("start", str(self.source), str(self.state))[0], 0)
        self.assertEqual(self.cli("answer", str(self.state), "U2", "--answer", "positive")[0], 0)
        self.import_question_repeat("U2", function="g", line=3)
        result, output, error = self.cli("ask", str(self.state), "U6")
        self.assertEqual(result, 0)
        self.assertIn("Awaiting answer for U6:", output)
        self.assertEqual(error, "")

    def test_invalid_workflow_and_legacy_migration(self) -> None:
        state = load(self.state)
        state["workflow"] = {"phase": "awaiting_answer", "pending_unknown_id": "U99"}
        self.state.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.cli("show", str(self.state))[0], 1)
        state["schema_version"] = 4
        del state["workflow"]
        del state["conflicts"]
        del state["scenarios"]
        del state["scenario_suggestions"]
        del state["test_call_analyses"]
        self.state.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(load(self.state)["workflow"]["phase"], "active")
        self.assertEqual(self.cli("ask", str(self.state), "U1")[0], 0)
        self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))["schema_version"], 10)


if __name__ == "__main__":
    unittest.main()
