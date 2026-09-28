"""STEP -> binary glTF (GLB) for the browser viewer: one mesh, positions and triangle indices, in millimetres."""
from __future__ import annotations

import json
import struct
from pathlib import Path


def step_to_glb(path: str | Path, tolerance: float = 0.2) -> bytes:
    import cadquery as cq

    shape = cq.importers.importStep(str(path)).val()
    vertices, triangles = shape.tessellate(tolerance)
    return glb([(float(v.x), float(v.y), float(v.z)) for v in vertices], [tuple(int(i) for i in t) for t in triangles])


def glb(positions: list[tuple[float, float, float]], triangles: list[tuple[int, int, int]]) -> bytes:
    if not positions or not triangles:
        raise ValueError("形状に表示できる面がありません。")
    pos = b"".join(struct.pack("<3f", *p) for p in positions)
    idx = b"".join(struct.pack("<3I", *t) for t in triangles)
    pad = lambda b, fill=b"\0": b + fill * (-len(b) % 4)  # noqa: E731
    binary = pad(pos) + pad(idx)
    lo = [min(p[i] for p in positions) for i in range(3)]
    hi = [max(p[i] for p in positions) for i in range(3)]
    document = {
        "asset": {"version": "2.0", "generator": "step-sheetmetal-estimate"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "part"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.31, 0.55, 0.84, 1.0], "metallicFactor": 0.3,
                                                "roughnessFactor": 0.6}, "doubleSided": True}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(pos), "target": 34962},
                        {"buffer": 0, "byteOffset": len(pad(pos)), "byteLength": len(idx), "target": 34963}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(positions), "type": "VEC3", "min": lo, "max": hi},
                      {"bufferView": 1, "componentType": 5125, "count": len(triangles) * 3, "type": "SCALAR"}],
    }
    header_json = pad(json.dumps(document, separators=(",", ":")).encode("utf-8"), b" ")
    body = struct.pack("<II", len(header_json), 0x4E4F534A) + header_json + struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body
