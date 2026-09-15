import pytest

from src.sheetmetal_analyzer import SheetMetalAnalyzer
from src.sheetmetal_recognition import SheetMetalRecognition, SurfacePair


def test_thickness_cluster_prefers_area_supported_value():
    recognition = SheetMetalRecognition()
    candidates = [
        SurfacePair(0, 1, "PLANE", 2.0, 1000),
        SurfacePair(2, 3, "CYLINDER", 2.01, 400),
        SurfacePair(4, 5, "PLANE", 20.0, 50),
    ]
    assert recognition.select_thickness(candidates) == pytest.approx(2.002857, rel=1e-5)


def test_k_factor_must_be_physical_fraction():
    with pytest.raises(ValueError, match="Kファクター"):
        SheetMetalAnalyzer(k_factor=1.1)
