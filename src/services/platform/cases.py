"""Cases (案件) and their progress: the list / kanban, the warnings, the phases, the next action of each case and
the To Do list of the home screen, moving a case to another status (受注 / 失注 with the reason and the
competitor's price), the status settings, and the review figures (実績分析).

Every judgment the screens show (which phase a case is in, what needs attention, what to do next) is made here.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from src.db.models import (Case, CaseStatus, CaseStatusLog, Drawing, DrawingRevision, IssuedDocument, PastQuoteRow,
                           Quote, now)
from src.services.estimate import ServiceError
from src.services.platform.core import Platform, bump, check_version, iso

# The phases of a case (each status belongs to one; the user can rename and add statuses in the settings).
PHASES = [{"key": "drafting", "number": 1, "label": "見積作成中"}, {"key": "checking", "number": 2, "label": "見積確認中"},
          {"key": "waiting", "number": 3, "label": "回答待ち"}, {"key": "production", "number": 4, "label": "製造・出荷"},
          {"key": "closed", "number": None, "label": "完了分"}]
PHASE_KEYS = [p["key"] for p in PHASES]
COLORS = {"light": "淡い青", "blue": "青", "dark": "濃い青", "grey": "グレー"}
LOST_REASONS = ["価格", "納期", "仕様・加工不可", "回答遅れ", "その他"]
CLOSED_ROLES = {"shipped", "done", "lost"}
STALE_DAYS, DUE_SOON_DAYS, WAITING_DAYS = 3, 3, 3


class StatusMove(BaseModel):
    status_id: int
    version: int
    lost_reason: str = ""
    competitor_price: int | None = Field(None, ge=0)
    lost_note: str = ""


class StatusRow(BaseModel):
    id: int | None = None
    name: str = Field(min_length=1)
    color: str = "blue"
    phase: str = "drafting"
    visible: bool = True
    role: str = ""


def today() -> date:
    return datetime.now().date()


def warnings_of(case: Case, ref: date | None = None) -> list[dict]:
    """納期超過 / 納期まで3日以内 / 3日以上更新なし / 回答待ち3日以上 (none for closed cases)."""
    ref = ref or today()
    role = case.status.role if case.status else ""
    if role in CLOSED_ROLES:
        return []
    out = []
    if case.due_date:
        left = (case.due_date - ref).days
        if left < 0:
            out.append({"kind": "overdue", "label": f"納期超過 {-left}日"})
        elif left <= DUE_SOON_DAYS:
            out.append({"kind": "due_soon", "label": f"納期まで {left}日"})
    if role == "issued":
        waiting = (ref - case.status_changed_at.date()).days
        if waiting >= WAITING_DAYS:
            out.append({"kind": "waiting", "label": f"回答待ち {waiting}日"})
    stale = (ref - case.updated_at.date()).days
    if stale >= STALE_DAYS and role not in ("hold",):
        out.append({"kind": "stale", "label": f"{stale}日更新なし"})
    return out


TODO_RANK = {"overdue": 0, "missing": 1, "due_soon": 2, "waiting": 3}


def status_brief(st: CaseStatus) -> dict:
    return {"id": st.id, "name": st.name, "color": st.color, "phase": st.phase, "role": st.role}


def next_action(case: Case, quote: Quote | None, drawing: Drawing | None, missing: int, pending: bool,
                kinds: set[str]) -> dict:
    """What to do next with a case: {label, kind, quote_id, drawing_id, doc_kind}. The screens turn `kind` into
    the screen to open (estimate / input / outcome / documents / new_estimate / progress)."""
    base = {"quote_id": quote.id if quote else None, "drawing_id": drawing.id if drawing else None, "doc_kind": ""}
    role, phase = case.status.role, case.status.phase
    if quote is None:
        return {**base, "kind": "new_estimate", "label": "新規見積"}
    if pending:
        return {**base, "kind": "progress", "label": "解析の進捗を表示"}
    if phase in ("drafting", "checking"):
        if missing:
            return {**base, "kind": "input", "label": "入力"}
        if "quote" in kinds:
            return {**base, "kind": "estimate", "label": "見積を表示"}
        return {**base, "kind": "issue", "label": "確認・発行" if phase == "checking" else "見積を表示"}
    if phase == "waiting":
        return {**base, "kind": "outcome", "label": "受注・失注の登録"}
    if phase == "production":
        for kind, label in (("delivery", "納品書の発行"), ("invoice", "請求書の発行")):
            if kind not in kinds:
                return {**base, "kind": "documents", "doc_kind": kind, "label": label}
        return {**base, "kind": "estimate", "label": "見積を表示"}
    if role == "lost" and drawing is not None:
        return {**base, "kind": "new_estimate", "label": "再見積"}
    return {**base, "kind": "estimate", "label": "見積を表示"}


def todo_of(case: Case, warnings: list[dict], missing: int) -> list[dict]:
    """The reasons a case is on the To Do list (納期超過・未入力あり・納期まで○日・回答待ち○日), most urgent first.
    The same warnings as the progress board (warnings_of); 「更新なし」 alone does not put a case on the list."""
    out = [w for w in warnings if w["kind"] in TODO_RANK]
    if missing and case.status.phase in ("drafting", "checking"):
        out.append({"kind": "missing", "label": "未入力あり"})
    return sorted(out, key=lambda w: TODO_RANK[w["kind"]])


def todo_note(case: Case, missing_first: str, phase_label: str) -> str:
    """One line under a To Do row: why it is there, in the estimator's words."""
    role, phase = case.status.role, case.status.phase
    if missing_first and phase in ("drafting", "checking"):
        return f"{missing_first.removesuffix('です')}（入力後に発行可能）"
    if phase == "checking":
        return f"{case.status.name}（確認後に見積書を発行）"
    if role == "issued" or phase == "waiting":
        return f"{case.status.name}（受注・失注の登録待ち）"
    if phase == "drafting":
        return f"ステータス：{case.status.name}"
    return f"{case.status.name}（{phase_label}）"


def case_out(case: Case, quote: Quote | None, drawing: Drawing | None, revision: DrawingRevision | None,
             kinds: set[str] | None = None, pending: bool = False) -> dict:
    price = ((quote.result or {}).get("price") if quote else None) or None
    missing = list((quote.result or {}).get("missing") or []) if quote else []
    warnings = warnings_of(case)
    todo = todo_of(case, warnings, len(missing))
    phase = next((p for p in PHASES if p["key"] == case.status.phase), PHASES[0])
    return {"id": case.id, "number": case.number, "customer": case.customer, "title": case.title,
            "status": status_brief(case.status), "phase": phase["key"],
            "staff": case.staff, "due_date": iso(case.due_date), "outcome": case.outcome, "version": case.version,
            "updated_at": iso(case.updated_at), "created_at": iso(case.created_at),
            "quote_id": quote.id if quote else None, "quantity": (quote.inputs or {}).get("quantity") if quote else None,
            "subtotal": price["subtotal"] if price else None, "unit_price": price["unit_price"] if price else None,
            "missing": len(missing), "missing_first": missing[0]["message"] if missing else "",
            "missing_short": (f"未入力：{missing[0]['label']}" + (f" ほか{len(missing) - 1}件" if len(missing) > 1 else "")) if missing else "",
            "drawing_id": drawing.id if drawing else None, "drawing_no": drawing.drawing_no if drawing else "",
            "name": drawing.name if drawing else "", "revision": revision.revision if revision else "",
            "revision_id": revision.id if revision else None, "has_pdf": bool(revision and revision.pdf_file_id),
            "lost_reason": case.lost_reason, "competitor_price": case.competitor_price,
            "warnings": warnings, "todo": todo,
            "todo_note": todo_note(case, missing[0]["message"] if missing else "", phase["label"]),
            "documents": sorted(kinds or []),
            "next_action": next_action(case, quote, drawing, len(missing), pending, kinds or set())}


def priority(row: dict) -> tuple:
    """To Do cases first (most urgent reason; then the longest overdue / the nearest due date), then the rest."""
    if not row["todo"]:
        return (1, 0, 0)
    first = row["todo"][0]
    days = int("".join(ch for ch in first["label"] if ch.isdigit()) or 0)
    return (0, TODO_RANK[first["kind"]], -days if first["kind"] in ("overdue", "waiting") else days)


def _rows(pf: Platform, s) -> list[dict]:
    cases = list(s.scalars(select(Case).options(selectinload(Case.status), selectinload(Case.quotes))
                           .order_by(Case.updated_at.desc(), Case.id.desc())))
    quotes = {c.id: (c.quotes[-1] if c.quotes else None) for c in cases}
    drawing_ids = [qq.drawing_id for qq in quotes.values() if qq and qq.drawing_id]
    drawings = {d.id: d for d in s.scalars(select(Drawing).where(Drawing.id.in_(drawing_ids)))} if drawing_ids else {}
    rev_ids = [qq.revision_id for qq in quotes.values() if qq and qq.revision_id]
    revisions = {r.id: r for r in s.scalars(select(DrawingRevision).where(DrawingRevision.id.in_(rev_ids)))} if rev_ids else {}
    kinds: dict[int, set[str]] = {}
    for case_id, kind in s.execute(select(IssuedDocument.case_id, IssuedDocument.kind)).all():
        kinds.setdefault(case_id, set()).add(kind)
    rows = []
    for c in cases:
        qq = quotes[c.id]
        # the estimate job writes the result at its end: only a quote without one can still be running
        job = pf.job(qq.job_id) if qq and qq.job_id and qq.result is None else None
        pending = bool(job and job["status"] in ("queued", "running"))
        rows.append(case_out(c, qq, drawings.get(qq.drawing_id) if qq else None,
                             revisions.get(qq.revision_id) if qq else None, kinds.get(c.id, set()), pending))
    return rows


def phase_summary(rows: list[dict]) -> list[dict]:
    """Per phase: the number of cases and what needs attention there (the home screen and the filter tabs)."""
    out = []
    for p in PHASES:
        items = [r for r in rows if r["phase"] == p["key"]]
        if p["key"] == "drafting":
            attention = {"label": "うち未入力あり", "count": sum(1 for r in items if r["missing"])}
        elif p["key"] == "checking":
            attention = {"label": "納期まで3日以内", "count": sum(1 for r in items if any(w["kind"] in ("due_soon", "overdue") for w in r["warnings"]))}
        elif p["key"] == "waiting":
            attention = {"label": "回答待ち3日以上", "count": sum(1 for r in items if any(w["kind"] == "waiting" for w in r["warnings"]))}
        elif p["key"] == "production":
            attention = {"label": "納品書・請求書を発行可能", "count": sum(1 for r in items if r["next_action"]["kind"] == "documents")}
        else:
            attention = {"label": "完了・失注・保留", "count": 0}
        out.append({**p, "count": len(items), "todo": sum(1 for r in items if r["todo"]), "attention": attention})
    return out


def list_cases(pf: Platform, q: str = "", staff: str = "", phase: str = "") -> dict:
    """The cases, the most urgent first, with the phase counts. `phase`: a phase key, "active" (all but 完了分) or ""."""
    with pf.session() as s:
        everything = _rows(pf, s)
        statuses = [status_out(st) for st in s.scalars(select(CaseStatus).order_by(CaseStatus.sort))]
    rows = [r for r in everything
            if (not q or any(q.upper() in str(r[k]).upper() for k in ("number", "customer", "name", "drawing_no", "title")))
            and (not staff or r["staff"] == staff)]
    phases = phase_summary(rows)
    if phase == "active":
        rows = [r for r in rows if r["phase"] != "closed"]
    elif phase:
        rows = [r for r in rows if r["phase"] == phase]
    rows.sort(key=priority)
    counts = {"overdue": 0, "due_soon": 0, "stale": 0, "waiting": 0, "missing": 0}
    for row in rows:
        for w in row["warnings"]:
            counts[w["kind"]] += 1
        if any(t["kind"] == "missing" for t in row["todo"]):
            counts["missing"] += 1
    return {"cases": rows, "statuses": statuses, "warning_counts": counts, "reference_date": iso(today()),
            "phases": phases, "active": sum(p["count"] for p in phases if p["key"] != "closed")}


def home(pf: Platform) -> dict:
    """The home screen: the To Do list (most urgent first), the phases with what needs attention, the counts of
    the menu."""
    with pf.session() as s:
        rows = _rows(pf, s)
        drawings = s.scalar(select(func.count()).select_from(Drawing)) or 0
    rows.sort(key=priority)
    phases = phase_summary(rows)
    return {"todo": [r for r in rows if r["todo"]], "phases": phases, "reference_date": iso(today()),
            "counts": {"active": sum(p["count"] for p in phases if p["key"] != "closed"), "drawings": drawings}}


def status_out(st: CaseStatus, count: int | None = None) -> dict:
    out = {"id": st.id, "name": st.name, "sort": st.sort, "color": st.color, "phase": st.phase,
           "visible": st.visible, "role": st.role, "version": st.version}
    if count is not None:
        out["count"] = count
    return out


def move(pf: Platform, case_id: int, body: StatusMove, actor: str) -> dict:
    with pf.session() as s:
        case = s.get(Case, case_id, options=[selectinload(Case.status), selectinload(Case.quotes)])
        if case is None:
            raise ServiceError("案件が見つかりません。", 404, "NOT_FOUND")
        check_version(case, body.version, "案件")
        status = s.get(CaseStatus, body.status_id)
        if status is None:
            raise ServiceError("ステータスが見つかりません。", 404, "NOT_FOUND")
        if status.role == "lost":
            if body.lost_reason not in LOST_REASONS:
                raise ServiceError(f"失注の理由を選択してください（{'・'.join(LOST_REASONS)}）。")
            case.lost_reason, case.competitor_price, case.lost_note = body.lost_reason, body.competitor_price, body.lost_note
        outcome = outcome_of(status)
        case.status, case.status_changed_at, case.updated_at, case.updated_by = status, now(), now(), actor
        case.outcome = outcome
        if outcome != "失注":
            case.lost_reason, case.competitor_price = "", None
        s.add(CaseStatusLog(case_id=case.id, status_name=status.name, actor=actor))
        rows = list(s.scalars(select(PastQuoteRow).where(PastQuoteRow.case_id == case.id)))
        for row in rows:  # the history (similar quotes, review) follows the case
            row.outcome, row.lost_reason = outcome, case.lost_reason
        if rows:
            bump(s, "history_rev")
        s.commit()
        quote = case.quotes[-1] if case.quotes else None
        drawing = s.get(Drawing, quote.drawing_id) if quote and quote.drawing_id else None
        revision = s.get(DrawingRevision, quote.revision_id) if quote and quote.revision_id else None
        kinds = set(s.scalars(select(IssuedDocument.kind).where(IssuedDocument.case_id == case.id)))
        return case_out(case, quote, drawing, revision, kinds)


def outcome_of(status: CaseStatus) -> str:
    if status.role == "lost":
        return "失注"
    if status.role in ("won", "shipped", "done") or status.phase == "production":
        return "受注"
    return "未回答"


def history(pf: Platform, case_id: int) -> list[dict]:
    with pf.session() as s:
        return [{"status_name": r.status_name, "at": iso(r.at), "actor": r.actor}
                for r in s.scalars(select(CaseStatusLog).where(CaseStatusLog.case_id == case_id).order_by(CaseStatusLog.id))]


# ---------------------------------------------------------------- status settings
def statuses(pf: Platform) -> list[dict]:
    with pf.session() as s:
        counts = dict(s.execute(select(Case.status_id, func.count()).group_by(Case.status_id)).all())
        return [status_out(st, counts.get(st.id, 0)) for st in s.scalars(select(CaseStatus).order_by(CaseStatus.sort))]


def save_statuses(pf: Platform, rows: list[StatusRow]) -> list[dict]:
    """Replace the list: order = the order given; rows without id are added; missing ones are deleted (only when
    no case uses them). Renaming keeps the history's old names (the log stores the name of the time)."""
    for row in rows:
        if row.phase not in PHASE_KEYS:
            raise ServiceError(f"フェーズは {'・'.join(p['label'] for p in PHASES)} のどれかです。")
        if row.color not in COLORS:
            raise ServiceError("色が正しくありません。")
    if len({r.name.strip() for r in rows}) != len(rows):
        raise ServiceError("同じ名前のステータスがあります。")
    with pf.session() as s:
        existing = {st.id: st for st in s.scalars(select(CaseStatus))}
        keep = {r.id for r in rows if r.id}
        used = dict(s.execute(select(Case.status_id, func.count()).group_by(Case.status_id)).all())
        for sid, st in existing.items():
            if sid not in keep:
                if used.get(sid):
                    raise ServiceError(f"ステータス「{st.name}」は案件 {used[sid]}件で使っているため削除できません。", 409, "IN_USE")
                if st.role in ("drafting", "issued", "won", "lost"):
                    raise ServiceError(f"ステータス「{st.name}」は見積の作成・発行・受注・失注に使うため削除できません。", 409, "IN_USE")
                s.delete(st)
        for i, row in enumerate(rows):
            st = existing.get(row.id) if row.id else None
            if st is None:
                st = CaseStatus(role="")
                s.add(st)
            st.name, st.color, st.phase, st.visible, st.sort = row.name.strip(), row.color, row.phase, row.visible, i
        s.commit()
    return statuses(pf)


# ---------------------------------------------------------------- review (実績分析)
def review(pf: Platform, months: int = 6, ref: date | None = None) -> dict:
    """Figures of the quotes in the last `months` months (the history: past quotes and the quotes issued here)."""
    ref = ref or today()
    start = ref - timedelta(days=round(months * 30.44))
    prev = start - timedelta(days=round(months * 30.44))
    with pf.session() as s:
        rows = list(s.scalars(select(PastQuoteRow).where(PastQuoteRow.date > prev, PastQuoteRow.date <= ref)))
    now_rows = [r for r in rows if r.date > start]
    old_rows = [r for r in rows if r.date <= start]

    def figures(items):
        decided = [r for r in items if r.outcome in ("受注", "失注")]
        won = [r for r in items if r.outcome == "受注"]
        days = [r.answer_days for r in items if r.answer_days is not None]
        return {"count": len(items), "won": len(won), "lost": len(decided) - len(won),
                "win_rate": len(won) / len(decided) if decided else None,
                "answer_days": sum(days) / len(days) if days else None,
                "total": sum(r.amount or 0 for r in items), "won_total": sum(r.amount or 0 for r in won)}

    def by(key):
        groups: dict[str, list] = {}
        for r in now_rows:
            groups.setdefault(getattr(r, key) or "（未設定）", []).append(r)
        out = [{"name": k, **figures(v)} for k, v in groups.items()]
        return sorted(out, key=lambda x: -x["count"])

    lost = [r for r in now_rows if r.outcome == "失注"]
    recorded = [r for r in lost if r.lost_reason]
    reasons: dict[str, int] = {}
    for r in recorded:
        reasons[r.lost_reason] = reasons.get(r.lost_reason, 0) + 1
    return {"months": months, "from": iso(start + timedelta(days=1)), "to": iso(ref), "current": figures(now_rows),
            "previous": figures(old_rows), "customers": by("customer")[:8], "staff": by("staff"),
            "lost_reasons": [{"reason": k, "count": v, "share": v / len(recorded)} for k, v in
                             sorted(reasons.items(), key=lambda kv: -kv[1])],
            "lost_unrecorded": len(lost) - len(recorded),
            "lost_count": len(lost)}
