from __future__ import annotations

import io

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from src.chat_service import ChatQuoteService
from src.llm_client import build_llm_client
from src.master_loader import MasterLoader
from src.models import QuoteCondition
from src.quote_engine import QuoteEngine, QuoteUnavailableError
from src.dxf_analyzer import DxfAnalyzer
from src.sheetmetal_analyzer import SheetMetalAnalyzer


load_dotenv()
st.set_page_config(page_title="STEP・DXF板金解析・見積デモ", page_icon="◫", layout="wide")
st.title("STEP・DXF板金解析・見積デモ")
st.caption("STEP形状または展開図DXFをローカル解析し、マスター単価でルールベース見積を作成します。")

masters = MasterLoader("data")
try:
    llm = build_llm_client()
    llm_error = None
    mode_label = "OpenAI API利用中" if llm.mode == "openai" else "Mockモード"
    st.sidebar.info(f"チャット解釈モード: {mode_label}")
except Exception as exc:
    llm = None
    llm_error = str(exc)
    st.sidebar.error(f"チャット設定エラー: {llm_error}")
upload_col, setting_col = st.columns([2, 1])
with upload_col:
    uploaded = st.file_uploader("STEP／展開図DXFファイル", type=["step", "stp", "dxf"])
is_dxf = uploaded is not None and uploaded.name.lower().endswith(".dxf")
with setting_col:
    if is_dxf:
        thickness = st.number_input("板厚（mm）", min_value=0.0, value=0.0, step=0.1, format="%g",
                                    help="展開図DXFには板厚が含まれないため入力してください。")
    else:
        k_factor = st.number_input("Kファクター", min_value=0.0, max_value=1.0, value=0.33, step=0.01)
        confirmed_k = st.checkbox("指定済み加工条件として扱う", value=False)

analyze_clicked = st.button("解析を実行", type="primary",
                            disabled=uploaded is None or (is_dxf and not thickness > 0))
if is_dxf and not thickness > 0:
    st.info("展開図DXFの解析には板厚（mm）の入力が必要です。")
if analyze_clicked and uploaded is not None:
    data = uploaded.getvalue()
    if is_dxf:
        with st.spinner("展開図DXFを解析しています…"):
            st.session_state.analysis_result = DxfAnalyzer(thickness_mm=thickness).analyze(
                io.BytesIO(data), file_name=uploaded.name)
            st.session_state.step_bytes = None
    else:
        with st.spinner("STEP形状を解析しています…"):
            st.session_state.analysis_result = SheetMetalAnalyzer(
                k_factor=k_factor, k_factor_is_default=not confirmed_k,
            ).analyze(io.BytesIO(data), file_name=uploaded.name)
            st.session_state.step_bytes = data
            st.session_state.step_suffix = "." + uploaded.name.rsplit(".", 1)[-1].lower()
    st.session_state.quote_condition = QuoteCondition(
        material=masters.material_names[0], quantity=1
    )
    st.session_state.chat_history = []
    st.session_state.pop("quote_result", None)

result = st.session_state.get("analysis_result")
if result is None:
    st.info(".step／.stp、または展開図の .dxf（板厚を入力）を指定し、「解析を実行」を押してください。")
    st.stop()

st.write(f"**ファイル:** {result.file_name}")
if result.status in {"unsupported", "error"}:
    label = "解析不可" if result.status == "unsupported" else "解析エラー"
    st.error(f"{label}: {result.message}")
    st.code(result.reason_code or "ANALYSIS_ERROR", language=None)
elif result.status == "partial":
    st.warning("部分成功: 一部の値は概算です。根拠と警告を確認してください。")
else:
    st.success("解析成功: 対象範囲の一定板厚部品として認識しました。")

if result.status in {"success", "partial"}:
    columns = st.columns(5)
    columns[0].metric("板厚", f"{result.thickness_mm:g} mm")
    columns[1].metric("展開面積", f"{result.blank_area_mm2:,.1f} mm²")
    columns[2].metric("切断長", f"{result.cut_length_mm:,.1f} mm")
    columns[3].metric("穴数", f"{result.hole_count}")
    columns[4].metric("曲げ回数", f"{result.bend_count}")
    for warning in result.warnings:
        st.warning(warning)

    if not st.session_state.get("step_bytes") and result.flat_pattern and result.flat_pattern.outer_loops:
        from src.visualization import flat_pattern_figure

        st.plotly_chart(flat_pattern_figure(result.flat_pattern), width="stretch")
    elif st.session_state.get("step_bytes"):
        try:
            from src.visualization import flat_pattern_figure, original_shape_figure

            original_col, flat_col = st.columns(2)
            with original_col:
                st.plotly_chart(original_shape_figure(
                    st.session_state.step_bytes, st.session_state.get("step_suffix", ".step")
                ), width="stretch")
            with flat_col:
                if result.flat_pattern and result.flat_pattern.outer_loops:
                    st.plotly_chart(flat_pattern_figure(result.flat_pattern), width="stretch")
                else:
                    st.info("この形状では正確な2D輪郭を構築できないため、推定図は表示しません。")
        except Exception as exc:
            st.warning(f"形状プレビューを表示できませんでした: {exc}")

with st.expander("解析根拠と処理段階", expanded=result.status != "success"):
    if result.reason_codes:
        st.write("理由コード: " + ", ".join(result.reason_codes))
    st.dataframe(pd.DataFrame([
        {"処理段階": stage.name, "状態": stage.status, "説明": stage.message}
        for stage in result.stages
    ]), hide_index=True, width="stretch")
    if result.metric_quality:
        st.write("項目別の算出方法と信頼度")
        st.dataframe(pd.DataFrame([
            {"項目": name, "方式": quality.method, "信頼度": quality.confidence,
             "根拠": " / ".join(quality.evidence)}
            for name, quality in result.metric_quality.items()
        ]), hide_index=True, width="stretch")
    if result.assumptions:
        st.write("前提条件")
        for assumption in result.assumptions:
            st.write(f"- {assumption}")

if result.status in {"success", "partial"}:
    st.divider()
    st.subheader("ルールベース見積")
    condition = st.session_state.get("quote_condition") or QuoteCondition(
        material=masters.material_names[0], quantity=1
    )
    quote_col1, quote_col2 = st.columns(2)
    selected_material = quote_col1.selectbox(
        "材料", masters.material_names,
        index=masters.material_names.index(condition.material),
    )
    selected_quantity = quote_col2.number_input(
        "数量", min_value=1, value=condition.quantity, step=1
    )
    if selected_material != condition.material or selected_quantity != condition.quantity:
        condition = condition.model_copy(update={
            "material": selected_material, "quantity": int(selected_quantity)
        })
        st.session_state.quote_condition = condition

    try:
        quote = QuoteEngine(masters).calculate(result, condition)
        st.session_state.quote_result = quote
        if quote.is_estimate:
            st.warning("概算見積: 加工条件または解析値に概算を含みます。")
        st.metric("見積金額（税込・税別設定なし）", f"¥{quote.rounded_final_price:,}")
        st.dataframe(pd.DataFrame([
            {"コード": line.code, "項目": line.name, "数量": line.quantity,
             "単位": line.unit, "単価": line.unit_price, "金額": round(line.amount)}
            for line in quote.lines
        ]), hide_index=True, width="stretch")
    except QuoteUnavailableError as exc:
        st.error(str(exc))

    if llm is not None:
        st.info(f"チャット解釈モード: {mode_label}")
        prompt = st.chat_input("例: 数量を10個にして、皿もみを2箇所追加")
        if prompt:
            history = st.session_state.get("chat_history", [])
            try:
                applied = ChatQuoteService(llm, masters).interpret_and_apply(
                    prompt, condition, chat_history=history,
                    pending_confirmation=st.session_state.get("pending_confirmation"),
                )
                st.session_state.quote_condition = applied.condition
                st.session_state.chat_history = (history + [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": applied.message},
                ])[-8:]
                st.session_state.pending_confirmation = (
                    applied.message if applied.interpretation.status == "needs_confirmation" else None
                )
                st.rerun()
            except Exception as exc:
                st.error(f"チャットAPIエラー: {exc}。現在の見積は変更していません。")
    else:
        st.error(f"チャット設定エラー: {llm_error}。ルールベース見積はそのまま利用できます。")

with st.expander("解析結果 JSON"):
    st.json(result.model_dump(mode="json"))
