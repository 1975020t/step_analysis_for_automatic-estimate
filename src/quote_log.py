"""Quote numbers and the output log (output/quote_log.csv, not in Git).

The number is `Q` + issue date (YYYYMMDD) + `-` + the day's serial (3 digits). The serial comes from the log,
and taking the number and writing the log row happen under one lock file, so two outputs never share a number.
"""
from __future__ import annotations

import csv
import os
import time
from datetime import datetime
from pathlib import Path

FIELDS = ["quote_no", "issued_at", "customer", "subject", "drawing_no", "quantity", "total", "is_estimate",
          "input_files"]


class QuoteLog:
    def __init__(self, path: str | Path = "output/quote_log.csv") -> None:
        self.path = Path(path)

    def rows(self) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def next_number(self, issued_at: datetime) -> str:
        prefix = f"Q{issued_at:%Y%m%d}-"
        serials = [int(row["quote_no"][len(prefix):]) for row in self.rows()
                   if (row.get("quote_no") or "").startswith(prefix) and row["quote_no"][len(prefix):].isdigit()]
        return f"{prefix}{max(serials, default=0) + 1:03d}"

    def issue(self, issued_at: datetime, row: dict[str, object]) -> str:
        """Take the next number for the issue date and write its log row; returns the number."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _Lock(self.path.with_suffix(".lock")):
            number = self.next_number(issued_at)
            new_file = not self.path.exists()
            with self.path.open("a", encoding="utf-8-sig" if new_file else "utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                if new_file:
                    writer.writeheader()
                writer.writerow({**{k: row.get(k, "") for k in FIELDS}, "quote_no": number,
                                 "issued_at": issued_at.isoformat(timespec="seconds")})
        return number


class _Lock:
    def __init__(self, path: Path, timeout_s: float = 10.0) -> None:
        self.path, self.timeout_s = path, timeout_s

    def __enter__(self):
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > 60:  # left behind by a crashed run
                        self.path.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError(f"見積番号の記録ファイルがロックされたままです: {self.path}")
                time.sleep(0.05)

    def __exit__(self, *exc) -> None:
        self.path.unlink(missing_ok=True)
