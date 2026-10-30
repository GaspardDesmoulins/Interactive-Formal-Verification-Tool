import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from formal_agent.cli import main
from formal_agent.evidence import read_evidence
from formal_agent.storage import load


class ReferenceCallTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.source = root / "example.py"
        self.reference = root / "test_example.py"
        self.marker = root / "executed"
        self.state = root / "state.json"
        self.source.write_text(
            f"open({str(self.marker)!r}, 'w').write('target')\n"
            "def f(x, /, amount=1, *args, limit=1, **kwargs): return amount\n",
            encoding="utf-8",
        )
        self.reference.write_text(
            f"open({str(self.marker)!r}, 'w').write('reference')\n"
            "def test_cases():\n"
            "    f(0, None, limit=0)\n"
            "    module.f(other, amount=0)\n"
            "    other.f(1)\n"
            "    alias(0)\n"
            "    f(*dynamic, 0, limit=fixture, **kwargs)\n"
            "    f(x=0)\n"
            "    f(False)\n"
            "    f(0x0)\n"
            "    f(0.0)\n"
            "    f(-0)\n"
            "    f(+0)\n"
            "    f('0')\n"
            "    text = 'f(0)'\n"
            "    # f(0)\n",
            encoding="utf-8",
        )
        self.assertEqual(self.cli(
            "start", str(self.source), str(self.state), "--evidence", str(self.reference)
        )[0], 0)
        self.assertEqual(self.cli("answer", str(self.state), "U1", "--answer", "Return amount.")[0], 0)
        self.assertEqual(self.cli(
            "add-property", str(self.state), "U1", "--statement", "Return amount."
        )[0], 0)

    def cli(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def analyze(self) -> None:
        code, output, error = self.cli("analyze-test-calls", str(self.state), "P1")
        self.assertEqual(code, 0, error)
        self.assertIn("TA1", output)
        self.assertIn("Call targets, execution and coverage are not established", output)

    def test_ast_calls_distinguish_literals_from_unresolved_values(self) -> None:
        self.analyze()
        analysis = load(self.state)["test_call_analyses"][0]
        self.assertEqual(analysis["method"], "python-ast-direct-v1")
        self.assertEqual(analysis["function"], {"name": "f", "line": 2})
        calls = analysis["calls"]
        self.assertEqual([call["reference"]["line"] for call in calls],
                         [3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14])
        self.assertEqual(calls[0]["reference"]["sha256"],
                         hashlib.sha256(self.reference.read_bytes()).hexdigest())
        self.assertEqual([(a["parameter"], a["value_kind"], a["boundary"])
                          for a in calls[0]["arguments"]],
                         [("x", "literal", "0"), ("amount", "literal", "None"),
                          ("limit", "literal", "0")])
        self.assertEqual((calls[1]["kind"], calls[1]["callee"]), ("attribute", "module.f"))
        self.assertEqual(calls[1]["arguments"][0]["value_kind"], "unresolved")
        self.assertEqual(calls[1]["arguments"][1]["parameter"], "amount")
        self.assertEqual(calls[1]["arguments"][1]["boundary"], "0")
        self.assertEqual((calls[2]["kind"], calls[2]["callee"]), ("attribute", "other.f"))
        self.assertEqual([a["kind"] for a in calls[3]["arguments"]],
                         ["starred", "positional", "keyword", "keyword_unpack"])
        self.assertIsNone(calls[3]["arguments"][1]["parameter"])
        self.assertEqual(calls[3]["arguments"][2]["value_kind"], "unresolved")
        self.assertIsNone(calls[4]["arguments"][0]["parameter"])
        self.assertIsNone(calls[5]["arguments"][0]["boundary"])
        self.assertEqual(calls[6]["arguments"][0]["boundary"], "0")
        self.assertEqual([call["arguments"][0]["boundary"] for call in calls[7:]],
                         [None, "0", "0", None])
        original = self.state.read_bytes()
        code, output, error = self.cli("analyze-test-calls", str(self.state), "P1")
        self.assertEqual(code, 0, error)
        self.assertIn("Already recorded", output)
        self.assertEqual(self.state.read_bytes(), original)
        report = self.cli("report", str(self.state))[1]
        self.assertIn("target resolution, test collection and execution are unknown", report)
        self.assertIn("Parameter names are hypothetical mappings", report)
        self.assertIn("Variables, fixtures, indirect calls, aliases", report)
        self.assertIn('"fixture"; unresolved', report)
        self.assertFalse(self.marker.exists())

    def test_suggestion_match_is_not_claimed_for_revised_free_text(self) -> None:
        self.assertEqual(self.cli("suggest-scenarios", str(self.state), "P1")[0], 0)
        self.assertEqual(self.cli(
            "accept-suggestion", str(self.state), "T3", "--expected", "Return.",
            "--description", "A different situation selected by the user.",
            "--reason", "Review needed.",
        )[0], 0)
        self.analyze()
        report = self.cli("report", str(self.state))[1]
        self.assertIn("Static analyses for the same property: TA1; free-text scenario not "
                      "automatically matched", report)
        self.assertIn("Possible syntactic match: TA1", report)
        self.assertIn('argument "0"; target and coverage not verified', report)
        self.assertIn("No verification engine or tests of the target were run", report)
        self.assertFalse(self.marker.exists())

    def test_no_python_reference_and_invalid_python_fail_without_writing(self) -> None:
        state = self.state.parent / "without-python.json"
        text = self.state.parent / "reference.txt"
        text.write_text("f(0)\n", encoding="utf-8")
        self.assertEqual(self.cli(
            "start", str(self.source), str(state), "--evidence", str(text)
        )[0], 0)
        self.assertEqual(self.cli("answer", str(state), "U1", "--answer", "Return.")[0], 0)
        self.assertEqual(self.cli("add-property", str(state), "U1", "--statement", "Return.")[0], 0)
        original = state.read_bytes()
        code, _, error = self.cli("analyze-test-calls", str(state), "P1")
        self.assertEqual(code, 1)
        self.assertIn("No Python references", error)
        self.assertEqual(state.read_bytes(), original)

        invalid = self.state.parent / "invalid.py"
        invalid.write_text("def broken(:\n", encoding="utf-8")
        invalid_state = self.state.parent / "invalid.json"
        self.assertEqual(self.cli(
            "start", str(self.source), str(invalid_state), "--evidence", str(invalid)
        )[0], 0)
        self.assertEqual(self.cli("answer", str(invalid_state), "U1", "--answer", "Return.")[0], 0)
        self.assertEqual(self.cli(
            "add-property", str(invalid_state), "U1", "--statement", "Return."
        )[0], 0)
        original = invalid_state.read_bytes()
        code, _, error = self.cli("analyze-test-calls", str(invalid_state), "P1")
        self.assertEqual(code, 1)
        self.assertIn("Python reference cannot be parsed", error)
        self.assertEqual(invalid_state.read_bytes(), original)

    def test_changed_evidence_during_analysis_blocks_recording(self) -> None:
        original = self.state.read_bytes()

        def mutate_after_read(path: Path) -> tuple[str, str]:
            content = read_evidence(path)
            path.write_text("# changed during analysis\n", encoding="utf-8")
            return content

        with patch("formal_agent.reference_calls.read_evidence", side_effect=mutate_after_read):
            code, _, error = self.cli("analyze-test-calls", str(self.state), "P1")
        self.assertEqual(code, 1)
        self.assertIn("Evidence has changed", error)
        self.assertEqual(self.state.read_bytes(), original)
        self.assertFalse(self.marker.exists())

    def test_analysis_limits_fail_atomically(self) -> None:
        for label, content, expected in [
            ("calls", "f(0)\n" * 201, "exceeds 200 same-name calls"),
            ("arguments", "f(" + ",".join("0" for _ in range(65)) + ")\n",
             "exceeds 64 arguments"),
            ("expression", "f('" + ("x" * 511) + "')\n", "exceeds 512 characters"),
        ]:
            with self.subTest(label=label):
                reference = self.state.parent / f"{label}.py"
                state = self.state.parent / f"{label}.json"
                reference.write_text(content, encoding="utf-8")
                self.assertEqual(self.cli(
                    "start", str(self.source), str(state), "--evidence", str(reference)
                )[0], 0)
                self.assertEqual(self.cli("answer", str(state), "U1", "--answer", "Return.")[0], 0)
                self.assertEqual(self.cli(
                    "add-property", str(state), "U1", "--statement", "Return."
                )[0], 0)
                original = state.read_bytes()
                code, _, error = self.cli("analyze-test-calls", str(state), "P1")
                self.assertEqual(code, 1)
                self.assertIn(expected, error)
                self.assertEqual(state.read_bytes(), original)

    def test_analysis_accepts_exact_limits(self) -> None:
        for label, content, count in [
            ("calls-at-limit", "f(0)\n" * 200, 200),
            ("arguments-at-limit", "f(" + ",".join("0" for _ in range(64)) + ")\n", 64),
            ("expression-at-limit", "f('" + ("x" * 510) + "')\n", 512),
        ]:
            with self.subTest(label=label):
                reference = self.state.parent / f"{label}.py"
                state = self.state.parent / f"{label}.json"
                reference.write_text(content, encoding="utf-8")
                self.assertEqual(self.cli(
                    "start", str(self.source), str(state), "--evidence", str(reference)
                )[0], 0)
                self.assertEqual(self.cli("answer", str(state), "U1", "--answer", "Return.")[0], 0)
                self.assertEqual(self.cli(
                    "add-property", str(state), "U1", "--statement", "Return."
                )[0], 0)
                result, _, error = self.cli("analyze-test-calls", str(state), "P1")
                self.assertEqual(result, 0, error)
                calls = load(state)["test_call_analyses"][0]["calls"]
                actual = (
                    len(calls) if label == "calls-at-limit" else
                    len(calls[0]["arguments"]) if label == "arguments-at-limit" else
                    len(calls[0]["arguments"][0]["expression"])
                )
                self.assertEqual(actual, count)

    def test_changed_source_blocks_analysis_without_saving(self) -> None:
        original = self.state.read_bytes()
        self.source.write_text("def f(x): return x\n", encoding="utf-8")
        code, _, error = self.cli("analyze-test-calls", str(self.state), "P1")
        self.assertEqual(code, 1)
        self.assertIn("Source has changed", error)
        self.assertEqual(self.state.read_bytes(), original)

    def test_no_matching_calls_is_not_a_coverage_claim(self) -> None:
        reference = self.state.parent / "other.py"
        state = self.state.parent / "other.json"
        reference.write_text("alias(0)\ntext = 'f(0)'\n# f(0)\n", encoding="utf-8")
        self.assertEqual(self.cli(
            "start", str(self.source), str(state), "--evidence", str(reference)
        )[0], 0)
        self.assertEqual(self.cli("answer", str(state), "U1", "--answer", "Return.")[0], 0)
        self.assertEqual(self.cli("add-property", str(state), "U1", "--statement", "Return.")[0], 0)
        self.assertEqual(self.cli("suggest-scenarios", str(state), "P1")[0], 0)
        self.assertEqual(self.cli("analyze-test-calls", str(state), "P1")[0], 0)
        report = self.cli("report", str(state))[1]
        self.assertIn("No same-name calls found; this is not evidence of a coverage gap", report)
        self.assertIn("No syntactic argument match found; this is not evidence of a coverage gap", report)
        self.assertEqual(load(state)["test_call_analyses"][0]["calls"], [])

    def test_finished_investigation_requires_resume(self) -> None:
        self.assertEqual(self.cli("finish", str(self.state), "--allow-open")[0], 0)
        original = self.state.read_bytes()
        code, _, error = self.cli("analyze-test-calls", str(self.state), "P1")
        self.assertEqual(code, 1)
        self.assertIn("not active", error)
        self.assertEqual(self.state.read_bytes(), original)
        self.assertEqual(self.cli("resume", str(self.state))[0], 0)
        self.analyze()

    def test_schema_nine_migrates_existing_suggestions_without_write(self) -> None:
        self.assertEqual(self.cli("suggest-scenarios", str(self.state), "P1")[0], 0)
        legacy = load(self.state)
        legacy["schema_version"] = 9
        del legacy["test_call_analyses"]
        self.state.write_text(json.dumps(legacy), encoding="utf-8")
        original = self.state.read_bytes()
        migrated = load(self.state)
        self.assertEqual(migrated["schema_version"], 10)
        self.assertEqual(migrated["scenario_suggestions"], legacy["scenario_suggestions"])
        self.assertEqual(migrated["test_call_analyses"], [])
        self.assertEqual(self.state.read_bytes(), original)
        self.analyze()
        written = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(written["schema_version"], 10)
        self.assertEqual(written["scenario_suggestions"], legacy["scenario_suggestions"])

    def test_malformed_analysis_is_rejected(self) -> None:
        self.analyze()
        valid = load(self.state)
        for label, mutate in [
            ("missing field", lambda s: s.pop("test_call_analyses")),
            ("bad id", lambda s: s["test_call_analyses"][0].update(id="TA01")),
            ("bad property", lambda s: s["test_call_analyses"][0].update(property_id="P99")),
            ("bad method", lambda s: s["test_call_analyses"][0].update(method="unknown")),
            ("bad scope", lambda s: s["test_call_analyses"][0].update(evidence_scope=[])),
            ("bad function", lambda s: s["test_call_analyses"][0]["function"].update(line=True)),
            ("bad digest", lambda s: s["test_call_analyses"][0]["calls"][0]["reference"].update(sha256="0" * 64)),
            ("bad line", lambda s: s["test_call_analyses"][0]["calls"][0]["reference"].update(line=True)),
            ("bad argument", lambda s: s["test_call_analyses"][0]["calls"][0]["arguments"][0].update(parameter="missing")),
            ("bad position", lambda s: s["test_call_analyses"][0]["calls"][0]["arguments"][0].update(position=True)),
            ("bad boundary", lambda s: s["test_call_analyses"][0]["calls"][0]["arguments"][0].update(value_kind="unresolved")),
            ("legacy extra data", lambda s: s.update(schema_version=9)),
        ]:
            with self.subTest(label=label):
                damaged = copy.deepcopy(valid)
                mutate(damaged)
                self.state.write_text(json.dumps(damaged), encoding="utf-8")
                original = self.state.read_bytes()
                code, _, error = self.cli("show", str(self.state))
                self.assertEqual(code, 1)
                self.assertIn("Error:", error)
                self.assertEqual(self.state.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
