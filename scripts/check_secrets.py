from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


PATTERNS = {
    "OpenAI API key": re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "generic secret assignment": re.compile(
        r"(?im)^[ \t]*(?:OPENAI_API_KEY|API_KEY|SECRET|ACCESS_TOKEN|PASSWORD)[ \t]*=[ \t]*['\"]?"
        r"(?!YOUR_|example|placeholder|changeme)[A-Za-z0-9_./+=-]{20,}['\"]?[ \t]*$"
    ),
}
TEXT_EXTENSIONS = {
    ".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".json", ".csv",
    ".ini", ".cfg", ".env", ".sh", ".ps1", ".js", ".ts",
}


def tracked_files() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "-co", "--exclude-standard"], text=True)
    return [Path(line) for line in output.splitlines() if line]


def scan_text(label: str, text: str) -> list[str]:
    findings: list[str] = []
    for name, pattern in PATTERNS.items():
        if pattern.search(text):
            findings.append(f"{label}: {name}")
    return findings


def main() -> int:
    findings: list[str] = []
    for path in tracked_files():
        if not path.is_file() or (
            path.suffix.lower() not in TEXT_EXTENSIONS
            and path.name != ".gitignore"
            and not path.name.startswith(".env")
        ):
            continue
        try:
            findings.extend(scan_text(str(path), path.read_text(encoding="utf-8", errors="ignore")))
        except OSError as exc:
            findings.append(f"{path}: read error: {exc}")

    if "--history" in sys.argv:
        history = subprocess.run(
            ["git", "log", "-p", "--all", "--no-ext-diff"],
            check=True, capture_output=True, text=True, errors="ignore",
        ).stdout
        findings.extend(scan_text("git history", history))

    if findings:
        print("Secret scan failed:")
        for finding in sorted(set(findings)):
            print(f"- {finding}")
        return 1
    print("Secret scan passed: no known secret patterns found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
