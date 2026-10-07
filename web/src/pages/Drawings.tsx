import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { day, get, yen } from "../api";
import { ErrorBox, Field, Loading, Marked, StatusTag, Tabs, Thumb, useMeta } from "../ui";
import { FlatPattern } from "../viewers";

type Mode = "conditions" | "similar" | "text";
const EMPTY = { drawing_no: "", name: "", customer: "", status: "", material: "", process: "", dim_min: "", dim_max: "", date_from: "", date_to: "", category_id: "" };

export default function DrawingsPage() {
  const { meta } = useMeta();
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>("conditions");
  const [view, setView] = useState<"preview" | "list">("preview");
  const [f, setF] = useState<any>(EMPTY);
  const [attrs, setAttrs] = useState<any>({});
  const [text, setText] = useState("");
  const [base, setBase] = useState("");
  const [all, setAll] = useState<any[] | null>(null);
  const [items, setItems] = useState<any[] | null>(null);
  const [similar, setSimilar] = useState<any[] | null>(null);
  const [error, setError] = useState<any>(null);
  const set = (k: string, v: any) => setF((x: any) => ({ ...x, [k]: v }));

  useEffect(() => {
    get("/api/drawings").then(setAll).catch(setError);
  }, []);
  useEffect(() => {
    const params = new URLSearchParams();
    if (mode === "conditions") Object.entries(f).forEach(([k, v]) => v && params.set(k, String(v)));
    if (mode === "text" && text.trim()) params.set("text", text.trim());
    const custom = Object.fromEntries(Object.entries(attrs).filter(([, v]) => v !== "" && v !== null));
    if (mode === "conditions" && Object.keys(custom).length) params.set("attrs", JSON.stringify(custom));
    if (f.category_id) params.set("category_id", f.category_id);
    const t = setTimeout(() => get(`/api/drawings?${params}`).then(setItems).catch(setError), 200);
    return () => clearTimeout(t);
  }, [f, attrs, text, mode]);
  useEffect(() => {
    if (mode !== "similar" || !base) return setSimilar(null);
    get(`/api/drawings/${base}/similar`).then(setSimilar).catch(setError);
  }, [mode, base]);

  const custom = (meta?.attributes || []).filter((a) => !a.builtin && a.searchable);
  const builtin = useMemo(() => new Set((meta?.attributes || []).filter((a) => a.builtin && a.searchable).map((a) => a.key)), [meta]);
  const shown = mode === "similar" ? similar : items;
  const total = (all || []).length;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "220px minmax(0, 1fr)", gap: 26 }}>
      <aside>
        <h2 style={{ marginBottom: 10 }}>検索条件</h2>
        {builtin.has("drawing_no") && <Field label="図番"><input aria-label="図番" placeholder="例: DB-25" value={f.drawing_no} onChange={(e) => set("drawing_no", e.target.value)} /></Field>}
        {builtin.has("name") && <Field label="品名"><input aria-label="品名" placeholder="例: ブラケット" value={f.name} onChange={(e) => set("name", e.target.value)} /></Field>}
        {builtin.has("customer") && <Field label="顧客名"><input aria-label="顧客名" value={f.customer} onChange={(e) => set("customer", e.target.value)} /></Field>}
        {builtin.has("status") && (
          <Field label="ステータス"><select aria-label="ステータス" value={f.status} onChange={(e) => set("status", e.target.value)}>
            <option value="">すべて</option><option>未作成</option>{(meta?.statuses || []).map((s) => <option key={s.id}>{s.name}</option>)}</select></Field>
        )}
        {builtin.has("material") && (
          <Field label="材質"><select aria-label="材質" value={f.material} onChange={(e) => set("material", e.target.value)}>
            <option value="">すべて</option>{(meta?.materials || []).map((m) => <option key={m.code} value={m.code}>{m.code}</option>)}</select></Field>
        )}
        {builtin.has("process") && (
          <Field label="加工"><select aria-label="加工" value={f.process} onChange={(e) => set("process", e.target.value)}>
            <option value="">すべて</option>{(meta?.attributes.find((a) => a.key === "process")?.options || []).map((o: string) => <option key={o}>{o}</option>)}</select></Field>
        )}
        {builtin.has("max_dimension") && (
          <Field label="最大寸法（mm）"><div className="row" style={{ flexWrap: "nowrap", gap: 4 }}>
            <input aria-label="最大寸法 下限" placeholder="下限" type="number" value={f.dim_min} onChange={(e) => set("dim_min", e.target.value)} />〜
            <input aria-label="最大寸法 上限" placeholder="上限" type="number" value={f.dim_max} onChange={(e) => set("dim_max", e.target.value)} /></div></Field>
        )}
        {builtin.has("created") && (
          <Field label="登録日"><input aria-label="登録日 から" type="date" value={f.date_from} onChange={(e) => set("date_from", e.target.value)} />
            <input aria-label="登録日 まで" type="date" value={f.date_to} onChange={(e) => set("date_to", e.target.value)} style={{ marginTop: 4 }} /></Field>
        )}
        {custom.map((a) => (
          <Field key={a.key} label={`${a.label}${a.unit ? `（${a.unit}）` : ""}`}>
            {a.input_type === "select" ? (
              <select value={attrs[a.key] || ""} onChange={(e) => setAttrs({ ...attrs, [a.key]: e.target.value })}>
                <option value="">すべて</option>{a.options.map((o: string) => <option key={o}>{o}</option>)}</select>
            ) : a.input_type === "number_range" || a.input_type === "date_range" ? (
              <div className="row" style={{ flexWrap: "nowrap", gap: 4 }}>
                {(a.input_type === "number_range" ? ["min", "max"] : ["from", "to"]).map((side) => (
                  <input key={side} type={a.input_type === "number_range" ? "number" : "date"} value={attrs[a.key]?.[side] || ""}
                    onChange={(e) => setAttrs({ ...attrs, [a.key]: { ...(attrs[a.key] || {}), [side]: e.target.value } })} />
                ))}
              </div>
            ) : (
              <input value={attrs[a.key] || ""} onChange={(e) => setAttrs({ ...attrs, [a.key]: e.target.value })} />
            )}
          </Field>
        ))}
        <div className="row between">
          <button className="btn link" onClick={() => { setF(EMPTY); setAttrs({}); }}>条件をクリア</button>
          <Link to="/settings/categories" className="small"><b>検索項目を設定</b></Link>
        </div>
        <div style={{ borderTop: "1px solid var(--line)", marginTop: 16, paddingTop: 12 }}>
          <div className="small muted">分類</div>
          <button className={`btn link ${f.category_id ? "" : "on"}`} style={{ width: "100%", justifyContent: "space-between", background: f.category_id ? "none" : "var(--accent-soft)" }} onClick={() => set("category_id", "")}>
            すべての図面 <span>{total}</span>
          </button>
          {(meta?.categories || []).map((g) => (
            <div key={g.id} style={{ marginTop: 8 }}>
              <div className="small muted">{g.name}</div>
              {g.children.map((c) => (
                <button key={c.id} className="btn link" style={{ width: "100%", justifyContent: "space-between", fontWeight: 400, color: "var(--text)", background: String(c.id) === f.category_id ? "var(--accent-soft)" : "none" }}
                  onClick={() => set("category_id", String(c.id))}>{c.name}<span className="muted">{c.count}</span></button>
              ))}
            </div>
          ))}
          <Link to="/settings/categories" className="small"><b>分類を設定する</b></Link>
        </div>
      </aside>

      <section>
        <div className="row" style={{ marginBottom: 14 }}>
          <span className="small">探し方</span>
          <Tabs value={mode} onChange={setMode} options={[{ value: "conditions", label: "条件で探す" }, { value: "similar", label: "似た形" }, { value: "text", label: "図面内の文字" }]} />
          {mode === "similar" && (
            <select aria-label="基準の図面" value={base} onChange={(e) => setBase(e.target.value)} style={{ width: 280 }}>
              <option value="">基準にする図面を選ぶ…</option>
              {(all || []).map((d) => <option key={d.id} value={d.id}>{d.drawing_no} {d.name}</option>)}
            </select>
          )}
          {mode === "text" && <input aria-label="図面内の文字" placeholder="図面PDFの文字（例: M4タップ、SPCC）" value={text} onChange={(e) => setText(e.target.value)} style={{ width: 280 }} />}
          <span className="spacer" />
          <span className="small muted">{shown ? `${shown.length}件` : ""}</span>
          <Tabs value={view} onChange={setView} options={[{ value: "preview", label: "プレビュー" }, { value: "list", label: "リスト" }]} />
        </div>
        <ErrorBox error={error} />
        {!shown ? (mode === "similar" && !base ? <div className="empty">基準にする図面を選んでください。</div> : <Loading />) : shown.length === 0 ? (
          <div className="empty">{total === 0 ? <>図面がまだありません。<Link to="/drawings/register">図面を登録</Link>してください。</> : "条件に合う図面はありません。"}</div>
        ) : view === "preview" ? (
          <div className="cards" data-testid="drawing-cards">
            {shown.map((d: any, i: number) => mode === "similar" ? <SimilarCard key={i} s={d} /> : (
              <Marked key={d.id} className="card" onClick={() => navigate(`/drawings/${d.id}`)}>
                <div className="pv hatch">{d.has_pdf ? <Thumb revisionId={d.revision_id} hasPdf className="" /> : d.flat_pattern ? <FlatPattern flat={d.flat_pattern} height={116} /> : null}</div>
                <div className="body">
                  <div className="row between"><span className="small">{d.drawing_no} {d.revision && `Rev.${d.revision}`}</span>
                    {d.status ? <StatusTag name={d.status.name} color={d.status.color} /> : <span className="tag plain">未作成</span>}</div>
                  <h3>{d.name || "（品名未設定）"}</h3>
                  <div className="small muted">{[d.customer, d.material, d.processes.slice(0, 2).join("・")].filter(Boolean).join(" ・ ")}</div>
                  <div className="price"><span className="num">{d.unit_price ? yen(d.unit_price) : "未作成"}</span><span className="small muted">{d.quantity ? `×${d.quantity}個` : ""}</span></div>
                  <div className="small muted">{day(d.quote_date || d.created_at)}</div>
                </div>
              </Marked>
            ))}
          </div>
        ) : (
          <table className="rule">
            <thead><tr><th>図面</th><th>図番</th><th>品名</th><th>顧客</th><th>材質</th><th>穴・曲げ</th><th>状態</th><th className="right">単価</th><th>登録日</th></tr></thead>
            <tbody>
              {shown.map((d: any, i: number) => (
                <tr key={i} className="click" onClick={() => d.drawing_id !== null && navigate(`/drawings/${d.drawing_id ?? d.id}`)}>
                  <td><Thumb revisionId={d.revision_id} hasPdf={d.has_pdf} /></td>
                  <td>{d.drawing_no} {d.revision && `Rev.${d.revision}`}</td>
                  <td>{d.name}</td>
                  <td>{d.customer}</td>
                  <td>{d.material}</td>
                  <td>{mode === "similar" ? `${d.holes}・${d.bends}（${d.difference}）` : `${d.metrics?.hole_count ?? "—"}・${d.metrics?.bend_count ?? "—"}`}</td>
                  <td>{mode === "similar" ? d.status : d.status ? <StatusTag name={d.status.name} color={d.status.color} /> : "未作成"}</td>
                  <td className="right">{d.unit_price ? yen(d.unit_price) : "—"}</td>
                  <td>{day(d.created_at || d.date)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function SimilarCard({ s }: { s: any }) {
  const navigate = useNavigate();
  return (
    <Marked className="card" onClick={() => s.drawing_id && navigate(`/drawings/${s.drawing_id}`)} style={{ cursor: s.drawing_id ? "pointer" : "default" }}>
      <div className="pv hatch">{s.has_pdf && <Thumb revisionId={s.revision_id} hasPdf className="" />}</div>
      <div className="body">
        <div className="row between"><span className="small">{s.drawing_no || s.quote_no} {s.revision && `Rev.${s.revision}`}</span><span className="tag">{s.difference}</span></div>
        <h3>{s.name || "—"}</h3>
        <div className="small muted">{s.customer} ・ {s.material} ・ 穴{s.holes} ・ 曲げ{s.bends}</div>
        <div className="price"><span className="num">{s.unit_price ? yen(s.unit_price) : "未作成"}</span><span className="small muted">{s.quantity ? `×${s.quantity}個` : ""}</span></div>
        <div className="small muted">{s.kind === "drawing" ? "登録図面" : "過去見積"} ・ {s.status} ・ {day(s.date)}</div>
      </div>
    </Marked>
  );
}
