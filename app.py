from __future__ import annotations

import pandas as pd
import streamlit as st

from src.sheetmetal_analyzer import SheetMetalAnalyzer


st.set_page_config(page_title="STEP板金解析デモ", page_icon="◫", layout="wide")
st.title("STEP板金解析デモ")
st.caption("単一・一定板厚の一般的な板金部品から、見積用の5項目を取得します。")

uploaded = st.file_uploader("STEPファイル", type=["step", "stp"])
analyze_clicked = st.button("解析を実行", type="primary", disabled=uploaded is None)

if analyze_clicked and uploaded is not None:
    with st.spinner("STEP形状を解析しています…"):
        st.session_state.analysis_result = SheetMetalAnalyzer().analyze(
            uploaded, file_name=uploaded.name
        )

result = st.session_state.get("analysis_result")
if result is None:
    st.info(".step または .stp ファイルを指定し、「解析を実行」を押してください。")
    st.stop()

st.write(f"**ファイル:** {result.file_name}")
if result.status != "success":
    label = "解析不可" if result.status == "unsupported" else "解析エラー"
    st.error(f"{label}: {result.message}")
    st.code(result.reason_code or "ANALYSIS_ERROR", language=None)
else:
    st.success("解析成功: 単一・一定板厚の板金部品として認識しました。")
    columns = st.columns(5)
    columns[0].metric("板厚", f"{result.thickness_mm:g} mm")
    columns[1].metric("展開面積", f"{result.blank_area_mm2:,.1f} mm²")
    columns[2].metric("切断長", f"{result.cut_length_mm:,.1f} mm")
    columns[3].metric("穴数", f"{result.hole_count}")
    columns[4].metric("曲げ回数", f"{result.bend_count}")
    for warning in result.warnings:
        st.warning(warning)

with st.expander("解析根拠と処理段階", expanded=result.status != "success"):
    st.dataframe(
        pd.DataFrame([
            {"処理段階": stage.name, "状態": stage.status, "説明": stage.message}
            for stage in result.stages
        ]),
        hide_index=True,
        width="stretch",
    )
    if result.status == "success":
        st.write(
            f"展開表現: 中立面 {result.flat_pattern.surface_region_count}領域、"
            f"外周 {result.flat_pattern.outer_boundary_count}、"
            f"閉じた内周 {result.flat_pattern.inner_boundary_count}"
        )
        st.write("板厚根拠（対応する表裏面）")
        st.dataframe(
            pd.DataFrame([
                {
                    "面種別": item.kind,
                    "面番号": f"{item.face_indices[0]} / {item.face_indices[1]}",
                    "中立面積 mm²": item.mid_surface_area_mm2,
                }
                for item in result.thickness_evidence
            ]),
            hide_index=True,
            width="stretch",
        )
        if result.bend_evidence:
            st.write("曲げ根拠（円筒面の軸と半径）")
            st.dataframe(
                pd.DataFrame([item.model_dump() for item in result.bend_evidence]),
                hide_index=True,
                width="stretch",
            )
        if result.hole_evidence:
            st.write("穴根拠（外周から独立した閉じた切断境界）")
            st.dataframe(
                pd.DataFrame([item.model_dump() for item in result.hole_evidence]),
                hide_index=True,
                width="stretch",
            )

with st.expander("解析結果 JSON"):
    st.json(result.model_dump(mode="json"))
