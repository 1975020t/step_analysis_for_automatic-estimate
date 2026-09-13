from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO

from src.models import CadFeatures


class CadAnalysisError(RuntimeError):
    pass


class CadAnalyzer:
    """CadQuery/OpenCASCADEを使うSTEP解析器。

    穴数は円筒面のGeometryを重複排除した簡易推定であり、製造Featureの
    厳密な認識ではない。
    """

    def analyze(self, source: str | Path | BinaryIO, file_name: str | None = None) -> CadFeatures:
        path: Path
        temporary_path: Path | None = None
        if hasattr(source, "read"):
            data = source.read()
            if hasattr(source, "seek"):
                source.seek(0)
            with NamedTemporaryFile(suffix=".step", delete=False) as handle:
                handle.write(data)
                temporary_path = Path(handle.name)
                path = temporary_path
            resolved_name = file_name or getattr(source, "name", "uploaded.step")
        else:
            path = Path(source)
            resolved_name = file_name or path.name

        try:
            return self._analyze_path(path, resolved_name)
        finally:
            if temporary_path:
                temporary_path.unlink(missing_ok=True)

    def _analyze_path(self, path: Path, file_name: str) -> CadFeatures:
        try:
            import cadquery as cq
        except ImportError as exc:
            raise CadAnalysisError(
                "CadQueryが未インストールです。`pip install -r requirements.txt` を実行してください。"
            ) from exc

        if path.suffix.lower() not in {".step", ".stp"}:
            raise CadAnalysisError("STEP/STPファイルを指定してください。")
        try:
            workplane = cq.importers.importStep(str(path))
            shape = workplane.val()
            solids = list(shape.Solids())
            if not solids:
                raise ValueError("STEP内に解析可能なSolidがありません")
            # AP242にはグラフィカルPMI（注記用の線・面）が同梱される場合がある。
            # 見積Geometryへ混入させないため、TopAbs_SOLID本体だけを再集約する。
            solid_shape = cq.Compound.makeCompound(solids)
            bbox = solid_shape.BoundingBox()
            faces = list(solid_shape.Faces())
            edges = list(solid_shape.Edges())
            vertices = list(solid_shape.Vertices())
            hole_diameters = self._estimate_holes(faces)
            return CadFeatures(
                file_name=file_name,
                solid_count=len(solids),
                bbox_x_mm=bbox.xlen,
                bbox_y_mm=bbox.ylen,
                bbox_z_mm=bbox.zlen,
                volume_mm3=sum(solid.Volume() for solid in solids),
                surface_area_mm2=sum(solid.Area() for solid in solids),
                face_count=len(faces),
                edge_count=len(edges),
                vertex_count=len(vertices),
                estimated_hole_count=len(hole_diameters),
                estimated_hole_diameters_mm=hole_diameters,
            )
        except Exception as exc:
            raise CadAnalysisError(f"STEPファイルを解析できませんでした: {exc}") from exc

    @staticmethod
    def _estimate_holes(faces: list) -> list[float]:
        grouped: dict[tuple[float, float, float, float], list[float]] = defaultdict(list)
        for face in faces:
            try:
                if face.geomType() != "CYLINDER":
                    continue
                adaptor = face._geomAdaptor()
                cylinder = adaptor.Cylinder()
                radius = float(cylinder.Radius())
                center = face.Center()
                key = (round(center.x, 3), round(center.y, 3), round(center.z, 3), round(radius, 3))
                grouped[key].append(radius * 2)
            except Exception:
                continue
        # 円筒面には外径面も混ざるため、部品外形の中心に近い最大径候補は除外せず、
        # あくまで「円筒面由来の推定穴候補」として表示する。
        return sorted(round(values[0], 3) for values in grouped.values())
