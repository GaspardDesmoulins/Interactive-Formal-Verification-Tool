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


class ScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.source = root / "example.py"
        self.evidence = root / "test_example.py"
        self.state = root / "state.json"
        self.marker = root / "executed"
        self.source.write_text(
            f"open({str(self.marker)!r}, 'w').write('source')\n"
            "def f(x): return x\n",
            encoding="utf-8",
        )
        self.evidence.write_text(
            f"open({str(self.marker)!r}, 'w').write('test')\n"
            "# A test of zero may be relevant.\n",
            encoding="utf-8",
        )
        self.assertEqual(self.cli(
            "start", str(self.source), str(self.state), "--evidence", str(self.evidence)
        )[0], 0)
        self.assertEqual(self.cli("answer", str(self.state), "U1", "--answer", "Return x.")[0], 0)
        self.assertEqual(self.cli(
            "add-property", str(self.state), "U1", "--statement", "Return x."
        )[0], 0)

    def cli(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def add_scenario(self) -> None:
        result, output, error = self.cli(
            "add-scenario", str(self.state), "P1",
            "--description", "x is zero", "--expected", "Return zero.",
        )
        self.assertEqual(result, 0, error)
        self.assertIn("coverage not assessed", output)

    def test_scenario_reviews_are_ordered_and_unverified(self) -> None:
        self.add_scenario()
        scenario = load(self.state)["scenarios"][0]
        self.assertEqual(scenario, {
            "id": "S1", "property_id": "P1", "description": "x is zero",
            "expected": "Return zero.", "origin": "user", "reviews": [],
        })
        self.assertIn('S1 [not assessed] "x is zero"', self.cli("report", str(self.state))[1])
        self.assertEqual(self.cli(
            "review-scenario", str(self.state), "S1", "--assessment", "potential_gap",
            "--reason", "No obvious case was found.",
        )[0], 0)
        self.assertEqual(self.cli(
            "review-scenario", str(self.state), "S1", "--assessment", "reference_found",
            "--reason", "This line may be relevant.", "--reference", str(self.evidence),
            "--line", "2",
        )[0], 0)
        self.assertEqual(self.cli(
            "review-scenario", str(self.state), "S1", "--assessment", "inconclusive",
            "--reason", "Further review is needed.", "--reference", str(self.evidence),
            "--line", "1",
        )[0], 0)
        scenario = load(self.state)["scenarios"][0]
        self.assertEqual([item["assessment"] for item in scenario["reviews"]],
                         ["potential_gap", "reference_found", "inconclusive"])
        self.assertIsNone(scenario["reviews"][0]["reference"])
        self.assertEqual(scenario["reviews"][1]["reference"], {
            "path": str(self.evidence.resolve()),
            "sha256": hashlib.sha256(self.evidence.read_bytes()).hexdigest(),
            "line": 2,
        })
        report = self.cli("report", str(self.state))[1]
        self.assertIn("S1 [user assessment: inconclusive]", report)
        self.assertIn('Review 1: potential_gap; user reason: "No obvious case was found."; '
                      "not verified", report)
        self.assertIn("Review 2: reference_found", report)
        self.assertIn("Review 3: inconclusive", report)
        self.assertIn("relevance not verified", report)
        self.assertIn("Scenario assessments and citations do not establish coverage or gaps.", report)
        self.assertIn("No verification engine or tests of the target were run.", report)
        self.assertFalse(self.marker.exists())

    def test_invalid_requests_do_not_modify_the_snapshot(self) -> None:
        original = self.state.read_bytes()
        for args in [
            ("add-scenario", str(self.state), "P99", "--description", "case", "--expected", "x"),
            ("add-scenario", str(self.state), "P1", "--description", " ", "--expected", "x"),
            ("add-scenario", str(self.state), "P1", "--description", "case", "--expected", " "),
            ("review-scenario", str(self.state), "S1", "--assessment", "potential_gap",
             "--reason", "why"),
        ]:
            with self.subTest(args=args):
                result, _, error = self.cli(*args)
                self.assertEqual(result, 1, error)
                self.assertEqual(self.state.read_bytes(), original)
        self.add_scenario()
        original = self.state.read_bytes()
        for args in [
            ("--assessment", "potential_gap", "--reason", " "),
            ("--assessment", "reference_found", "--reason", "why"),
            ("--assessment", "potential_gap", "--reason", "why", "--reference", str(self.evidence)),
            ("--assessment", "potential_gap", "--reason", "why", "--line", "1"),
            ("--assessment", "reference_found", "--reason", "why", "--reference", str(self.source),
             "--line", "1"),
            ("--assessment", "reference_found", "--reason", "why", "--reference", str(self.evidence),
             "--line", "0"),
            ("--assessment", "reference_found", "--reason", "why", "--reference", str(self.evidence),
             "--line", "3"),
        ]:
            with self.subTest(args=args):
                result, _, error = self.cli("review-scenario", str(self.state), "S1", *args)
                self.assertEqual(result, 1, error)
                self.assertEqual(self.state.read_bytes(), original)
        with self.assertRaises(SystemExit):
            self.cli("review-scenario", str(self.state), "S1", "--assessment", "covered",
                     "--reason", "why")
        self.assertEqual(self.state.read_bytes(), original)

    def test_scenarios_link_independently_to_candidate_and_accepted_properties(self) -> None:
        self.assertEqual(self.cli("accept-property", str(self.state), "P1")[0], 0)
        self.assertEqual(self.cli(
            "add-property", str(self.state), "U1", "--statement", "Return an integer."
        )[0], 0)
        self.add_scenario()
        self.assertEqual(self.cli(
            "add-scenario", str(self.state), "P2",
            "--description", "x is negative", "--expected", "Return a negative integer.",
        )[0], 0)
        self.assertEqual(self.cli(
            "review-scenario", str(self.state), "S2", "--assessment", "potential_gap",
            "--reason", "Negative inputs need review.",
        )[0], 0)
        first, second = load(self.state)["scenarios"]
        self.assertEqual((first["id"], first["property_id"], first["reviews"]), ("S1", "P1", []))
        self.assertEqual((second["id"], second["property_id"]), ("S2", "P2"))
        self.assertEqual(second["reviews"][0]["assessment"], "potential_gap")

    def test_old_schema_migrates_without_losing_existing_information(self) -> None:
        self.assertEqual(self.cli(
            "record-conflict", str(self.state), "U1", "--description", "Review behavior."
        )[0], 0)
        legacy = load(self.state)
        legacy["schema_version"] = 7
        del legacy["scenarios"]
        del legacy["scenario_suggestions"]
        self.state.write_text(json.dumps(legacy), encoding="utf-8")
        original = self.state.read_bytes()
        migrated = load(self.state)
        self.assertEqual(migrated["schema_version"], 9)
        self.assertEqual(migrated["scenarios"], [])
        self.assertEqual(migrated["properties"], legacy["properties"])
        self.assertEqual(migrated["unknowns"], legacy["unknowns"])
        self.assertEqual(migrated["conflicts"], legacy["conflicts"])
        self.assertEqual(self.state.read_bytes(), original)
        self.add_scenario()
        written = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(written["schema_version"], 9)
        self.assertEqual(written["properties"], legacy["properties"])
        self.assertEqual(written["conflicts"], legacy["conflicts"])

    def test_corrupted_scenarios_and_references_are_rejected(self) -> None:
        self.add_scenario()
        self.assertEqual(self.cli(
            "review-scenario", str(self.state), "S1", "--assessment", "reference_found",
            "--reason", "Possibly relevant.", "--reference", str(self.evidence), "--line", "2",
        )[0], 0)
        valid = load(self.state)
        for label, mutate in [
            ("missing scenarios", lambda s: s.pop("scenarios")),
            ("wrong property", lambda s: s["scenarios"][0].update(property_id="P99")),
            ("bad ID", lambda s: s["scenarios"][0].update(id="S01")),
            ("duplicate ID", lambda s: s["scenarios"].append(copy.deepcopy(s["scenarios"][0]))),
            ("missing expected", lambda s: s["scenarios"][0].pop("expected")),
            ("blank description", lambda s: s["scenarios"][0].update(description=" ")),
            ("wrong origin", lambda s: s["scenarios"][0].update(origin="llm")),
            ("invalid reviews", lambda s: s["scenarios"][0].update(reviews={})),
            ("bad assessment", lambda s: s["scenarios"][0]["reviews"][0].update(assessment="covered")),
            ("blank reason", lambda s: s["scenarios"][0]["reviews"][0].update(reason=" ")),
            ("no reference", lambda s: s["scenarios"][0]["reviews"][0].update(reference=None)),
            ("bad digest", lambda s: s["scenarios"][0]["reviews"][0]["reference"].update(sha256="0" * 64)),
            ("unknown path", lambda s: s["scenarios"][0]["reviews"][0]["reference"].update(path="other")),
            ("boolean line", lambda s: s["scenarios"][0]["reviews"][0]["reference"].update(line=True)),
            ("legacy extra data", lambda s: s.update(schema_version=7)),
        ]:
            with self.subTest(label=label):
                damaged = copy.deepcopy(valid)
                mutate(damaged)
                self.state.write_text(json.dumps(damaged), encoding="utf-8")
                original = self.state.read_bytes()
                result, _, error = self.cli("show", str(self.state))
                self.assertEqual(result, 1)
                self.assertIn("Error:", error)
                self.assertEqual(self.state.read_bytes(), original)

    def test_phase_and_changed_reference_block_updates(self) -> None:
        self.add_scenario()
        self.assertEqual(self.cli("finish", str(self.state), "--allow-open")[0], 0)
        original = self.state.read_bytes()
        result, _, error = self.cli(
            "review-scenario", str(self.state), "S1", "--assessment", "potential_gap",
            "--reason", "Possibly missing.",
        )
        self.assertEqual(result, 1)
        self.assertIn("not active", error)
        self.assertEqual(self.state.read_bytes(), original)
        self.assertEqual(self.cli("resume", str(self.state))[0], 0)
        original = self.state.read_bytes()
        self.evidence.write_text("# changed\n", encoding="utf-8")
        result, _, error = self.cli(
            "review-scenario", str(self.state), "S1", "--assessment", "potential_gap",
            "--reason", "Possibly missing.",
        )
        self.assertEqual(result, 1)
        self.assertIn("Evidence has changed", error)
        self.assertEqual(self.state.read_bytes(), original)
        report = self.cli("report", str(self.state))[1]
        self.assertIn("Evidence has changed", report)
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
