from __future__ import annotations

from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from src.chat_service import ChatQuoteService
from src.llm_client import build_llm_client
from src.models import QuoteCondition
from src.past_quotes import OUTCOMES
from src.pdf_quote import CONFIRMED, MISSING, REVIEW, UNREG, ConditionItem, condition_items
from src.quote_document import PartInfo, Recipient, default_subject, load_company
from src.services.estimate import EstimateService, ServiceError
from src.services.schemas import ConditionInput, DrawingContext, DrawingItem
from src.services.settings import Settings

# Three pages in one script: the estimate (upload, analysis, conditions, amount), the detail of one similar quote,
# and the quotation (recipient, part, terms, PDF, outcome). The estimate page only estimates.
ESTIMATE, SIMILAR, DOCUMENT = "estimate", "similar", "document"
# widget values that must survive a visit to another page (Streamlit forgets the state of widgets it does not draw)
ESTIMATE_KEYS = ("pdf_material", "pdf_thickness_mm", "pdf_quantity", "pdf_surface_treatment", "pdf_rush")
DOCUMENT_KEYS = ("doc_customer", "doc_person", "doc_part", "doc_dwg", "doc_rev", "doc_subject", "doc_place",
                 "doc_remarks", "doc_internal")

load_dotenv()
st.set_page_config(page_title="STEP・DXF板金解析・見積デモ", page_icon="◫", layout="wide")

service = EstimateService(Settings.from_env())  # the same processing layer as the API (api/main.py)
masters = service.masters
try:
    llm = build_llm_client()
    llm_error = None
except Exception as exc:
    llm = None
    llm_error = str(exc)

page = st.session_state.get("page", ESTIMATE)
for key in (DOCUMENT_KEYS if page == ESTIMATE else ESTIMATE_KEYS if page == DOCUMENT else ESTIMATE_KEYS + DOCUMENT_KEYS):
    if key in st.session_state:
        st.session_state[key] = st.session_state[key]

BADGE = {CONFIRMED: "✅ 読み取り済み", REVIEW: "⚠️ 要確認", UNREG: "❌ 未登録", MISSING: "➖ 記載なし"}


# ================================================================== navigation
def keep_processes(registered: list[dict] | None = None) -> None:
    """Draw the process table again from its current rows (or `registered` and the current unregistered rows):
    after a visit to another page, or when the chat changed the processes."""
    item = next((i for i in st.session_state.get("pdf_items") or [] if i.field == "processes"), None)
    if item is None:
        return
    rows = item.value or []
    st.session_state.pdf_processes_rows = [p for p in rows if p.get("code") != "UNREGISTERED"] \
        if registered is None else registered
    st.session_state.pdf_unregistered_rows = [p.get("text") for p in rows if p.get("code") == "UNREGISTERED"]
    st.session_state.pdf_processes_version = st.session_state.get("pdf_processes_version", 0) + 1


def open_similar(match) -> None:
    keep_processes()
    st.session_state.similar_match = match
    st.session_state.page = SIMILAR


def open_document(condition: QuoteCondition, drawing: DrawingContext | None) -> None:
    keep_processes()
    st.session_state.doc_basis = (condition, drawing)
    st.session_state.page = DOCUMENT


def back_to_estimate() -> None:
    st.session_state.page = ESTIMATE


# ================================================================== estimate page: drawing conditions and chat
def pdf_condition_editor(reading: dict, analysis) -> QuoteCondition | None:
    """Conditions read from the drawing PDF, as editable inputs. The values in the inputs are quoted; the status
    tells what to check against the drawing. None while the material is not chosen."""
    st.markdown(f"**図面から読み取った加工条件**（{st.session_state.get('pdf_name', '図面PDF')}"
                f"{'、図番 ' + reading['drawing_no'] if reading.get('drawing_no') else ''}"
                f"{'、改訂 ' + reading['revision'] if reading.get('revision') else ''}）")
    items = condition_items(reading, masters)
    counts = {status: sum(item.status == status for item in items) for status in BADGE}
    st.caption("　".join(f"{BADGE[s]} {n}件" for s, n in counts.items() if n)
               + "　— 入力欄の値で見積を計算します。⚠️・❌・➖ の項目は図面と見比べ、必要なら直してください。")
    if reading.get("flags"):
        labels = {"tolerance": "厳しい公差", "appearance": "外観指定", "inspection": "検査・証明書"}
        st.warning("特記事項: " + "、".join(labels.get(f, f) for f in reading["flags"]) + "（見積には含めません）")
    final: list[ConditionItem] = []
    material_codes, finish_codes = list(masters.materials), list(masters.surface_treatments)
    apply_chat_update()
    unregistered_rows = unregistered_process_rows(items)
    for item in items:
        cols = st.columns([1.1, 2.6, 1.1, 3.8])
        cols[0].markdown(f"**{item.label}**")
        key = f"pdf_{item.field}"
        restored = key in st.session_state  # a value kept from before: no default (it would override it)
        with cols[1]:
            if item.field == "material":
                # a material that is not in the master (or not on the drawing) is chosen by the user: never price
                # with whichever material happens to be first in the list
                index = material_codes.index(item.value) if item.value in material_codes else None
                value = st.selectbox(item.label, material_codes, key=key, placeholder="材質を選んでください",
                                     format_func=lambda c: masters.materials[c]["display_name"], label_visibility="collapsed",
                                     **({} if restored else {"index": index}))
            elif item.field == "surface_treatment":
                default = item.value if item.value in finish_codes else "NONE"
                value = st.selectbox(item.label, finish_codes, key=key, label_visibility="collapsed",
                                     format_func=lambda c: masters.surface_treatments[c]["display_name"],
                                     **({} if restored else {"index": finish_codes.index(default)}))
            elif item.field == "thickness_mm":
                default = float(item.value or analysis.thickness_mm or 0.0)
                value = st.number_input(item.label, min_value=0.0, step=0.1, format="%g", key=key,
                                        label_visibility="collapsed", **({} if restored else {"value": default}))
            elif item.field == "quantity":
                value = int(st.number_input(item.label, min_value=1, step=1, key=key, label_visibility="collapsed",
                                            **({} if restored else {"value": int(item.value or 1)})))
            elif item.field == "rush":
                value = st.checkbox("特急", key=key, **({} if restored else {"value": bool(item.value)}))
            else:
                # the unregistered processes of the drawing are rows too ("未登録: <drawing text>"): they are quoted
                # separately (a remark on the quotation) unless the user deletes the row
                source = st.session_state.get("pdf_processes_rows", item.value or [])
                rows = [{"加工": p["code"], "個数/個": int(p.get("count_per_part") or 0)} for p in source
                        if p.get("code") in masters.process_rates] + [{"加工": label, "個数/個": None}
                                                                       for label in unregistered_rows]
                table = st.data_editor(
                    pd.DataFrame(rows, columns=["加工", "個数/個"]), num_rows="dynamic", hide_index=True,
                    key=f"{key}_{st.session_state.get('pdf_processes_version', 0)}",
                    column_config={"加工": st.column_config.SelectboxColumn(
                        options=masters.llm_process_codes + list(unregistered_rows))})
                value = []
                for r in table.to_dict("records"):
                    code, count = r.get("加工"), r.get("個数/個")
                    if code in unregistered_rows:
                        value.append({"code": "UNREGISTERED", "count_per_part": None, "text": unregistered_rows[code]})
                    elif code and count == count and count:  # count == count: not NaN
                        value.append({"code": code, "count_per_part": int(count)})
        cols[2].markdown(BADGE[item.status])
        cols[3].caption(item.display if not item.reasons else f"{item.display} ／ " + "、".join(item.reasons))
        final.append(ConditionItem(item.field, item.label, value, item.display, CONFIRMED, item.reasons))
    st.session_state.pdf_items = final
    material_input = next(i.value for i in final if i.field == "material")
    if material_input is None:
        return None
    return service.build_condition(analysis, ConditionInput(material=material_input), drawing_context())


def unregistered_process_rows(items: list[ConditionItem]) -> dict[str, str]:
    """Row label -> drawing text of each unregistered process read from the drawing."""
    item = next(i for i in items if i.field == "processes")
    if "pdf_unregistered_rows" in st.session_state:  # the rows the user kept
        return {f"未登録: {text}": text for text in st.session_state.pdf_unregistered_rows}
    count = sum(1 for p in item.value or [] if p.get("code") == "UNREGISTERED")
    texts = list(st.session_state.get("pdf_unregistered") or [])
    texts = (texts + [f"加工{n + 1}" for n in range(len(texts), count)])[:max(count, 0)]
    return {f"未登録: {text}": text for text in texts}


def chat_changes(before: QuoteCondition, after: QuoteCondition) -> dict:
    """What the chat changed, to carry into the drawing conditions."""
    changes = {}
    if after.material != before.material:
        changes["material"] = after.material
    if after.quantity != before.quantity:
        changes["quantity"] = after.quantity
    procs = lambda c: sorted((p.process_code, p.quantity) for p in c.additional_processes)  # noqa: E731
    if procs(after) != procs(before):
        changes["processes"] = [{"code": p.process_code, "count_per_part": int(p.quantity)}
                                for p in after.additional_processes if p.process_code in masters.process_rates]
    return changes


def apply_chat_update() -> None:
    """Put the chat's change into the inputs before they are drawn (a widget's value cannot change afterwards)."""
    changes = st.session_state.pop("pdf_chat_update", None) or {}
    if "material" in changes:
        st.session_state["pdf_material"] = changes["material"]
    if "quantity" in changes:
        st.session_state["pdf_quantity"] = changes["quantity"]
    if "processes" in changes:
        # the drawing's unregistered processes stay (rows of their own); the table is drawn again from the chat's list
        keep_processes(changes["processes"])


def drawing_context() -> DrawingContext | None:
    """The drawing conditions as they are on the screen (None without a drawing PDF)."""
    reading = st.session_state.get("pdf_reading")
    if reading is None:
        return None
    items = [DrawingItem(field=i.field, label=i.label, value=i.value, display=i.display, status=i.status,
                         reasons=list(i.reasons)) for i in st.session_state.get("pdf_items") or []]
    return DrawingContext(file_name=st.session_state.get("pdf_name", ""), drawing_no=reading.get("drawing_no"),
                          revision=reading.get("revision"), items=items, flags=list(reading.get("flags") or []),
                          unregistered_texts=[p["text"] for i in items if i.field == "processes" for p in i.value or []
                                              if p.get("code") == "UNREGISTERED" and p.get("text")]
                          or st.session_state.get("pdf_unregistered") or [])


def chat_box(condition: QuoteCondition) -> None:
    """Change the conditions in words, right under them (the amounts are recomputed by the rules)."""
    history = st.session_state.get("chat_history", [])
    if history:
        st.caption(f"チャット：{history[-2]['content']} → {history[-1]['content']}")
    with st.container():
        prompt = st.chat_input("チャットで条件を変更（例: 数量を10個にして、皿もみを2箇所追加）")
    if not prompt:
        return
    try:
        applied = ChatQuoteService(llm, masters).interpret_and_apply(
            prompt, condition, chat_history=history,
            pending_confirmation=st.session_state.get("pending_confirmation"),
        )
    except Exception as exc:
        st.error(f"チャットAPIエラー: {exc}。現在の見積は変更していません。")
        return
    st.session_state.quote_condition = applied.condition
    if st.session_state.get("pdf_reading") is not None:
        st.session_state.pdf_chat_update = chat_changes(condition, applied.condition)
    st.session_state.chat_history = (history + [
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": applied.message},
    ])[-8:]
    st.session_state.pending_confirmation = (
        applied.message if applied.interpretation.status == "needs_confirmation" else None
    )
    st.rerun()


def _yen(value) -> str:
    return "-" if value is None else f"¥{value:,.0f}"


def similar_quotes_section(analysis, outcome) -> None:
    """類似見積（参考）: one button per past quote; its detail opens on its own page. The quote is not changed."""
    st.divider()
    st.subheader("類似見積（参考）")
    summary = outcome.summary
    reading = st.session_state.get("pdf_reading") or {}
    # the recipient is entered on the quotation page; once it is, repeats and the same customer are found too
    customer = st.session_state.get("doc_customer") or ""
    drawing_no = st.session_state.get("doc_dwg") or reading.get("drawing_no") or ""
    revision = st.session_state.get("doc_rev") or reading.get("revision") or ""
    matches, reference, size = service.similar(analysis, outcome, customer, drawing_no, revision, today=date.today())
    if not size:
        st.info("見積履歴がありません（data/past_quotes/history.csv）。")
        return
    if not matches:
        st.info("材質の系統と板厚が近い過去の見積はありません。")
        return
    note = f"履歴 {size:,}件から、値段を決める要素が近いものを最大5件。参考表示のみで、今回の単価は変えません。"
    if not customer:
        note += "見積書の画面で宛先を入れると、同じ顧客・リピートの見積も探します。"
    if reference:
        gap = (summary.unit_price - reference.unit) / reference.unit
        note += f"過去の出し値の水準で見た今回の単価 {_yen(reference.unit)}（今回はこれより {gap:+.1%}）。"
    st.caption(note)
    for n, m in enumerate(matches, start=1):
        q = m.quote
        diff = f"（今回比 {m.price_diff:+.1%}）" if m.price_diff is not None else ""
        label = (f"{n}. 【{m.category}】 {q.date:%Y/%m/%d}　{q.customer}　{q.drawing_no or '図番なし'}　{q.part_name}"
                 f"　単価 {_yen(q.unit_price)}{diff}")
        st.button(label, key=f"similar_{n}", on_click=open_similar, args=(m,), width="stretch")


# ================================================================== similar-quote page
def similar_page() -> None:
    st.button("← 見積に戻る", on_click=back_to_estimate)
    m = st.session_state.get("similar_match")
    if m is None:
        st.info("類似見積が選ばれていません。")
        return
    q = m.quote
    drawing = " ".join(x for x in (q.drawing_no, f"Rev.{q.revision}" if q.revision else "") if x) or "図番なし"
    st.subheader(f"類似見積【{m.category}】 {q.quote_no}")
    st.markdown(f"{q.date:%Y/%m/%d}　**{q.customer}**　{drawing}　{q.part_name}")
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
    st.markdown("**この見積の全項目**")
    st.dataframe(pd.DataFrame([{"項目": k, "値": str(v)} for k, v in (q.original or {}).items()]
                              + [{"項目": "（取り込み元）", "値": q.source}]), hide_index=True, width="stretch")


# ================================================================== quotation page
def document_page() -> None:
    """見積書の作成: recipient, part and terms, then the PDF (and the internal basis). Issuing settles the
    conditions: the quotation is never an estimate. No LLM."""
    st.button("← 見積に戻る", on_click=back_to_estimate)
    analysis = st.session_state.get("analysis_result")
    basis = st.session_state.get("doc_basis")
    if analysis is None or basis is None:
        st.info("先に見積の画面で解析と条件の入力をしてください。")
        return
    condition, drawing = basis
    outcome = service.price(analysis, condition, drawing)
    summary = outcome.summary
    st.subheader("見積書の作成")
    cols = st.columns(4)
    cols[0].metric("単価（1個）", f"¥{summary.unit_price:,}")
    cols[1].metric(f"小計（税抜、{summary.quantity:,}個）", f"¥{summary.subtotal:,}")
    cols[2].metric(f"消費税（{summary.tax_rate:.0%}）", f"¥{summary.tax:,}")
    cols[3].metric("見積金額（税込）", f"¥{summary.total:,}")

    st.markdown("**宛先と部品**")
    reading = st.session_state.get("pdf_reading") or {}
    col1, col2 = st.columns(2)
    customer = col1.text_input("宛先の会社名（必須）", key="doc_customer", placeholder="サンプル電機株式会社")
    person = col2.text_input("部署・担当者名（任意）", key="doc_person", placeholder="購買部　山田 太郎")
    col3, col4, col5 = st.columns([2, 1.4, 0.6])
    defaults = {"doc_part": Path(analysis.file_name).stem, "doc_dwg": reading.get("drawing_no") or "",
                "doc_rev": reading.get("revision") or ""}
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
    part = PartInfo(name=col3.text_input("品名", key="doc_part"), drawing_no=col4.text_input("図番", key="doc_dwg"),
                    revision=col5.text_input("改訂", key="doc_rev"), shape_file=analysis.file_name,
                    drawing_file=st.session_state.get("pdf_name", "") if reading else "")

    st.markdown("**取引条件と備考**")
    company = load_company(service.settings.data_dir)
    st.session_state.setdefault("doc_place", company.delivery_place)
    col6, col7 = st.columns([2, 1])
    subject = col6.text_input("件名", key="doc_subject", placeholder=default_subject(part)) or default_subject(part)
    delivery_place = col7.text_input("受渡場所", key="doc_place")
    free_remarks = st.text_area("備考（任意、1行に1項目）", key="doc_remarks", height=80)
    with_internal = st.checkbox("社内用の内訳も出力する（別PDF、社外秘）", key="doc_internal")
    if outcome.reasons:
        st.info("見積書は、画面の条件を確定として出力します。次の点を確認してください。\n\n"
                + "\n".join(f"- {note}" for note in outcome.reasons))
    if not customer.strip():
        st.info("宛先の会社名を入力すると、見積書PDFを作成できます。")
    inputs = repr((condition.model_dump(), outcome.quote.final_price, customer, person, part, subject, delivery_place,
                   free_remarks, with_internal))
    if st.button("見積書PDFを作成", type="primary", disabled=not customer.strip()):
        issued = service.issue_document(analysis, condition, drawing, Recipient(customer, person), part,
                                        subject=subject, delivery_place=delivery_place, remarks=free_remarks,
                                        include_internal=with_internal)
        st.session_state.doc_files = (inputs, dict(issued.files.values()))
    made_for, files = st.session_state.get("doc_files") or (None, {})
    if files and made_for != inputs:
        st.caption("条件か入力が変わったため、作成済みの見積書は表示していません。もう一度作成してください。")
        files = {}
    for name, data in files.items():
        st.download_button(f"ダウンロード: {name}", data=data, file_name=name, mime="application/pdf", key=f"dl_{name}")
    if files:
        st.caption("この見積は見積履歴（data/past_quotes/history.csv）に入り、次からの類似見積の検索対象になります。")
    outcome_section()


def outcome_section() -> None:
    """受注・失注の記録: the outcome of a quote in the history."""
    with st.expander("受注・失注の記録"):
        quotes = service.search_index().quotes
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
            service.set_outcome(chosen.quote_no, outcome, chosen.customer)
            st.success(f"{chosen.quote_no} を「{outcome}」にしました。")
            st.rerun()


# ================================================================== pages
st.title("STEP・DXF板金解析・見積デモ")
if page == SIMILAR:
    similar_page()
    st.stop()
if page == DOCUMENT:
    document_page()
    st.stop()

st.caption("STEP形状または展開図DXFをローカル解析し、図面PDFの加工条件と合わせて、マスター単価でルールベース見積を作成します。")
upload_col, setting_col = st.columns([2, 1])
with upload_col:
    uploaded = st.file_uploader("STEP／展開図DXFファイル", type=["step", "stp", "dxf"])
    pdf_file = st.file_uploader("図面PDF（任意：材質・数量・表面処理・追加加工・特急を読み取ります）", type=["pdf"])
is_dxf = uploaded is not None and uploaded.name.lower().endswith(".dxf")
with setting_col:
    if is_dxf:
        thickness = st.number_input("板厚（mm）", min_value=0.0, value=0.0, step=0.1, format="%g",
                                    help="展開図DXFには板厚が含まれないため入力してください。")
        flat_confirmed = st.checkbox("曲げなし（平板）", value=False,
                                     help="曲げ線のない展開図は、曲げ線の描き漏れと区別できないため確認が必要になります。"
                                          "曲げのない平板であることを確認したらチェックしてください。")
    else:
        k_factor = st.number_input("Kファクター", min_value=0.0, max_value=1.0, value=0.33, step=0.01)
        confirmed_k = st.checkbox("指定済み加工条件として扱う", value=False)

analyze_clicked = st.button("解析を実行", type="primary",
                            disabled=uploaded is None or (is_dxf and not thickness > 0 and pdf_file is None))
if is_dxf and not thickness > 0:
    st.info("展開図DXFの解析には板厚（mm）の入力が必要です（図面PDFを指定した場合は図面の板厚を使います）。")
if analyze_clicked and uploaded is not None:
    data = uploaded.getvalue()
    for key in ("pdf_reading", "pdf_items", "pdf_unregistered", "doc_files", "doc_basis", "pdf_chat_update",
                "pdf_processes_rows", "pdf_unregistered_rows", "doc_part", "doc_dwg", "doc_rev", "doc_subject") + ESTIMATE_KEYS:
        st.session_state.pop(key, None)
    if pdf_file is not None:
        with st.spinner("図面PDFから加工条件を読み取っています…（Claude API）"):
            handle = None
            try:
                with NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
                    handle.write(pdf_file.getvalue())
                reading, drawing = service.read_drawing(handle.name, pdf_file.name)
                st.session_state.pdf_unregistered = drawing.unregistered_texts
                st.session_state.pdf_reading = reading
                st.session_state.pdf_name = pdf_file.name
            except ServiceError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"図面PDFを読み取れませんでした: {exc}。加工条件は手入力してください。")
            finally:
                if handle is not None:
                    Path(handle.name).unlink(missing_ok=True)
    if is_dxf and not thickness > 0:
        thickness = float((st.session_state.get("pdf_reading") or {}).get("thickness_mm") or 0)
    if is_dxf:
        with st.spinner("展開図DXFを解析しています…"):
            st.session_state.analysis_result = service.analyze_bytes(data, uploaded.name, thickness_mm=thickness,
                                                                     flat_confirmed=flat_confirmed)
            st.session_state.step_bytes = None
    else:
        with st.spinner("STEP形状を解析しています…"):
            st.session_state.analysis_result = service.analyze_bytes(
                data, uploaded.name, k_factor=k_factor, k_factor_confirmed=confirmed_k)
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
    if st.session_state.get("pdf_reading") is not None:
        condition = pdf_condition_editor(st.session_state.pdf_reading, result)
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

    if llm is not None and condition is not None:
        chat_box(condition)
    elif llm is None:
        st.error(f"チャット設定エラー: {llm_error}。ルールベース見積はそのまま利用できます。")

    try:
        if condition is None:
            raise ServiceError("図面の材質がマスターにない（または記載がない）ため、材質を選ぶと金額を出します。")
        outcome = service.price(result, condition, drawing_context())  # the same call as POST /api/quotes
        quote, summary = outcome.quote, outcome.summary
        st.session_state.quote_result = quote
        if outcome.reasons:
            st.warning("確認してください（見積書は、画面の条件を確定として出力します）。\n\n"
                       + "\n".join(f"- {note}" for note in outcome.reasons))
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
    except ServiceError as exc:
        st.error(str(exc))
    else:
        st.button("見積書を作成する →", type="primary", on_click=open_document, args=(condition, drawing_context()))
        similar_quotes_section(result, outcome)

with st.expander("解析結果 JSON"):
    st.json(result.model_dump(mode="json"))
