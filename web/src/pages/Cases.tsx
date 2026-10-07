import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { day, put, stamp, yen } from "../api";
import { actionHref, ErrorBox, Field, Loading, Modal, Reasons, StatusDot, Tabs, Thumb, useLoad, useMeta, useToast } from "../ui";

const ALERTS: [string, string][] = [["overdue", "納期超過"], ["due_soon", "納期まで3日以内"], ["missing", "未入力あり"], ["stale", "3日以上更新なし"], ["waiting", "回答待ち3日以上"]];

/** 見積・案件: the estimates and cases in one screen (list or kanban), filtered by phase. The phase of each case,
 * its warnings, its next action and the order (most urgent first) come from the API (/api/cases). */
export default function CasesPage() {
  const { meta } = useMeta();
  const toast = useToast();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const phase = params.get("phase") || "active";
  const view = params.get("view") === "kanban" ? "kanban" : "list";
  const setParam = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    setParams(next, { replace: true });
  };
  const [q, setQ] = useState(params.get("q") || "");
  const [staff, setStaff] = useState("");
  const { data, error, reload } = useLoad<any>(`/api/cases?${new URLSearchParams({ q, staff, phase })}`);
  const [alert, setAlert] = useState("");
  const [lost, setLost] = useState<any>(null);
  const [moveError, setMoveError] = useState<any>(null);
  const [overCol, setOverCol] = useState<number | null>(null);

  if (!data) return error ? <ErrorBox error={error} /> : <Loading />;
  const visible = data.statuses.filter((s: any) => s.visible);
  const cases = data.cases.filter((c: any) => !alert || c.warnings.some((w: any) => w.kind === alert) || (alert === "missing" && c.todo.some((t: any) => t.kind === "missing")));
  const cols = visible.filter((s: any) => (phase === "active" ? s.phase !== "closed" : s.phase === phase));

  async function move(c: any, status: any, extra: any = {}) {
    if (!status || status.id === c.status.id) return;
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
    const i = visible.findIndex((s: any) => s.id === c.status.id);
    move(c, visible[i + dir]);
  };
  const phaseTab = (key: string, small: string, label: string, count: number) => (
    <button key={key} className={phase === key ? "on" : ""} onClick={() => setParam("phase", key === "active" ? "" : key)} aria-pressed={phase === key}>
      <small>{small}</small><b>{label} {count}</b>
    </button>
  );

  return (
    <div>
      <div className="row" style={{ marginBottom: 12 }}>
        <input type="search" aria-label="絞り込み" placeholder="案件番号・顧客・品名・図番で絞り込み" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 340 }} />
        <span className="small muted">担当</span>
        <select aria-label="担当者" value={staff} onChange={(e) => setStaff(e.target.value)} style={{ width: 130 }}>
          <option value="">すべて</option>
          {(meta?.staff || []).map((s) => <option key={s.id}>{s.name}</option>)}
        </select>
        <span className="spacer" />
        <Tabs value={view} onChange={(v) => setParam("view", v === "list" ? "" : v)} options={[{ value: "list", label: "一覧" }, { value: "kanban", label: "カンバン" }]} />
        <button className="btn" onClick={() => navigate("/estimates/new")}>＋ 新規見積</button>
      </div>

      <div className="phase-tabs" role="group" aria-label="フェーズ">
        {phaseTab("active", "すべて", "進行中", data.active)}
        {data.phases.map((p: any) => phaseTab(p.key, p.number ? `フェーズ ${p.number}` : "完了・失注・保留", p.label, p.count))}
      </div>
      <div className="row" style={{ marginBottom: 12, gap: 6 }}>
        <span className="small muted">アラート</span>
        {ALERTS.map(([k, label]) => (
          <button key={k} className={`btn small ${alert === k ? "primary" : ""}`} onClick={() => setAlert(alert === k ? "" : k)}>{label} {data.warning_counts[k]}</button>
        ))}
        <span className="spacer" />
        <span className="small muted">基準日 {day(data.reference_date)} ・ {cases.length}件</span>
      </div>
      <ErrorBox error={moveError} />

      {view === "list" ? (
        cases.length === 0 ? <div className="empty">該当する案件はありません。</div> : (
          <>
            <table className="rule" data-testid="cases">
              <thead><tr><th>品名・図番</th><th>顧客</th><th>ステータス</th><th className="right">数量・金額</th><th>納期</th><th>担当</th><th>次のアクション</th><th>ステータス変更</th></tr></thead>
              <tbody>
                {cases.map((c: any) => (
                  <tr key={c.id} data-testid={`case-${c.number}`}>
                    <td>{c.quote_id ? <Link to={`/estimates/${c.quote_id}`}><b>{c.name || c.title}</b></Link> : <b>{c.name || c.title}</b>}
                      <span className="sub">{c.drawing_no}{c.revision ? ` Rev.${c.revision}` : ""}・{c.number}</span></td>
                    <td>{c.customer}</td>
                    <td><StatusDot name={c.status.name} color={c.status.color} />
                      {c.warnings.filter((w: any) => w.kind === "waiting" || w.kind === "stale").map((w: any) => <span key={w.kind} className="sub">{w.label}</span>)}</td>
                    <td className="right nowrap">{c.quantity ?? "—"}個<span className="sub">{c.subtotal ? yen(c.subtotal) : c.missing ? c.missing_short : "—"}</span></td>
                    <td className="nowrap">{day(c.due_date).slice(5) || "—"}
                      {c.warnings.filter((w: any) => w.kind === "overdue" || w.kind === "due_soon").map((w: any) => <span key={w.kind} className={`reason ${w.kind}`} style={{ display: "block", width: "fit-content", marginTop: 2 }}>{w.label}</span>)}</td>
                    <td className="nowrap">{c.staff || "—"}</td>
                    <td className="nowrap"><Link to={actionHref(c.next_action)}><b>{c.next_action.label} →</b></Link></td>
                    <td>
                      <select aria-label="ステータス" value={c.status.id} onChange={(e) => move(c, data.statuses.find((s: any) => s.id === Number(e.target.value)))} style={{ minWidth: 120 }}>
                        {visible.map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="small muted">優先度の高い順（納期超過・未入力あり・納期が近い・回答待ちが長い順）。ステータスは各行、またはカンバン表示から変更できます。</p>
          </>
        )
      ) : (
        <>
          <p className="small muted" style={{ marginTop: 0 }}>ドラッグ、または ←→ でステータスを変更（元に戻せます）</p>
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
                      <div key={c.id} className="kcard" draggable onDragStart={(e) => e.dataTransfer.setData("text/plain", String(c.id))} data-testid={`card-${c.number}`}>
                        <div className="small muted">{c.number}</div>
                        <div className="row" style={{ alignItems: "flex-start", margin: "6px 0", flexWrap: "nowrap" }}>
                          <Thumb revisionId={c.revision_id} hasPdf={c.has_pdf} />
                          <div>
                            <div className="small">{c.customer}</div>
                            <b>{c.name || c.title}</b>
                            <div className="small muted">{c.drawing_no} {c.revision && `Rev.${c.revision}`}</div>
                          </div>
                        </div>
                        <Reasons items={[...c.todo, ...c.warnings.filter((w: any) => w.kind === "stale")]} />
                        <div className="kv"><span className="muted">数量・金額</span><span>{c.quantity ?? "—"}個 ／ {c.subtotal ? yen(c.subtotal) : "未計算"}</span></div>
                        <div className="kv"><span className="muted">納期</span><span>{day(c.due_date)}</span></div>
                        <div className="kv"><span className="muted">担当</span><span>{c.staff || "—"}</span></div>
                        <div className="moves">
                          <button aria-label="前のステータスへ" onClick={() => step(c, -1)}>←</button>
                          <Link to={actionHref(c.next_action)} className="small"><b>{c.next_action.label}</b></Link>
                          <button aria-label="次のステータスへ" onClick={() => step(c, 1)}>→</button>
                        </div>
                        <div className="small muted">更新 {stamp(c.updated_at)}</div>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {lost && (
        <Modal title="失注を記録" onClose={() => setLost(null)}>
          <p className="small muted">{lost.c.number} {lost.c.name}（{lost.c.customer}）。理由と他社価格は実績分析に使います。</p>
          <Field label="失注の理由（必須）">
            <select aria-label="失注の理由" value={lost.reason} onChange={(e) => setLost({ ...lost, reason: e.target.value })}>
              <option value="">選択してください</option>
              {(meta?.lost_reasons || []).map((r) => <option key={r}>{r}</option>)}
            </select>
          </Field>
          <Field label="他社価格（判明していれば、税抜の合計）"><input aria-label="他社価格" type="number" value={lost.price} onChange={(e) => setLost({ ...lost, price: e.target.value })} /></Field>
          <Field label="メモ"><input value={lost.note} onChange={(e) => setLost({ ...lost, note: e.target.value })} /></Field>
          <div className="row">
            <button className="btn primary" disabled={!lost.reason} onClick={() => { move(lost.c, lost.status, { lost_reason: lost.reason, competitor_price: lost.price ? Number(lost.price) : null, lost_note: lost.note }); setLost(null); }}>失注を登録</button>
            <button className="btn" onClick={() => setLost(null)}>キャンセル</button>
          </div>
        </Modal>
      )}
    </div>
  );
}
