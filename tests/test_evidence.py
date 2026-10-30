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

    def test_opt_in_discovery_is_bounded_and_deduplicates_explicit_reference(self) -> None:
        readme = self.root / "README.md"
        readme.write_text("Project requirements", encoding="utf-8")
        adjacent_test = self.root / "test_example.py"
        adjacent_test.write_text("assert f(1) == 1", encoding="utf-8")
        test_directory = self.root / "tests"
        test_directory.mkdir()
        nested_test = test_directory / "test_example.py"
        nested_test.write_text("assert f(2) == 2", encoding="utf-8")
        (test_directory / "test_other.py").write_text("unrelated", encoding="utf-8")
        result, _, error = self.cli(
            "start", str(self.source), str(self.state), "--discover-evidence",
            "--evidence", str(readme),
        )
        self.assertEqual(result, 0, error)
        paths = [item["path"] for item in load(self.state)["model"]["evidence_sources"]]
        self.assertEqual(paths, [str(readme), str(adjacent_test), str(nested_test)])
        self.assertFalse(self.marker.exists())

    def test_discovered_symlink_outside_source_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as external:
            target = Path(external) / "external.md"
            target.write_text("external", encoding="utf-8")
            try:
                (self.root / "README.md").symlink_to(target)
            except OSError as error:
                self.skipTest(f"Symlinks unavailable: {error}")
            result, _, error = self.cli(
                "start", str(self.source), str(self.state), "--discover-evidence"
            )
            self.assertEqual(result, 1)
            self.assertIn("outside the source directory", error)
            self.assertFalse(self.state.exists())

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

    def test_user_can_cite_reference_line_without_automatic_acceptance(self) -> None:
        self.assertEqual(self.start(), 0)
        code, _, error = self.cli(
            "answer", str(self.state), "U2", "--answer", "Only positive values",
            "--reference", str(self.docs), "--line", "1",
        )
        self.assertEqual(code, 0, error)
        state = load(self.state)
        citation = state["unknowns"][1]["answer_reference"]
        self.assertEqual(citation["path"], str(self.docs.resolve()))
        self.assertEqual(citation["line"], 1)
        self.assertEqual(citation["sha256"], state["model"]["evidence_sources"][0]["sha256"])
        self.assertEqual(state["properties"], [])
        report = self.cli("report", str(self.state))[1]
        self.assertIn("User-cited reference:", report)
        self.assertIn("interpretation not verified", report)
        self.assertIn("Verification results: none", report)

    def test_invalid_citation_does_not_save_answer(self) -> None:
        self.assertEqual(self.start(), 0)
        original = self.state.read_bytes()
        for options in [
            ("--reference", str(self.docs)),
            ("--line", "1"),
            ("--reference", str(self.source), "--line", "1"),
            ("--reference", str(self.docs), "--line", "0"),
            ("--reference", str(self.docs), "--line", "2"),
        ]:
            result, _, _ = self.cli(
                "answer", str(self.state), "U2", "--answer", "positive", *options
            )
            self.assertEqual(result, 1)
            self.assertEqual(self.state.read_bytes(), original)

    def test_corrupted_citation_is_rejected(self) -> None:
        self.assertEqual(self.start(), 0)
        self.assertEqual(self.cli(
            "answer", str(self.state), "U2", "--answer", "positive",
            "--reference", str(self.docs), "--line", "1",
        )[0], 0)
        state = load(self.state)
        state["unknowns"][1]["answer_reference"]["sha256"] = "0" * 64
        self.state.write_text(json.dumps(state), encoding="utf-8")
        code, _, error = self.cli("show", str(self.state))
        self.assertEqual(code, 1)
        self.assertIn("Invalid answer reference", error)

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
