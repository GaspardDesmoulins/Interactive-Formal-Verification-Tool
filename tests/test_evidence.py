import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from formal_agent.cli import main
from formal_agent.evidence import MAX_EVIDENCE_BYTES
from formal_agent.storage import load


class EvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "example.py"
        self.docs = self.root / "requirements.txt"
        self.tests = self.root / "test_target.py"
        self.marker = self.root / "executed"
        self.state = self.root / "state.json"
        self.source.write_text("def f(x): return x\n", encoding="utf-8")
        self.docs.write_text("The caller expects positive inputs.", encoding="utf-8")
        self.tests.write_text(
            f"open({str(self.marker)!r}, 'w').write('executed')\n"
            "assert f(1) == 1\n",
            encoding="utf-8",
        )

    def cli(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def start(self) -> int:
        return self.cli(
            "start", str(self.source), str(self.state),
            "--evidence", str(self.docs), "--evidence", str(self.tests),
        )[0]

    def test_explicit_references_are_hashed_reported_and_not_executed(self) -> None:
        self.assertEqual(self.start(), 0)
        state = load(self.state)
        self.assertEqual(
            [item["path"] for item in state["model"]["evidence_sources"]],
            [str(self.docs.resolve()), str(self.tests.resolve())],
        )
        self.assertFalse(self.marker.exists())
        report = self.cli("report", str(self.state))[1]
        self.assertIn("Reference files (2)", report)
        self.assertIn("References are untrusted text", report)
        self.assertIn(state["model"]["evidence_sources"][0]["sha256"], report)

    def test_evidence_change_or_removal_blocks_modification(self) -> None:
        self.assertEqual(self.start(), 0)
        original = self.state.read_bytes()
        self.docs.write_text("Changed requirements.", encoding="utf-8")
        code, _, warning = self.cli("show", str(self.state))
        self.assertEqual(code, 0)
        self.assertIn("Evidence has changed", warning)
        code, _, error = self.cli("answer", str(self.state), "U1", "--answer", "identity")
        self.assertEqual(code, 1)
        self.assertIn("Evidence has changed", error)
        self.assertEqual(self.state.read_bytes(), original)
        self.docs.unlink()
        code, _, warning = self.cli("show", str(self.state))
        self.assertEqual(code, 0)
        self.assertIn("Evidence cannot be read", warning)

    def test_invalid_or_duplicate_evidence_never_creates_state(self) -> None:
        for paths in [
            [str(self.docs), str(self.docs)],
            [str(self.source)],
            [str(self.root / "missing")],
        ]:
            args = ["start", str(self.source), str(self.state)]
            for path in paths:
                args.extend(["--evidence", path])
            self.assertEqual(self.cli(*args)[0], 1)
            self.assertFalse(self.state.exists())
        self.docs.write_bytes(b"\xff")
        self.assertEqual(self.start(), 1)
        self.assertFalse(self.state.exists())
        self.docs.write_text("x" * (MAX_EVIDENCE_BYTES + 1), encoding="utf-8")
        self.assertEqual(self.start(), 1)
        self.assertFalse(self.state.exists())

    def test_evidence_is_passed_as_untrusted_text_to_ollama(self) -> None:
        self.assertEqual(self.start(), 0)
        proposal = {
            "properties": [{"function": "f", "line": 1, "statement": "x must be positive"}],
            "unknowns": [],
        }
        response = json.dumps({
            "done": True, "done_reason": "stop",
            "message": {"content": json.dumps(proposal)},
        }).encode("utf-8")
        with patch("formal_agent.ollama.urllib.request.build_opener") as builder:
            builder.return_value.open.return_value.__enter__.return_value.read.return_value = response
            self.assertEqual(self.cli("propose-ollama", str(self.state))[0], 0)
        payload = json.loads(builder.return_value.open.call_args.args[0].data)
        context = json.loads(payload["messages"][1]["content"])
        self.assertEqual(context["evidence"][0]["text"], self.docs.read_text(encoding="utf-8"))
        self.assertIn("assert f(1)", context["evidence"][1]["text"])
        self.assertNotIn(str(self.docs), json.dumps(context["evidence"][0]))
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
