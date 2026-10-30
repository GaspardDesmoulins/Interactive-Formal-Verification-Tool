import contextlib
from dataclasses import replace
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from formal_agent.cli import main
from formal_agent.execution_policy import ExecutionBudget, execution_readiness
from formal_agent.storage import load, load_snapshot


ROOT = Path(__file__).resolve().parents[1]


class ExecutionPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.source = root / "example.py"
        self.evidence = root / "test_example.py"
        self.marker = root / "executed"
        self.state = root / "state.json"
        self.source.write_text(
            f"open({str(self.marker)!r}, 'w').write('target')\n"
            "def f(x): return x\n",
            encoding="utf-8",
        )
        self.evidence.write_text(
            f"open({str(self.marker)!r}, 'w').write('test')\n"
            "def test_f(): assert f(0) == 0\n",
            encoding="utf-8",
        )
        self.assertEqual(self.cli(
            "start", str(self.source), str(self.state), "--evidence", str(self.evidence)
        )[0], 0)
        self.assertEqual(self.cli("answer", str(self.state), "U1", "--answer", "Return x.")[0], 0)
        self.assertEqual(self.cli(
            "add-property", str(self.state), "U1", "--statement", "Return x."
        )[0], 0)
        self.assertEqual(self.cli(
            "add-scenario", str(self.state), "P1", "--description", "x is zero",
            "--expected", "Return zero.",
        )[0], 0)

    def cli(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_readiness_is_blocked_without_execution_or_isolation_probe(self) -> None:
        original = self.state.read_bytes()
        with patch("subprocess.Popen", side_effect=AssertionError("unexpected process")), \
             patch("socket.socket", side_effect=AssertionError("unexpected network")):
            code, output, error = self.cli("execution-readiness", str(self.state), "S1")
        self.assertEqual(code, 1)
        self.assertIn("Execution blocked", error)
        result = json.loads(output)
        self.assertEqual(set(result), {
            "kind", "version", "status", "execution_started", "isolation_checks",
            "investigation_revision", "source_sha256", "property_id", "scenario_id",
            "requested_budget", "blockers",
        })
        self.assertEqual(result["kind"], "test_execution_readiness")
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["execution_started"])
        self.assertEqual(result["isolation_checks"], "not_performed")
        self.assertEqual(result["investigation_revision"], hashlib.sha256(original).hexdigest())
        self.assertEqual(result["source_sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual((result["property_id"], result["scenario_id"]), ("P1", "S1"))
        self.assertEqual(result["requested_budget"], {
            "wall_seconds": 30, "cpu_seconds": 10, "vcpus": 1,
            "guest_memory_mib": 128, "host_memory_mib": 256, "storage_mib": 64,
            "output_kib": 64, "host_pids": 64,
        })
        self.assertEqual([item["code"] for item in result["blockers"]],
                         ["test_bundle_unavailable", "isolated_backend_unavailable"])
        self.assertNotIn(str(self.source), output)
        self.assertEqual(self.state.read_bytes(), original)
        self.assertFalse(self.marker.exists())

    def test_budget_boundaries_and_types(self) -> None:
        budget = ExecutionBudget()
        bounds = {
            "wall_seconds": (1, 60), "cpu_seconds": (1, 60), "vcpus": (1, 2),
            "guest_memory_mib": (128, 512), "host_memory_mib": (256, 768),
            "storage_mib": (16, 128), "output_kib": (1, 256), "host_pids": (16, 128),
        }
        for field, (minimum, maximum) in bounds.items():
            for value in (minimum, maximum):
                with self.subTest(field=field, value=value):
                    changes = {field: value}
                    if field == "guest_memory_mib":
                        changes["host_memory_mib"] = value + 128
                    replace(budget, **changes).validate()
            for value in (minimum - 1, maximum + 1, True, 1.0, "1"):
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(ValueError, field):
                        replace(budget, **{field: value}).validate()
        replace(budget, guest_memory_mib=512, host_memory_mib=640).validate()
        with self.assertRaisesRegex(ValueError, "at least 128"):
            replace(budget, guest_memory_mib=512, host_memory_mib=639).validate()

    def test_cli_custom_budget_and_invalid_inputs(self) -> None:
        original = self.state.read_bytes()
        code, output, error = self.cli(
            "execution-readiness", str(self.state), "S1", "--wall-seconds", "60",
            "--cpu-seconds", "1", "--vcpus", "2", "--guest-memory-mib", "512",
            "--host-memory-mib", "640", "--storage-mib", "128", "--output-kib", "256",
            "--host-pids", "128",
        )
        self.assertEqual(code, 1)
        self.assertIn("Execution blocked", error)
        self.assertEqual(json.loads(output)["requested_budget"], {
            "wall_seconds": 60, "cpu_seconds": 1, "vcpus": 2,
            "guest_memory_mib": 512, "host_memory_mib": 640, "storage_mib": 128,
            "output_kib": 256, "host_pids": 128,
        })
        for args, message in [
            (("execution-readiness", str(self.state), "S99"), "does not exist"),
            (("execution-readiness", str(self.state), "S1", "--wall-seconds", "0"), "wall_seconds"),
            (("execution-readiness", str(self.state), "S1", "--guest-memory-mib", "512",
              "--host-memory-mib", "639"), "at least 128"),
        ]:
            with self.subTest(args=args):
                code, output, error = self.cli(*args)
                self.assertEqual(code, 1)
                self.assertEqual(output, "")
                self.assertIn(message, error)
                self.assertEqual(self.state.read_bytes(), original)

    def test_changed_source_and_reference_are_explicit_blockers(self) -> None:
        original = self.state.read_bytes()
        for path in (self.source, self.evidence):
            content = path.read_bytes()
            path.write_text("# changed\n", encoding="utf-8")
            code, output, error = self.cli("execution-readiness", str(self.state), "S1")
            self.assertEqual(code, 1)
            self.assertIn("Execution blocked", error)
            result = json.loads(output)
            self.assertEqual(result["blockers"][0]["code"], "input_snapshot_unavailable")
            self.assertIn("changed", result["blockers"][0]["message"])
            self.assertFalse(result["execution_started"])
            self.assertEqual(self.state.read_bytes(), original)
            path.write_bytes(content)
        self.assertFalse(self.marker.exists())

    def test_invalid_revision_and_concurrent_snapshot_change_are_reported(self) -> None:
        state = load(self.state)
        with self.assertRaisesRegex(ValueError, "revision"):
            execution_readiness(state, "not-a-sha256", "S1", ExecutionBudget())
        calls = 0

        def change_after_snapshot(path: Path) -> tuple[dict, str]:
            nonlocal calls
            result = load_snapshot(path)
            calls += 1
            if calls == 1:
                path.write_text(json.dumps(result[0]), encoding="utf-8")
            return result

        with patch("formal_agent.cli.load_snapshot", side_effect=change_after_snapshot):
            code, output, error = self.cli("execution-readiness", str(self.state), "S1")
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn("Investigation changed", error)
        self.assertFalse(self.marker.exists())

    def test_legacy_state_readiness_never_writes_migration(self) -> None:
        state = load(self.state)
        state["schema_version"] = 8
        del state["scenario_suggestions"]
        del state["test_call_analyses"]
        self.state.write_text(json.dumps(state), encoding="utf-8")
        original = self.state.read_bytes()
        code, output, _ = self.cli("execution-readiness", str(self.state), "S1")
        self.assertEqual(code, 1)
        result = json.loads(output)
        self.assertEqual(result["investigation_revision"], hashlib.sha256(original).hexdigest())
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.state.read_bytes(), original)

    def test_finished_investigation_is_inspectable_but_not_authorized(self) -> None:
        self.assertEqual(self.cli("finish", str(self.state), "--allow-open")[0], 0)
        original = self.state.read_bytes()
        code, output, error = self.cli("execution-readiness", str(self.state), "S1")
        self.assertEqual(code, 1)
        self.assertIn("Execution blocked", error)
        self.assertFalse(json.loads(output)["execution_started"])
        self.assertEqual(self.state.read_bytes(), original)

    def test_real_cli_returns_nonzero_blocked_result_without_execution(self) -> None:
        original = self.state.read_bytes()
        result = subprocess.run(
            [sys.executable, "-m", "formal_agent", "execution-readiness", str(self.state), "S1"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["status"], "blocked")
        self.assertIn("Execution blocked", result.stderr)
        self.assertEqual(self.state.read_bytes(), original)
        self.assertFalse(self.marker.exists())

    def test_cli_help_explains_the_preview_and_budget_limits(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "formal_agent", "execution-readiness", "--help"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Preview only", result.stdout)
        self.assertIn("1..60 (default 30)", result.stdout)
        self.assertIn("not enforced", result.stdout)
        self.assertIn("at least 128 MiB", result.stdout)
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
