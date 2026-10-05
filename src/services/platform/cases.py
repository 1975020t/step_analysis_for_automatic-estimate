"""Cases (案件) and their progress: the kanban / list, the warnings, moving a case to another status (受注 /
失注 with the reason and the competitor's price), the status settings, and the review figures (振り返り).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from src.db.models import Case, CaseStatus, CaseStatusLog, Drawing, DrawingRevision, PastQuoteRow, Quote, now
from src.services.estimate import ServiceError
from src.services.platform.core import Platform, bump, check_version, iso

GROUPS = ["見積・受注", "製造・出荷", "保留・失注"]
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
    group: str = "見積・受注"
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


def case_out(case: Case, quote: Quote | None, drawing: Drawing | None, revision: DrawingRevision | None) -> dict:
    price = ((quote.result or {}).get("price") if quote else None) or None
    return {"id": case.id, "number": case.number, "customer": case.customer, "title": case.title,
            "status": {"id": case.status.id, "name": case.status.name, "color": case.status.color,
                       "group": case.status.group, "role": case.status.role},
            "staff": case.staff, "due_date": iso(case.due_date), "outcome": case.outcome, "version": case.version,
            "updated_at": iso(case.updated_at), "created_at": iso(case.created_at),
            "quote_id": quote.id if quote else None, "quantity": (quote.inputs or {}).get("quantity") if quote else None,
            "subtotal": price["subtotal"] if price else None,
            "drawing_id": drawing.id if drawing else None, "drawing_no": drawing.drawing_no if drawing else "",
            "name": drawing.name if drawing else "", "revision": revision.revision if revision else "",
            "revision_id": revision.id if revision else None, "has_pdf": bool(revision and revision.pdf_file_id),
            "lost_reason": case.lost_reason, "competitor_price": case.competitor_price,
            "warnings": warnings_of(case)}


def list_cases(pf: Platform, q: str = "", staff: str = "") -> dict:
    with pf.session() as s:
        cases = list(s.scalars(select(Case).options(selectinload(Case.status), selectinload(Case.quotes))
                               .order_by(Case.updated_at.desc(), Case.id.desc())))
        quotes = {c.id: (c.quotes[-1] if c.quotes else None) for c in cases}
        drawing_ids = [qq.drawing_id for qq in quotes.values() if qq and qq.drawing_id]
        drawings = {d.id: d for d in s.scalars(select(Drawing).where(Drawing.id.in_(drawing_ids)))} if drawing_ids else {}
        rev_ids = [qq.revision_id for qq in quotes.values() if qq and qq.revision_id]
        revisions = {r.id: r for r in s.scalars(select(DrawingRevision).where(DrawingRevision.id.in_(rev_ids)))} if rev_ids else {}
        rows = []
        for c in cases:
            qq = quotes[c.id]
            row = case_out(c, qq, drawings.get(qq.drawing_id) if qq else None, revisions.get(qq.revision_id) if qq else None)
            if q and not any(q.upper() in str(row[k]).upper() for k in ("number", "customer", "name", "drawing_no", "title")):
                continue
            if staff and row["staff"] != staff:
                continue
            rows.append(row)
        statuses = [status_out(st) for st in s.scalars(select(CaseStatus).order_by(CaseStatus.sort))]
    counts = {"overdue": 0, "due_soon": 0, "stale": 0, "waiting": 0}
    for row in rows:
        for w in row["warnings"]:
            counts[w["kind"]] += 1
    return {"cases": rows, "statuses": statuses, "warning_counts": counts, "reference_date": iso(today())}


def status_out(st: CaseStatus, count: int | None = None) -> dict:
    out = {"id": st.id, "name": st.name, "sort": st.sort, "color": st.color, "group": st.group,
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
                raise ServiceError(f"失注の理由を選んでください（{'・'.join(LOST_REASONS)}）。")
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
        return case_out(case, quote, drawing, revision)


def outcome_of(status: CaseStatus) -> str:
    if status.role == "lost":
        return "失注"
    if status.role == "won" or status.group == "製造・出荷":
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
        if row.group not in GROUPS:
            raise ServiceError(f"表示グループは {'・'.join(GROUPS)} のどれかです。")
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
            st.name, st.color, st.group, st.visible, st.sort = row.name.strip(), row.color, row.group, row.visible, i
        s.commit()
    return statuses(pf)


# ---------------------------------------------------------------- review (振り返り)
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
    reasons: dict[str, int] = {}
    for r in lost:
        reasons[r.lost_reason or "記録なし"] = reasons.get(r.lost_reason or "記録なし", 0) + 1
    return {"months": months, "from": iso(start + timedelta(days=1)), "to": iso(ref), "current": figures(now_rows),
            "previous": figures(old_rows), "customers": by("customer")[:8], "staff": by("staff"),
            "lost_reasons": [{"reason": k, "count": v, "share": v / len(lost)} for k, v in
                             sorted(reasons.items(), key=lambda kv: -kv[1])],
            "lost_count": len(lost)}
