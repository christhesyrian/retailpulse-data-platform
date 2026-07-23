#!/usr/bin/env python3
"""Local, credential-free repository security check.

Verifies that secrets and raw business data cannot end up in Git. Never
reads or prints the contents of `.env` or any actual secret value.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

IGNORED_PATH_CHECKS = [".env", "data/bronze", "data/silver", "data/gold"]

# Patterns that always indicate a real secret, never a documentation example.
SECRET_PATTERNS = [
    re.compile(r"\bEAAA[A-Za-z0-9_\-]{20,}\b"),  # Square access token shape
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]

# Words/shapes that mark a value as documentation/placeholder, not a live secret.
PLACEHOLDER_MARKERS = re.compile(
    r"replace_me|your[_-]|<.*>|xxx|example|placeholder|\btoken\b|\bkey\b",
    re.IGNORECASE,
)
TOKEN_ASSIGNMENT = re.compile(r"SQUARE_ACCESS_TOKEN\s*=\s*(\S+)")
AUTH_HEADER = re.compile(r"Authorization:\s*Bearer\s+(\S+)", re.IGNORECASE)

EXCLUDED_DIR_NAMES = {".git", ".venv", "venv", "__pycache__", "data"}


def run(cmd: list[str]) -> tuple[int, str]:
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return result.returncode, result.stdout.strip()


def is_git_repo() -> bool:
    code, _ = run(["git", "rev-parse", "--is-inside-work-tree"])
    return code == 0


def tracked_files() -> list[Path]:
    if not is_git_repo():
        return []
    code, out = run(["git", "ls-files"])
    if code != 0 or not out:
        return []
    return [ROOT / line for line in out.splitlines()]


def check_env_ignored(failures: list[str]) -> None:
    if not is_git_repo():
        print("  (skipped: not a git repository yet)")
        return
    for path in IGNORED_PATH_CHECKS:
        # Trailing slash so check-ignore matches directory-only patterns
        # even when the directory doesn't exist on disk yet.
        code, out = run(["git", "check-ignore", "-q", f"{path}/"])
        if code != 0:
            failures.append(f"{path} is NOT covered by .gitignore")
        else:
            print(f"  ignored: {path}")


def check_env_not_tracked(failures: list[str]) -> None:
    if not is_git_repo():
        print("  (skipped: not a git repository yet)")
        return
    code, out = run(["git", "ls-files", ".env"])
    if out:
        failures.append(".env is TRACKED by git — remove it from the index immediately")
    else:
        print("  .env is not tracked")


def check_env_example_placeholders(failures: list[str]) -> None:
    example = ROOT / ".env.example"
    if not example.exists():
        failures.append(".env.example is missing")
        return
    for line in example.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.strip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "SQUARE_ACCESS_TOKEN" and not PLACEHOLDER_MARKERS.search(value.strip()):
            failures.append(".env.example SQUARE_ACCESS_TOKEN does not look like a placeholder")
    print("  .env.example contains placeholders only")


def _is_excluded(path: Path) -> bool:
    parts = path.parts
    if any(part in EXCLUDED_DIR_NAMES for part in parts):
        return True
    return any(part.endswith(".egg-info") for part in parts)


def check_no_secret_patterns(failures: list[str]) -> None:
    files = tracked_files() or [
        p for p in ROOT.rglob("*") if p.is_file() and not _is_excluded(p.relative_to(ROOT))
    ]
    hits = []
    for path in files:
        if path.name == ".env" or path.name == "security_check.py" or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                hits.append(f"{path.relative_to(ROOT)}: matches pattern {pattern.pattern!r}")

        for match in TOKEN_ASSIGNMENT.finditer(text):
            value = match.group(1)
            if not PLACEHOLDER_MARKERS.search(value):
                hits.append(f"{path.relative_to(ROOT)}: SQUARE_ACCESS_TOKEN assigned a non-placeholder value")

        for match in AUTH_HEADER.finditer(text):
            value = match.group(1)
            if not PLACEHOLDER_MARKERS.search(value):
                hits.append(f"{path.relative_to(ROOT)}: Authorization: Bearer header with a non-placeholder value")

    if hits:
        failures.extend(hits)
    else:
        print(f"  no secret-shaped patterns found in {len(files)} scanned file(s)")


def main() -> int:
    failures: list[str] = []

    print("Checking .gitignore coverage...")
    check_env_ignored(failures)

    print("Checking .env is not tracked...")
    check_env_not_tracked(failures)

    print("Checking .env.example...")
    check_env_example_placeholders(failures)

    print("Scanning for secret-shaped patterns...")
    check_no_secret_patterns(failures)

    if failures:
        print("\nSECURITY CHECK FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("\nSecurity check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
