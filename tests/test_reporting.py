import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from formal_agent.cli import main


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "example.py"
        self.state = self.root / "state.json"
        self.source.write_text("def f(x):\n    return x\n", encoding="utf-8")
        self.assertEqual(self.run_cli("start", str(self.source), str(self.state))[0], 0)

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_report_distinguishes_decisions_and_verification(self) -> None:
        self.assertEqual(
            self.run_cli("answer", str(self.state), "U2", "--answer", "x is positive")[0], 0
        )
        self.assertEqual(
            self.run_cli("add-property", str(self.state), "U2", "--statement", "x > 0")[0], 0
        )
        self.assertEqual(self.run_cli("accept-property", str(self.state), "P1")[0], 0)
        code, report, error = self.run_cli("report", str(self.state))
        self.assertEqual(code, 0)
        self.assertEqual(error, "")
        self.assertIn('"U1" [open]', report)
        self.assertIn('"U2" [answered]', report)
        self.assertIn('User answer: "x is positive"', report)
        self.assertIn('P1 [accepted] "x > 0"', report)
        self.assertIn('source answer: "U2"', report)
        self.assertIn("verification: not performed", report)
        self.assertIn("Verification results: none", report)
        self.assertEqual(self.run_cli("report", str(self.state))[1], report)

    def test_report_identifies_stale_and_missing_sources(self) -> None:
        self.source.write_text("def f(): pass\n", encoding="utf-8")
        result, report, _ = self.run_cli("report", str(self.state))
        self.assertEqual(result, 0)
        self.assertIn("Source has changed", report)
        self.source.unlink()
        result, report, _ = self.run_cli("report", str(self.state))
        self.assertEqual(result, 0)
        self.assertIn("Source cannot be read", report)

    def test_multiline_user_text_cannot_forge_report_lines(self) -> None:
        self.assertEqual(
            self.run_cli("answer", str(self.state), "U1", "--answer", "ok\nVerification: proved")[0],
            0,
        )
        report = self.run_cli("report", str(self.state))[1]
        self.assertIn('User answer: "ok\\nVerification: proved"', report)
        self.assertNotIn("\nVerification: proved", report)


if __name__ == "__main__":
    unittest.main()
