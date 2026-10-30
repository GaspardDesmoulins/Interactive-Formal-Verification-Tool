import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.environ.get("IFVT_OLLAMA_INTEGRATION") == "1",
    "Set IFVT_OLLAMA_INTEGRATION=1 to run against the local Ollama server.",
)
class OllamaIntegrationTests(unittest.TestCase):
    def test_oversized_context_is_rejected_without_changing_investigation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "example.py"
            state = root / "state.json"
            marker = root / "executed"
            source.write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).touch()\n"
                'def f():\n    """' + "0123456789 abcdefghij " * 1600 + '"""\n    return 1\n',
                encoding="utf-8",
            )

            def invoke(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, "-m", "formal_agent", *args],
                    cwd=ROOT, capture_output=True, text=True, timeout=240,
                )

            started = invoke("start", str(source), str(state))
            self.assertEqual(started.returncode, 0, started.stderr)
            original = state.read_bytes()
            result = invoke(
                "propose-ollama", str(state), "--context-tokens", "512",
                "--timeout", "180", "--max-tokens", "16",
            )
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("Ollama returned HTTP 400", result.stderr)
            self.assertIn("exceeds the available context size", result.stderr)
            self.assertEqual(state.read_bytes(), original)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
