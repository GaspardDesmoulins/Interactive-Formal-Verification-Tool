import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from formal_agent.cli import main
from formal_agent.storage import load


class AssumptionTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "sample.py"
        self.state = self.root / "state.json"
        self.source.write_text("def f(x): return x\n\ndef g(y): return y\n", encoding="utf-8")
        self.assertEqual(self.cli("start", str(self.source), str(self.state))[0], 0)
        self.assertEqual(self.cli("answer", str(self.state), "U2", "--answer", "positive")[0], 0)
        self.assertEqual(
            self.cli("add-property", str(self.state), "U2", "--statement", "f returns x")[0], 0
        )

    def cli(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_assumption_and_links_persist_with_report_provenance(self) -> None:
        self.assertEqual(
            self.cli("add-assumption", str(self.state), "U2", "P1", "--statement", "x > 0")[0], 0
        )
        self.assertEqual(self.cli("link-property", str(self.state), "U1", "P1")[0], 0)
        state = load(self.state)
        self.assertEqual(state["assumptions"][0], {
            "id": "A1", "statement": "x > 0", "status": "assumed", "origin": "user",
            "source_unknown_id": "U2", "property_id": "P1",
        })
        self.assertIn({"unknown_id": "U1", "property_id": "P1"}, state["links"])
        report = self.cli("report", str(self.state))[1]
        self.assertIn('A1 [assumed] "x > 0"', report)
        self.assertIn('source answer: "U2"; property: P1; verification: not performed', report)
        self.assertIn("Related properties: P1", report)

    def test_invalid_links_and_assumptions_do_not_modify_state(self) -> None:
        original = self.state.read_bytes()
        for args in [
            ("link-property", str(self.state), "U2", "P1"),
            ("link-property", str(self.state), "U3", "P1"),
            ("link-property", str(self.state), "U99", "P1"),
            ("add-assumption", str(self.state), "U1", "P1", "--statement", "x > 0"),
            ("add-assumption", str(self.state), "U2", "P99", "--statement", "x > 0"),
            ("add-assumption", str(self.state), "U2", "P1", "--statement", " "),
        ]:
            self.assertEqual(self.cli(*args)[0], 1, args)
            self.assertEqual(self.state.read_bytes(), original)

    def test_corrupted_links_and_assumptions_are_rejected(self) -> None:
        self.assertEqual(
            self.cli("add-assumption", str(self.state), "U2", "P1", "--statement", "x > 0")[0], 0
        )
        state = load(self.state)
        for change in (
            lambda data: data["links"].append({"unknown_id": "U99", "property_id": "P1"}),
            lambda data: data["links"].clear(),
            lambda data: data["assumptions"][0].update({"source_unknown_id": "U1"}),
            lambda data: data["assumptions"][0].update({"status": "proved"}),
        ):
            data = json.loads(json.dumps(state))
            change(data)
            self.state.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(self.cli("show", str(self.state))[0], 1)

    def test_version_three_migrates_user_property_provenance(self) -> None:
        state = load(self.state)
        state["schema_version"] = 3
        del state["links"]
        del state["assumptions"]
        self.state.write_text(json.dumps(state), encoding="utf-8")
        original = self.state.read_bytes()
        migrated = load(self.state)
        self.assertEqual(migrated["links"], [{"unknown_id": "U2", "property_id": "P1"}])
        self.assertEqual(self.state.read_bytes(), original)
        self.assertEqual(
            self.cli("add-assumption", str(self.state), "U2", "P1", "--statement", "x > 0")[0], 0
        )
        self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))["schema_version"], 4)


if __name__ == "__main__":
    unittest.main()
