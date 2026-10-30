import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from formal_agent.cli import main
from formal_agent.storage import load


class ScenarioSuggestionTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.source = root / "example.py"
        self.evidence = root / "test_example.py"
        self.marker = root / "executed"
        self.state = root / "state.json"
        self.source.write_text(
            f"open({str(self.marker)!r}, 'w').write('source')\n"
            "def f(amount, /, x=1, *args, flag=False, **kwargs): return amount\n"
            "def g(other): return other\n",
            encoding="utf-8",
        )
        self.evidence.write_text(
            f"open({str(self.marker)!r}, 'w').write('test')\n"
            "assert amount == 0\n"
            "assert amount == 10\n"
            "assert amount == 0.0\n"
            "x = None\n"
            "# amount 0 may refer to an output, not an input\n",
            encoding="utf-8",
        )
        self.assertEqual(self.cli(
            "start", str(self.source), str(self.state), "--evidence", str(self.evidence)
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

    def suggest(self) -> None:
        code, output, error = self.cli("suggest-scenarios", str(self.state), "P1")
        self.assertEqual(code, 0, error)
        self.assertIn("6 scenario suggestions", output)

    def test_suggestions_record_only_bounded_literal_matches(self) -> None:
        self.suggest()
        state = load(self.state)
        self.assertEqual(state["scenarios"], [])
        suggestions = state["scenario_suggestions"]
        self.assertEqual([(item["parameter"], item["value"]) for item in suggestions], [
            ("amount", "0"), ("amount", "None"), ("x", "0"), ("x", "None"),
            ("flag", "0"), ("flag", "None"),
        ])
        self.assertEqual(suggestions[0]["function"], {"name": "f", "line": 2})
        self.assertEqual(suggestions[0]["property_id"], "P1")
        self.assertEqual(suggestions[0]["evidence_scope"], state["model"]["evidence_sources"])
        self.assertEqual([item["line"] for item in suggestions[0]["matches"]], [2, 6])
        self.assertEqual(suggestions[0]["matches"][0]["sha256"],
                         hashlib.sha256(self.evidence.read_bytes()).hexdigest())
        self.assertEqual(suggestions[1]["matches"], [])
        self.assertEqual([item["line"] for item in suggestions[3]["matches"]], [5])
        self.assertFalse(suggestions[0]["matches_truncated"])
        report = self.cli("report", str(self.state))[1]
        self.assertIn("T1 [pending review]", report)
        self.assertIn("Expected outcome, applicability and coverage: not established", report)
        self.assertIn("same-line literal text match only", report)
        self.assertIn("relevance and coverage not verified", report)
        self.assertIn("No same-line text match found; this is not evidence of a coverage gap", report)
        original = self.state.read_bytes()
        code, output, error = self.cli("suggest-scenarios", str(self.state), "P1")
        self.assertEqual(code, 0, error)
        self.assertIn("0 scenario suggestions; 6 already recorded", output)
        self.assertEqual(self.state.read_bytes(), original)
        self.assertFalse(self.marker.exists())

    def test_suggestions_work_for_an_llm_property_on_another_function(self) -> None:
        proposals = self.state.parent / "proposals.json"
        proposals.write_text(json.dumps({
            "source_sha256": load(self.state)["model"]["sha256"],
            "properties": [{"function": "g", "line": 3, "statement": "Return other."}],
            "unknowns": [],
        }), encoding="utf-8")
        self.assertEqual(self.cli(
            "import-proposals", str(self.state), str(proposals), "--reasoner", "fixture"
        )[0], 0)
        code, output, error = self.cli("suggest-scenarios", str(self.state), "P2")
        self.assertEqual(code, 0, error)
        self.assertIn("2 scenario suggestions", output)
        suggestions = load(self.state)["scenario_suggestions"]
        self.assertEqual([(item["property_id"], item["parameter"]) for item in suggestions],
                         [("P2", "other"), ("P2", "other")])
        self.assertTrue(all(item["function"] == {"name": "g", "line": 3}
                            for item in suggestions))

    def test_no_references_is_not_misreported_as_a_search_result(self) -> None:
        state = self.state.parent / "without-references.json"
        self.assertEqual(self.cli("start", str(self.source), str(state))[0], 0)
        self.assertEqual(self.cli("answer", str(state), "U1", "--answer", "Return amount.")[0], 0)
        self.assertEqual(self.cli(
            "add-property", str(state), "U1", "--statement", "Return amount."
        )[0], 0)
        self.assertEqual(self.cli("suggest-scenarios", str(state), "P1")[0], 0)
        report = self.cli("report", str(state))[1]
        self.assertIn("No references were declared; no reference search was possible.", report)
        self.assertNotIn("No same-line text match found", report)

    def test_match_limit_is_explicit_and_all_reference_files_are_scoped(self) -> None:
        first = self.state.parent / "many.txt"
        second = self.state.parent / "other.txt"
        state = self.state.parent / "many-state.json"
        first.write_text("amount 0\n" * 7, encoding="utf-8")
        second.write_text("amount 0\n", encoding="utf-8")
        self.assertEqual(self.cli(
            "start", str(self.source), str(state), "--evidence", str(first),
            "--evidence", str(second),
        )[0], 0)
        self.assertEqual(self.cli("answer", str(state), "U1", "--answer", "Return amount.")[0], 0)
        self.assertEqual(self.cli(
            "add-property", str(state), "U1", "--statement", "Return amount."
        )[0], 0)
        self.assertEqual(self.cli("suggest-scenarios", str(state), "P1")[0], 0)
        suggestion = load(state)["scenario_suggestions"][0]
        self.assertEqual(len(suggestion["evidence_scope"]), 2)
        self.assertEqual([item["line"] for item in suggestion["matches"]], [1, 2, 3, 4, 5])
        self.assertTrue(suggestion["matches_truncated"])
        report = self.cli("report", str(state))[1]
        self.assertIn("Declared references searched: 2", report)
        self.assertIn("Additional text matches omitted", report)

    def test_unsupported_parameters_and_suggestion_limit_are_reported(self) -> None:
        for label, signature, expected in [
            ("variadic", "*args, **kwargs", "no supported named parameters"),
            ("large", ", ".join(f"x{i}" for i in range(33)), "More than 64 suggestions"),
        ]:
            with self.subTest(label=label):
                source = self.state.parent / f"{label}.py"
                state = self.state.parent / f"{label}.json"
                source.write_text(f"def f({signature}): return None\n", encoding="utf-8")
                self.assertEqual(self.cli("start", str(source), str(state))[0], 0)
                self.assertEqual(self.cli("answer", str(state), "U1", "--answer", "Return.")[0], 0)
                self.assertEqual(self.cli(
                    "add-property", str(state), "U1", "--statement", "Returns."
                )[0], 0)
                original = state.read_bytes()
                code, _, error = self.cli("suggest-scenarios", str(state), "P1")
                self.assertEqual(code, 1)
                self.assertIn(expected, error)
                self.assertEqual(state.read_bytes(), original)

    def test_review_is_explicit_and_preserves_revised_decisions(self) -> None:
        self.suggest()
        self.assertEqual(self.cli(
            "reject-suggestion", str(self.state), "T1", "--reason", "Unclear whether relevant."
        )[0], 0)
        self.assertEqual(load(self.state)["scenarios"], [])
        self.assertEqual(self.cli(
            "accept-suggestion", str(self.state), "T1", "--reason", "Reconsidered.",
            "--description", "A zero amount with a valid balance", "--expected", "Return zero.",
        )[0], 0)
        state = load(self.state)
        self.assertEqual(state["scenarios"][0]["description"], "A zero amount with a valid balance")
        self.assertEqual(state["scenarios"][0]["expected"], "Return zero.")
        self.assertEqual(state["scenarios"][0]["reviews"], [])
        self.assertEqual(state["scenario_suggestions"][0]["decisions"], [
            {"decision": "rejected", "origin": "user", "reason": "Unclear whether relevant.",
             "scenario_id": None},
            {"decision": "accepted", "origin": "user", "reason": "Reconsidered.",
             "scenario_id": "S1"},
        ])
        report = self.cli("report", str(self.state))[1]
        self.assertIn("Accepted from suggestion: T1; relevance not verified", report)
        self.assertIn("T1 [accepted]", report)
        self.assertIn("Decision 1: rejected", report)
        self.assertIn("Decision 2: accepted", report)
        original = self.state.read_bytes()
        for args in [
            ("accept-suggestion", str(self.state), "T1", "--reason", "again", "--expected", "x"),
            ("reject-suggestion", str(self.state), "T1", "--reason", "again"),
        ]:
            self.assertEqual(self.cli(*args)[0], 1)
            self.assertEqual(self.state.read_bytes(), original)
        self.assertFalse(self.marker.exists())

    def test_invalid_requests_preserve_state_and_changed_evidence_blocks_updates(self) -> None:
        original = self.state.read_bytes()
        self.assertEqual(self.cli("suggest-scenarios", str(self.state), "P99")[0], 1)
        self.assertEqual(self.state.read_bytes(), original)
        self.suggest()
        original = self.state.read_bytes()
        for args in [
            ("accept-suggestion", str(self.state), "T99", "--expected", "x", "--reason", "why"),
            ("accept-suggestion", str(self.state), "T1", "--expected", " ", "--reason", "why"),
            ("accept-suggestion", str(self.state), "T1", "--expected", "x", "--reason", " "),
            ("accept-suggestion", str(self.state), "T1", "--expected", "x", "--reason", "why",
             "--description", " "),
            ("reject-suggestion", str(self.state), "T1", "--reason", " "),
        ]:
            with self.subTest(args=args):
                self.assertEqual(self.cli(*args)[0], 1)
                self.assertEqual(self.state.read_bytes(), original)
        self.evidence.write_text("# changed\n", encoding="utf-8")
        for args in [
            ("suggest-scenarios", str(self.state), "P1"),
            ("accept-suggestion", str(self.state), "T1", "--expected", "x", "--reason", "why"),
            ("reject-suggestion", str(self.state), "T1", "--reason", "why"),
        ]:
            result, _, error = self.cli(*args)
            self.assertEqual(result, 1)
            self.assertIn("Evidence has changed", error)
            self.assertEqual(self.state.read_bytes(), original)
        self.assertIn("Evidence has changed", self.cli("report", str(self.state))[1])

    def test_suggestions_require_active_phase_and_remain_visible_after_resume(self) -> None:
        self.suggest()
        self.assertEqual(self.cli("ask", str(self.state), "U2")[0], 0)
        original = self.state.read_bytes()
        code, _, error = self.cli(
            "accept-suggestion", str(self.state), "T1", "--expected", "Return zero.",
            "--reason", "Relevant boundary.",
        )
        self.assertEqual(code, 1)
        self.assertIn("not active", error)
        self.assertEqual(self.state.read_bytes(), original)
        self.assertEqual(self.cli("resume", str(self.state))[0], 0)
        code, output, error = self.cli("finish", str(self.state), "--allow-open")
        self.assertEqual(code, 0, error)
        self.assertIn("6 scenario suggestions await review", output)
        original = self.state.read_bytes()
        self.assertEqual(self.cli(
            "reject-suggestion", str(self.state), "T1", "--reason", "Not applicable."
        )[0], 1)
        self.assertEqual(self.state.read_bytes(), original)
        self.assertEqual(self.cli("resume", str(self.state))[0], 0)
        self.assertEqual(self.cli(
            "accept-suggestion", str(self.state), "T1", "--expected", "Return zero.",
            "--reason", "Relevant boundary.",
        )[0], 0)
        scenario = load(self.state)["scenarios"][0]
        self.assertEqual(
            scenario["description"],
            "Parameter amount of f is 0; other inputs unspecified.",
        )
        self.assertEqual(scenario["expected"], "Return zero.")

    def test_schema_eight_migration_preserves_scenarios_until_write(self) -> None:
        self.assertEqual(self.cli(
            "add-scenario", str(self.state), "P1", "--description", "Zero",
            "--expected", "Return zero.",
        )[0], 0)
        self.assertEqual(self.cli(
            "review-scenario", str(self.state), "S1", "--assessment", "potential_gap",
            "--reason", "Review coverage.",
        )[0], 0)
        old = load(self.state)
        old["schema_version"] = 8
        del old["scenario_suggestions"]
        del old["test_call_analyses"]
        self.state.write_text(json.dumps(old), encoding="utf-8")
        original = self.state.read_bytes()
        migrated = load(self.state)
        self.assertEqual(migrated["schema_version"], 10)
        self.assertEqual(migrated["scenarios"], old["scenarios"])
        self.assertEqual(migrated["scenario_suggestions"], [])
        self.assertEqual(self.state.read_bytes(), original)
        self.suggest()
        written = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(written["schema_version"], 10)
        self.assertEqual(written["scenarios"], old["scenarios"])

    def test_corrupted_suggestions_decisions_and_citations_are_rejected(self) -> None:
        self.suggest()
        self.assertEqual(self.cli(
            "accept-suggestion", str(self.state), "T1", "--expected", "x", "--reason", "why"
        )[0], 0)
        valid = load(self.state)
        for label, mutate in [
            ("missing suggestions", lambda s: s.pop("scenario_suggestions")),
            ("bad id", lambda s: s["scenario_suggestions"][0].update(id="T01")),
            ("bad property", lambda s: s["scenario_suggestions"][0].update(property_id="P99")),
            ("bad function", lambda s: s["scenario_suggestions"][0]["function"].update(line=True)),
            ("bad parameter", lambda s: s["scenario_suggestions"][0].update(parameter="args")),
            ("bad value", lambda s: s["scenario_suggestions"][0].update(value="1")),
            ("duplicate suggestion", lambda s: s["scenario_suggestions"].append(
                copy.deepcopy(s["scenario_suggestions"][0]))),
            ("wrong scope", lambda s: s["scenario_suggestions"][0].update(evidence_scope=[])),
            ("wrong digest", lambda s: s["scenario_suggestions"][0]["matches"][0].update(sha256="0" * 64)),
            ("wrong line", lambda s: s["scenario_suggestions"][0]["matches"][0].update(line=True)),
            ("false truncation", lambda s: s["scenario_suggestions"][0].update(matches_truncated=True)),
            ("wrong decision", lambda s: s["scenario_suggestions"][0]["decisions"][0].update(decision="covered")),
            ("blank reason", lambda s: s["scenario_suggestions"][0]["decisions"][0].update(reason=" ")),
            ("missing scenario", lambda s: s["scenario_suggestions"][0]["decisions"][0].update(scenario_id="S99")),
            ("legacy extra data", lambda s: s.update(schema_version=8)),
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
