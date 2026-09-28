from __future__ import annotations

import io
import os
from datetime import date, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from src.chat_service import ChatQuoteService
from src.llm_client import build_llm_client
from src.master_loader import MasterLoader
from src.models import QuoteCondition
from src.quote_engine import QuoteEngine, QuoteUnavailableError
from src.dxf_analyzer import DxfAnalyzer
from src.pdf_quote import CONFIRMED, MISSING, REVIEW, UNREG, ConditionItem, condition_items, quote_condition
from src.quote_document import (PartInfo, Recipient, build_document, default_subject, load_company, log_row,
                                price_summary, unregistered_process_texts)
from src.quote_log import QuoteLog
from src.past_quotes import OUTCOMES, HistoryStore, from_document
from src.similar_quotes import SimilarQuoteSearch, query_for
from src.quote_pdf import render_internal, render_quote
from src.sheetmetal_analyzer import SheetMetalAnalyzer


load_dotenv()
st.set_page_config(page_title="STEP・DXF板金解析・見積デモ", page_icon="◫", layout="wide")
st.title("STEP・DXF板金解析・見積デモ")
st.caption("STEP形状または展開図DXFをローカル解析し、図面PDFの加工条件と合わせて、マスター単価でルールベース見積を作成します。")

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
    pdf_file = st.file_uploader("図面PDF（任意：材質・数量・表面処理・追加加工・特急を読み取ります）", type=["pdf"])
is_dxf = uploaded is not None and uploaded.name.lower().endswith(".dxf")
with setting_col:
    if is_dxf:
        thickness = st.number_input("板厚（mm）", min_value=0.0, value=0.0, step=0.1, format="%g",
                                    help="展開図DXFには板厚が含まれないため入力してください。")
    else:
        k_factor = st.number_input("Kファクター", min_value=0.0, max_value=1.0, value=0.33, step=0.01)
        confirmed_k = st.checkbox("指定済み加工条件として扱う", value=False)

analyze_clicked = st.button("解析を実行", type="primary",
                            disabled=uploaded is None or (is_dxf and not thickness > 0 and pdf_file is None))
if is_dxf and not thickness > 0:
    st.info("展開図DXFの解析には板厚（mm）の入力が必要です（図面PDFを指定した場合は図面の板厚を使います）。")
if analyze_clicked and uploaded is not None:
    data = uploaded.getvalue()
    for key in ("pdf_reading", "pdf_items", "pdf_unregistered", "doc_files"):
        st.session_state.pop(key, None)
    if pdf_file is not None:
        with st.spinner("図面PDFから加工条件を読み取っています…（Claude API）"):
            try:
                from src.pdf_reader import PdfConditionReader

                with NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
                    handle.write(pdf_file.getvalue())
                reading = PdfConditionReader().read(handle.name)
                st.session_state.pdf_unregistered = unregistered_process_texts(reading.pop("evidence", None), masters)
                st.session_state.pdf_reading = reading
                st.session_state.pdf_name = pdf_file.name
            except Exception as exc:
                st.error(f"図面PDFを読み取れませんでした: {exc}。加工条件は手入力してください。")
            finally:
                Path(handle.name).unlink(missing_ok=True)
    if is_dxf and not thickness > 0:
        thickness = float((st.session_state.get("pdf_reading") or {}).get("thickness_mm") or 0)
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

BADGE = {CONFIRMED: "✅ 確定", REVIEW: "⚠️ 要確認", UNREG: "❌ 未登録", MISSING: "➖ 記載なし"}


def pdf_condition_editor(reading: dict, base: QuoteCondition, analysis) -> QuoteCondition:
    """Conditions read from the drawing PDF, as editable inputs. Items that are not 確定 are not priced
    until the user corrects them or ticks 「この値で確定」."""
    st.markdown(f"**図面から読み取った加工条件**（{st.session_state.get('pdf_name', '図面PDF')}"
                f"{'、図番 ' + reading['drawing_no'] if reading.get('drawing_no') else ''}"
                f"{'、改訂 ' + reading['revision'] if reading.get('revision') else ''}）")
    items = condition_items(reading, masters)
    counts = {status: sum(item.status == status for item in items) for status in BADGE}
    st.caption("　".join(f"{BADGE[s]} {n}件" for s, n in counts.items() if n))
    if reading.get("flags"):
        labels = {"tolerance": "厳しい公差", "appearance": "外観指定", "inspection": "検査・証明書"}
        st.warning("特記事項: " + "、".join(labels.get(f, f) for f in reading["flags"]) + "（見積には含めません）")
    final: list[ConditionItem] = []
    material_codes, finish_codes = list(masters.materials), list(masters.surface_treatments)
    for item in items:
        cols = st.columns([1.1, 2.6, 1.0, 2.6, 1.3])
        cols[0].markdown(f"**{item.label}**")
        key = f"pdf_{item.field}"
        with cols[1]:
            if item.field == "material":
                default = item.value if item.value in material_codes else base.material
                value = st.selectbox(item.label, material_codes, index=material_codes.index(default), key=key,
                                     format_func=lambda c: masters.materials[c]["display_name"], label_visibility="collapsed")
            elif item.field == "surface_treatment":
                default = item.value if item.value in finish_codes else "NONE"
                value = st.selectbox(item.label, finish_codes, index=finish_codes.index(default), key=key,
                                     format_func=lambda c: masters.surface_treatments[c]["display_name"], label_visibility="collapsed")
            elif item.field == "thickness_mm":
                default = float(item.value or analysis.thickness_mm or 0.0)
                value = st.number_input(item.label, min_value=0.0, value=default, step=0.1, format="%g", key=key,
                                        label_visibility="collapsed")
            elif item.field == "quantity":
                value = int(st.number_input(item.label, min_value=1, value=int(item.value or 1), step=1, key=key,
                                            label_visibility="collapsed"))
            elif item.field == "rush":
                value = st.checkbox("特急", value=bool(item.value), key=key)
            else:
                rows = [{"加工": p["code"], "個数/個": int(p.get("count_per_part") or 0)} for p in item.value or []
                        if p.get("code") in masters.process_rates]
                table = st.data_editor(
                    pd.DataFrame(rows, columns=["加工", "個数/個"]), num_rows="dynamic", key=key, hide_index=True,
                    column_config={"加工": st.column_config.SelectboxColumn(options=masters.llm_process_codes)})
                value = [{"code": r["加工"], "count_per_part": int(r["個数/個"])} for r in table.to_dict("records")
                         if r.get("加工") and r.get("個数/個")]
        cols[2].markdown(BADGE[item.status])
        cols[3].caption(item.display if not item.reasons else f"{item.display} ／ " + "、".join(item.reasons))
        changed = _edited(item, value)
        confirmed = item.status == CONFIRMED or changed
        if item.status != CONFIRMED:
            confirmed = cols[4].checkbox("この値で確定", value=changed, key=f"{key}_ok")
        final.append(ConditionItem(item.field, item.label, value, item.display,
                                   CONFIRMED if confirmed else item.status, item.reasons))
    st.session_state.pdf_items = final
    material_input = next(i.value for i in final if i.field == "material")
    condition = quote_condition(final, masters, material_input, analysis_thickness=analysis.thickness_mm)
    chat_extra = [p for p in base.additional_processes if p.source == "chat"]
    return condition.model_copy(update={"additional_processes": condition.additional_processes + chat_extra})


def _edited(item: ConditionItem, value) -> bool:
    if item.field == "processes":
        read = sorted((p["code"], p.get("count_per_part")) for p in item.value or [] if p.get("code") != "UNREGISTERED")
        return read != sorted((p["code"], p["count_per_part"]) for p in value)
    if item.value is None or item.value == "UNREGISTERED":
        return False  # a value the user has not looked at is not confirmed by default
    if item.field == "thickness_mm":
        return abs(float(item.value) - float(value)) > 1e-9
    return item.value != value


def history_store() -> HistoryStore:
    return HistoryStore(os.environ.get("PAST_QUOTES_PATH", "data/past_quotes/history.csv"))


@st.cache_resource(show_spinner=False)
def _search_index(path: str, mtime: float) -> SimilarQuoteSearch:  # rebuilt when the history file changes
    return SimilarQuoteSearch(HistoryStore(path).load(), masters)


def search_index() -> SimilarQuoteSearch:
    store = history_store()
    return _search_index(str(store.path), store.path.stat().st_mtime if store.path.exists() else 0.0)


def case_inputs(analysis):
    """Recipient and part: used by the similar-quote search and printed on the quotation."""
    st.divider()
    st.subheader("顧客と部品")
    reading = st.session_state.get("pdf_reading") or {}
    col1, col2 = st.columns(2)
    customer = col1.text_input("宛先の会社名（必須）", key="doc_customer", placeholder="サンプル電機株式会社")
    person = col2.text_input("部署・担当者名（任意）", key="doc_person", placeholder="購買部　山田 太郎")
    col3, col4, col5 = st.columns([2, 1.4, 0.6])
    part = PartInfo(
        name=col3.text_input("品名", value=Path(analysis.file_name).stem, key=f"doc_part_{analysis.file_name}"),
        drawing_no=col4.text_input("図番", value=reading.get("drawing_no") or "", key=f"doc_dwg_{analysis.file_name}"),
        revision=col5.text_input("改訂", value=reading.get("revision") or "", key=f"doc_rev_{analysis.file_name}"),
        shape_file=analysis.file_name, drawing_file=st.session_state.get("pdf_name", "") if reading else "")
    return customer, person, part


def _yen(value) -> str:
    return "-" if value is None else f"¥{value:,.0f}"


def similar_quotes_section(analysis, condition: QuoteCondition, quote, summary, customer: str, part: PartInfo) -> None:
    """類似見積（参考）: up to 5 past quotes with reasons, differences and prices. The quote is not changed."""
    st.divider()
    st.subheader("類似見積（参考）")
    index = search_index()
    if not index.quotes:
        st.info("見積履歴がありません（data/past_quotes/history.csv）。")
        return
    query = query_for(analysis, condition, summary.unit_price, quote.final_price / condition.quantity, customer,
                      part.drawing_no, part.revision, today=date.today())
    matches = index.search(query)
    st.caption(f"履歴 {len(index.quotes):,}件から、値段を決める要素（材質の系統・板厚・数量帯・大きさ・加工）が近いものを最大5件。"
               "リピート（同じ顧客・同じ図番）は必ず先頭に出します。参考表示のみで、今回の単価は変えません。")
    if not matches:
        st.info("材質の系統と板厚が近い過去の見積はありません。")
        return
    reference = index.reference(matches)
    if reference:
        gap = (summary.unit_price - reference.unit) / reference.unit
        st.info(f"参考：過去の出し値の水準で見た今回の単価 **{_yen(reference.unit)}**（{reference.basis}）。"
                f"今回の単価 {_yen(summary.unit_price)} はこれより {gap:+.1%}。")
    for n, m in enumerate(matches, start=1):
        q = m.quote
        with st.container(border=True):
            drawing = " ".join(x for x in (q.drawing_no, f"Rev.{q.revision}" if q.revision else "") if x) or "図番なし"
            st.markdown(f"**{n}. 【{m.category}】** {q.date:%Y/%m/%d}　{q.customer}　{drawing}　{q.part_name}")
            procs = q.processes_text or "追加加工なし"
            st.caption(f"{q.material_text or '-'} t{q.thickness:g}　数量 {q.quantity or '-'}　"
                       f"表面処理 {q.finish_text or '記録なし'}　{procs}　{'特急' if q.rush else ''}　結果 {q.outcome}"
                       if q.thickness is not None else f"{q.material_text}　数量 {q.quantity}　結果 {q.outcome}")
            cols = st.columns(3)
            diff = f"（今回比 {m.price_diff:+.1%}）" if m.price_diff is not None else ""
            cols[0].markdown(f"単価 **{_yen(q.unit_price)}**{diff}")
            if m.leveled_unit:
                cols[1].markdown(f"出し値÷今のマスターの標準単価 **{m.ratio:.2f}**（標準 {_yen(m.past_standard)}）")
                cols[2].markdown(f"この水準で見た今回の単価 **{_yen(m.leveled_unit)}**")
            else:
                cols[1].caption(m.standard_note or "標準単価を計算できません")
            st.markdown("似ている理由：" + "、".join(m.reasons))
            st.markdown("今回との違い：" + "、".join(m.differences))
            for warning in m.warnings:
                st.warning(warning)
            with st.expander("この見積の全項目"):
                detail = {k: v for k, v in q.original.items()} or {}
                st.dataframe(pd.DataFrame([{"項目": k, "値": str(v)} for k, v in detail.items()]
                                          + [{"項目": "（取り込み元）", "値": q.source}]),
                             hide_index=True, width="stretch")


def quote_document_section(analysis, condition: QuoteCondition, quote, customer: str, person: str, part: PartInfo) -> None:
    """見積書を出力: terms, then a PDF (and the internal basis) to download; the quote enters the history. No LLM."""
    st.divider()
    st.subheader("見積書を出力")
    company = load_company("data")
    reading = st.session_state.get("pdf_reading") or {}
    items = st.session_state.get("pdf_items") if reading else None
    col6, col7 = st.columns([2, 1])
    subject = col6.text_input("件名", value=default_subject(part))
    delivery_place = col7.text_input("受渡場所", value=company.delivery_place)
    free_remarks = st.text_area("備考（任意、1行に1項目）", key="doc_remarks", height=80)
    with_internal = st.checkbox("社内用の内訳も出力する（別PDF、社外秘）", key="doc_internal")

    def document(issued_at, number=""):
        return build_document(
            analysis=analysis, condition=condition, quote=quote, masters=masters, company=company,
            recipient=Recipient(customer or "（宛先未入力）", person), part=part, issued_at=issued_at, number=number,
            subject=subject, delivery_place=delivery_place, free_remarks=free_remarks, items=items,
            flags=reading.get("flags"), unregistered_texts=st.session_state.get("pdf_unregistered"))

    preview = document(datetime.now())
    if preview.is_estimate:
        st.warning("未確定の条件があるため概算見積書として出力されます。\n\n"
                   + "\n".join(f"- {note}" for note in preview.pending))
    if not customer.strip():
        st.info("宛先の会社名を入力すると、見積書PDFを作成できます。")
    inputs = repr((condition.model_dump(), quote.final_price, customer, person, part, subject, delivery_place,
                   free_remarks, with_internal))
    if st.button("見積書PDFを作成", type="primary", disabled=not customer.strip()):
        issued_at = datetime.now().replace(microsecond=0)
        doc = document(issued_at)
        store = history_store()
        taken = [q.quote_no for q in search_index().quotes]
        doc.number = QuoteLog(os.environ.get("QUOTE_LOG_PATH", "output/quote_log.csv")).issue(issued_at, log_row(doc), taken)
        files = {f"{doc.number}_{doc.title}.pdf": render_quote(doc)}
        if with_internal:
            files[f"{doc.number}_見積根拠（社内用）.pdf"] = render_internal(doc)
        store.append([from_document(doc, masters)])
        st.session_state.doc_files = (inputs, files)
    made_for, files = st.session_state.get("doc_files") or (None, {})
    if files and made_for != inputs:
        st.caption("条件か入力が変わったため、作成済みの見積書は表示していません。もう一度作成してください。")
        files = {}
    for name, data in files.items():
        st.download_button(f"ダウンロード: {name}", data=data, file_name=name, mime="application/pdf", key=f"dl_{name}")
    if files:
        st.caption("この見積は見積履歴（data/past_quotes/history.csv）に入り、次からの類似見積の検索対象になります。")


def outcome_section() -> None:
    """受注・失注の記録: the outcome of a quote in the history."""
    with st.expander("受注・失注の記録"):
        quotes = search_index().quotes
        if not quotes:
            st.caption("見積履歴がありません。")
            return
        mine = st.checkbox("この画面で出力した見積だけ", value=True, key="outcome_app_only")
        rows = [q for q in quotes if q.source == "app"] if mine else list(quotes)
        rows = sorted(rows, key=lambda q: (q.date, q.quote_no), reverse=True)[:200]
        if not rows:
            st.caption("この画面で出力した見積はまだありません。")
            return
        labels = {f"{q.quote_no}｜{q.date:%Y/%m/%d}｜{q.customer}｜{q.drawing_no or q.part_name}｜{q.outcome}": q for q in rows}
        chosen = labels[st.selectbox("見積", list(labels), key="outcome_quote")]
        outcome = st.radio("結果", list(OUTCOMES), index=list(OUTCOMES).index(chosen.outcome), horizontal=True,
                           key=f"outcome_{chosen.quote_no}")
        if st.button("結果を記録", key="outcome_save"):
            history_store().set_outcome(chosen.quote_no, outcome, chosen.customer)
            st.success(f"{chosen.quote_no} を「{outcome}」にしました。")
            st.rerun()


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
    if st.session_state.get("pdf_reading") is not None:
        condition = pdf_condition_editor(st.session_state.pdf_reading, condition, result)
    else:
        quote_col1, quote_col2, quote_col3, quote_col4 = st.columns([2, 1, 2, 1])
        selected_material = quote_col1.selectbox(
            "材料", masters.material_names,
            index=masters.material_names.index(condition.material),
        )
        selected_quantity = quote_col2.number_input(
            "数量", min_value=1, value=condition.quantity, step=1
        )
        finish_codes = list(masters.surface_treatments) or ["NONE"]
        selected_finish = quote_col3.selectbox(
            "表面処理", finish_codes, index=finish_codes.index(condition.surface_treatment or "NONE"),
            format_func=lambda c: masters.surface_treatments.get(c, {}).get("display_name", c),
        )
        selected_rush = quote_col4.checkbox("特急", value=condition.rush)
        selected_finish = None if selected_finish == "NONE" else selected_finish
        if (selected_material, selected_quantity, selected_finish, selected_rush) != (
                condition.material, condition.quantity, condition.surface_treatment, condition.rush):
            condition = condition.model_copy(update={
                "material": selected_material, "quantity": int(selected_quantity),
                "surface_treatment": selected_finish, "rush": bool(selected_rush),
            })
            st.session_state.quote_condition = condition

    try:
        quote = QuoteEngine(masters).calculate(result, condition)
        st.session_state.quote_result = quote
        if quote.is_estimate:
            st.warning("概算見積: 加工条件または解析値に概算を含みます。")
        summary = price_summary(quote, condition.quantity, masters.policy("tax_rate", 0.10))
        amount_cols = st.columns(4)
        amount_cols[0].metric("単価（1個）", f"¥{summary.unit_price:,}")
        amount_cols[1].metric(f"小計（税抜、{summary.quantity:,}個）", f"¥{summary.subtotal:,}")
        amount_cols[2].metric(f"消費税（{summary.tax_rate:.0%}）", f"¥{summary.tax:,}")
        amount_cols[3].metric("見積金額（税込）", f"¥{summary.total:,}")
        st.caption("原価の内訳（社内用。見積書には出ません）")
        st.dataframe(pd.DataFrame([
            {"コード": line.code, "項目": line.name, "数量": line.quantity,
             "単位": line.unit, "単価": line.unit_price, "金額": round(line.amount)}
            for line in quote.lines
        ]), hide_index=True, width="stretch")
    except QuoteUnavailableError as exc:
        st.error(str(exc))
    else:
        customer, person, part = case_inputs(result)
        similar_quotes_section(result, condition, quote, summary, customer, part)
        quote_document_section(result, condition, quote, customer, person, part)
        outcome_section()

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
