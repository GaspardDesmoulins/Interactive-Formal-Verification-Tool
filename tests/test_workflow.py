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

    def test_invalid_workflow_and_legacy_migration(self) -> None:
        state = load(self.state)
        state["workflow"] = {"phase": "awaiting_answer", "pending_unknown_id": "U99"}
        self.state.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.cli("show", str(self.state))[0], 1)
        state["schema_version"] = 4
        del state["workflow"]
        self.state.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(load(self.state)["workflow"]["phase"], "active")
        self.assertEqual(self.cli("ask", str(self.state), "U1")[0], 0)
        self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))["schema_version"], 6)


if __name__ == "__main__":
    unittest.main()
