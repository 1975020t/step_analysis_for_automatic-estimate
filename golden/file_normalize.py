"""Make generated data files byte-identical across runs, so they can be kept in Git.

OpenCascade writes the export time and a per-process counter into a STEP header; ezdxf writes creation /
update times and random GUIDs into the DXF header. The geometry is already deterministic, so fixing these
fields makes a regeneration produce exactly the committed files.
"""
from __future__ import annotations

import re
from pathlib import Path

FIXED_TIME = "2026-01-01T00:00:00"
FIXED_JULIAN = "2461041.5"  # 2026-01-01 00:00 as a Julian date (DXF $TD* variables)


def normalize_step(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    new = re.sub(r"(FILE_NAME\('[^']*',)'[^']*'", rf"\1'{FIXED_TIME}'", text, count=1)
    new = re.sub(r"Open CASCADE STEP translator ([\d.]+) \d+", path.stem, new)
    if new != text:
        path.write_text(new, encoding="utf-8")


_DXF_TIME = re.compile(r"(\$(?:TDCREATE|TDUCREATE|TDUPDATE|TDUUPDATE)\r?\n\s*40\r?\n)[^\r\n]+")
_DXF_WRITER = re.compile(r"(\d+\.\d+\.\d+ @ )\d{4}-\d\d-\d\dT[\d:.]+(?:\+00:00)?")  # ezdxf metadata
_DXF_GUID = re.compile(r"(\$(?:FINGERPRINTGUID|VERSIONGUID)\r?\n\s*2\r?\n)\{[0-9A-Fa-f-]+\}")


def normalize_dxf(path: Path) -> None:
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="surrogateescape")
    new = _DXF_TIME.sub(lambda m: m.group(1) + FIXED_JULIAN, text)
    new = _DXF_WRITER.sub(lambda m: m.group(1) + FIXED_TIME + "+00:00", new)
    new = _DXF_GUID.sub(lambda m: m.group(1) + "{00000000-0000-0000-0000-000000000000}", new)
    if new != text:
        path.write_bytes(new.encode("utf-8", errors="surrogateescape"))


def normalize_tree(root: str | Path) -> int:
    """Normalize every .step / .dxf file under root. Returns the number of files."""
    n = 0
    for path in Path(root).rglob("*"):
        suffix = path.suffix.lower()
        if suffix in (".step", ".stp"):
            normalize_step(path)
            n += 1
        elif suffix == ".dxf":
            normalize_dxf(path)
            n += 1
    return n
