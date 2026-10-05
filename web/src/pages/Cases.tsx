import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { day, put, stamp, yen } from "../api";
import { ErrorBox, Field, Loading, Marked, Modal, StatusDot, Tabs, Thumb, useLoad, useMeta, useToast } from "../ui";

const WARN_LABEL: Record<string, string> = { overdue: "納期超過", due_soon: "納期まで3日以内", stale: "3日以上更新なし", waiting: "回答待ち3日以上" };

export default function CasesPage() {
  const { meta } = useMeta();
  const toast = useToast();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [staff, setStaff] = useState("");
  const { data, error, reload } = useLoad<any>(`/api/cases?q=${encodeURIComponent(q)}&staff=${encodeURIComponent(staff)}`);
  const [view, setView] = useState<"kanban" | "list">("kanban");
  const [group, setGroup] = useState("見積・受注");
  const [warn, setWarn] = useState("");
  const [lost, setLost] = useState<any>(null);
  const [moveError, setMoveError] = useState<any>(null);
  const [overCol, setOverCol] = useState<number | null>(null);

  if (!data) return error ? <ErrorBox error={error} /> : <Loading />;
  const statuses = data.statuses.filter((s: any) => s.visible);
  const cases = data.cases.filter((c: any) => !warn || c.warnings.some((w: any) => w.kind === warn));
  const inGroup = (g: string) => cases.filter((c: any) => c.status.group === g);
  const cols = statuses.filter((s: any) => s.group === group);

  async function move(c: any, status: any, extra: any = {}) {
    if (status.id === c.status.id) return;
    if (status.role === "lost" && !extra.lost_reason) return setLost({ c, status, reason: "", price: "", note: "" });
    try {
      await put(`/api/cases/${c.id}/status`, { status_id: status.id, version: c.version, ...extra });
      setMoveError(null);
      toast(`${c.number} を「${status.name}」にしました`);
      reload();
    } catch (e) {
      setMoveError(e);
      reload();
    }
  }
  const step = (c: any, dir: number) => {
    const order = statuses;
    const i = order.findIndex((s: any) => s.id === c.status.id);
    const next = order[i + dir];
    if (next) move(c, next);
  };

  return (
    <div>
      <div className="row" style={{ marginBottom: 12 }}>
        <input aria-label="絞り込み" placeholder="案件番号・顧客・品名・図番で絞込" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 340 }} />
        <span className="small muted">担当者</span>
        <select aria-label="担当者" value={staff} onChange={(e) => setStaff(e.target.value)} style={{ width: 130 }}>
          <option value="">すべて</option>
          {(meta?.staff || []).map((s) => <option key={s.id}>{s.name}</option>)}
        </select>
        <span className="spacer" />
        <Tabs value={view} onChange={setView} options={[{ value: "kanban", label: "カンバン" }, { value: "list", label: "一覧" }]} />
        <button className="btn" onClick={() => navigate("/settings/statuses")}>ステータス設定</button>
      </div>
      <div className="row" style={{ marginBottom: 14 }}>
        {Object.entries(WARN_LABEL).map(([k, label]) => (
          <button key={k} className={`btn small ${warn === k ? "primary" : ""}`} onClick={() => setWarn(warn === k ? "" : k)}>{label} {data.warning_counts[k]}件</button>
        ))}
        <span className="spacer" />
        <span className="small muted">判定基準日 {day(data.reference_date)} ・ {cases.length}件</span>
      </div>
      <ErrorBox error={moveError} />
      <div className="row between" style={{ borderBottom: "1px solid var(--line)", marginBottom: 14 }}>
        <div className="row" style={{ gap: 26 }}>
          {(meta?.groups || []).map((g) => {
            const list = inGroup(g);
            const alerts = list.filter((c: any) => c.warnings.length).length;
            return (
              <button key={g} onClick={() => setGroup(g)} className="btn link" style={{ fontFamily: "var(--head)", fontSize: 19, color: g === group ? "var(--accent-dark)" : "var(--muted)", borderBottom: g === group ? "2px solid var(--accent)" : "2px solid transparent", borderRadius: 0, padding: "4px 2px" }}>
                {g} {list.length} {alerts > 0 && <span className="tag solid" style={{ fontSize: 10 }}>要注意 {alerts}</span>}
              </button>
            );
          })}
        </div>
        <span className="small muted">ドラッグ、または ←→ で進捗を変更（元に戻せます）</span>
      </div>

      {view === "kanban" ? (
        <div className="kanban" data-testid="kanban">
          {cols.map((s: any) => {
            const items = cases.filter((c: any) => c.status.id === s.id);
            return (
              <div key={s.id} className={`col ${overCol === s.id ? "over" : ""}`} data-status={s.name}
                onDragOver={(e) => { e.preventDefault(); setOverCol(s.id); }} onDragLeave={() => setOverCol(null)}
                onDrop={(e) => { e.preventDefault(); setOverCol(null); const c = data.cases.find((x: any) => String(x.id) === e.dataTransfer.getData("text/plain")); if (c) move(c, s); }}>
                <header><i className={`dot ${s.color}`} />{s.name}<span className="count">{items.length}</span></header>
                <div className="items">
                  {items.map((c: any) => (
                    <Marked key={c.id} className="kcard" draggable onDragStart={(e: any) => e.dataTransfer.setData("text/plain", String(c.id))} data-testid={`case-${c.number}`}>
                      <div className="small muted">{c.number}</div>
                      <div className="row" style={{ alignItems: "flex-start", margin: "6px 0", flexWrap: "nowrap" }}>
                        <Thumb revisionId={c.revision_id} hasPdf={c.has_pdf} />
                        <div>
                          <div className="small">{c.customer}</div>
                          <b>{c.name || c.title}</b>
                          <div className="small muted">{c.drawing_no} {c.revision && `Rev.${c.revision}`}</div>
                        </div>
                      </div>
                      <div className="row" style={{ gap: 4 }}>{c.warnings.map((w: any) => <span key={w.kind} className={w.kind === "stale" ? "tag plain" : "tag"}>{w.label}</span>)}</div>
                      <div className="kv"><span className="muted">数量／金額</span><span>{c.quantity ?? "—"}個 ／ {c.subtotal ? yen(c.subtotal) : "未作成"}</span></div>
                      <div className="kv"><span className="muted">納期</span><span>{day(c.due_date)}</span></div>
                      <div className="kv"><span className="muted">担当</span><span>{c.staff || "—"}</span></div>
                      <div className="moves">
                        <button aria-label="前のステータスへ" onClick={() => step(c, -1)}>←</button>
                        <Link to={c.quote_id ? `/estimates/${c.quote_id}` : `/drawings/${c.drawing_id}`} className="small"><b>{c.quote_id ? "見積結果を開く" : "図面・案件を開く"}</b></Link>
                        <button aria-label="次のステータスへ" onClick={() => step(c, 1)}>→</button>
                      </div>
                      <div className="small muted">更新 {stamp(c.updated_at)}</div>
                    </Marked>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <table className="rule">
          <thead><tr><th>案件番号</th><th>品名・図番</th><th>顧客</th><th>状態</th><th className="right">数量／金額</th><th>納期</th><th>担当</th><th>注意</th><th>変更</th></tr></thead>
          <tbody>
            {inGroup(group).map((c: any) => (
              <tr key={c.id}>
                <td><Link to={c.quote_id ? `/estimates/${c.quote_id}` : "#"}>{c.number}</Link></td>
                <td>{c.name || c.title}<span className="sub">{c.drawing_no}</span></td>
                <td>{c.customer}</td>
                <td><StatusDot name={c.status.name} color={c.status.color} /></td>
                <td className="right">{c.quantity ?? "—"}個 ／ {c.subtotal ? yen(c.subtotal) : "未作成"}</td>
                <td>{day(c.due_date)}</td>
                <td>{c.staff || "—"}</td>
                <td>{c.warnings.map((w: any) => <span key={w.kind} className="tag" style={{ marginRight: 4 }}>{w.label}</span>)}</td>
                <td>
                  <select aria-label="ステータス" value={c.status.id} onChange={(e) => move(c, statuses.find((s: any) => s.id === Number(e.target.value)))}>
                    {statuses.map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {lost && (
        <Modal title="失注を記録" onClose={() => setLost(null)}>
          <p className="small muted">{lost.c.number} {lost.c.name}（{lost.c.customer}）。理由と他社価格は振り返り分析に使います。</p>
          <Field label="失注の理由（必須）">
            <select aria-label="失注の理由" value={lost.reason} onChange={(e) => setLost({ ...lost, reason: e.target.value })}>
              <option value="">選んでください</option>
              {(meta?.lost_reasons || []).map((r) => <option key={r}>{r}</option>)}
            </select>
          </Field>
          <Field label="他社価格（分かれば、税抜の合計）"><input aria-label="他社価格" type="number" value={lost.price} onChange={(e) => setLost({ ...lost, price: e.target.value })} /></Field>
          <Field label="メモ"><input value={lost.note} onChange={(e) => setLost({ ...lost, note: e.target.value })} /></Field>
          <div className="row">
            <button className="btn primary" disabled={!lost.reason} onClick={() => { move(lost.c, lost.status, { lost_reason: lost.reason, competitor_price: lost.price ? Number(lost.price) : null, lost_note: lost.note }); setLost(null); }}>失注にする</button>
            <button className="btn" onClick={() => setLost(null)}>やめる</button>
          </div>
        </Modal>
      )}
    </div>
  );
}
