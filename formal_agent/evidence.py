"""Capture explicit, untrusted reference files without executing them."""

import hashlib
from pathlib import Path
from typing import Any


MAX_EVIDENCE_BYTES = 32768


def discover_evidence(source: Path) -> list[Path]:
    source = source.resolve()
    root = source.parent
    candidates = [
        root / "README.md",
        source.with_suffix(".md"),
        root / f"test_{source.stem}.py",
        root / f"{source.stem}_test.py",
        root / "tests" / f"test_{source.stem}.py",
    ]
    result = []
    seen = set()
    for candidate in candidates:
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"Discovered evidence is outside the source directory: {candidate}")
        if resolved not in seen:
            result.append(resolved)
            seen.add(resolved)
    return result


def read_evidence(path: Path) -> tuple[str, str]:
    with path.open("rb") as file:
        raw = file.read(MAX_EVIDENCE_BYTES + 1)
    if len(raw) > MAX_EVIDENCE_BYTES:
        raise ValueError(f"Evidence file exceeds {MAX_EVIDENCE_BYTES} bytes: {path}")
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()


def capture_evidence(paths: list[Path], source: str) -> list[dict[str, str]]:
    seen = {str(Path(source).resolve())}
    references = []
    for path in paths:
        resolved = str(path.resolve())
        if resolved in seen:
            raise ValueError(f"Duplicate evidence or source file: {resolved}")
        _, digest = read_evidence(Path(resolved))
        references.append({"path": resolved, "sha256": digest})
        seen.add(resolved)
    return references


def evidence_warning(model: dict[str, Any]) -> str | None:
    for item in model.get("evidence_sources", []):
        path = Path(item["path"])
        try:
            _, digest = read_evidence(path)
        except (OSError, UnicodeError, ValueError) as error:
            return f"Evidence cannot be read ({path}): {error}"
        if digest != item["sha256"]:
            return f"Evidence has changed since this investigation was created: {path}"
    return None


def evidence_context(model: dict[str, Any]) -> list[dict[str, str]]:
    result = []
    for item in model.get("evidence_sources", []):
        path = Path(item["path"])
        content, digest = read_evidence(path)
        if digest != item["sha256"]:
            raise ValueError(f"Evidence changed before the reasoning request: {path}")
        result.append({"name": path.name, "sha256": digest, "text": content})
    return result


def reference_at_line(model: dict[str, Any], path: Path, line: int) -> dict[str, Any]:
    resolved = str(path.resolve())
    reference = next(
        (item for item in model.get("evidence_sources", []) if item["path"] == resolved),
        None,
    )
    if reference is None:
        raise ValueError(f"Reference was not included when the investigation started: {resolved}")
    text, digest = read_evidence(Path(resolved))
    if digest != reference["sha256"]:
        raise ValueError(f"Evidence has changed: {resolved}")
    if line < 1 or line > len(text.splitlines()):
        raise ValueError(f"Reference line {line} does not exist: {resolved}")
    return {"path": resolved, "sha256": digest, "line": line}
