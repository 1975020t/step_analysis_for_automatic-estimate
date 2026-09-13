from pathlib import Path

import pytest

pytest.importorskip("cadquery")

from src.cad_analyzer import CadAnalyzer


def test_nist_step_geometry_can_be_read():
    path = Path("samples/nist_stc06.step")
    if not path.exists():
        pytest.skip("NISTサンプルが配置されていません")
    result = CadAnalyzer().analyze(path)
    assert result.volume_mm3 > 0
    assert result.bbox_x_mm > 0
    assert result.bbox_y_mm > 0
    assert result.bbox_z_mm > 0
    assert result.face_count > 0

