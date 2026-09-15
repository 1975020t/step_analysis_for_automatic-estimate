from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile

from src.models import FlatPatternSummary
from src.sheetmetal_geometry import shape_mesh


def original_shape_figure(data: bytes, suffix: str = ".step"):
    import cadquery as cq
    import plotly.graph_objects as go

    temporary: Path | None = None
    try:
        with NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(data)
            temporary = Path(handle.name)
        shape = cq.importers.importStep(str(temporary)).val()
        mesh = shape_mesh(shape)
        figure = go.Figure(data=[go.Mesh3d(
            **mesh, color="#4f8bd6", opacity=1.0, flatshading=False,
            lighting={"ambient": 0.45, "diffuse": 0.75, "specular": 0.35},
        )])
        figure.update_layout(
            margin={"l": 0, "r": 0, "t": 30, "b": 0}, height=430,
            scene={"aspectmode": "data", "xaxis_title": "X mm", "yaxis_title": "Y mm", "zaxis_title": "Z mm"},
            title="元の3D形状（ローカル描画）",
        )
        return figure
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def flat_pattern_figure(pattern: FlatPatternSummary):
    import plotly.graph_objects as go

    figure = go.Figure()
    for index, loop in enumerate(pattern.outer_loops):
        x, y = zip(*loop)
        figure.add_trace(go.Scatter(
            x=x, y=y, mode="lines", line={"color": "#146c94", "width": 3},
            name="外周" if index == 0 else "外周", fill="toself", fillcolor="rgba(20,108,148,0.10)",
        ))
    for index, loop in enumerate(pattern.inner_loops):
        x, y = zip(*loop)
        figure.add_trace(go.Scatter(
            x=x, y=y, mode="lines", line={"color": "#d1495b", "width": 2},
            name="穴・内周" if index == 0 else "穴・内周", fill="toself", fillcolor="white",
        ))
    for index, line in enumerate(pattern.bend_lines):
        x, y = zip(*line)
        figure.add_trace(go.Scatter(
            x=x, y=y, mode="lines", line={"color": "#ed8b00", "width": 2, "dash": "dash"},
            name="曲げ線" if index == 0 else "曲げ線",
        ))
    figure.update_layout(
        title="解析後の展開形状（ローカル描画）", height=430,
        margin={"l": 20, "r": 20, "t": 45, "b": 20},
        xaxis={"title": "X mm", "scaleanchor": "y", "scaleratio": 1},
        yaxis={"title": "Y mm"}, showlegend=True,
    )
    return figure
