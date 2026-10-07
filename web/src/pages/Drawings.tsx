import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { day, get, yen } from "../api";
import { ErrorBox, Field, Loading, StatusTag, Tabs, Thumb, useMeta } from "../ui";
import { FlatPattern } from "../viewers";

type Mode = "conditions" | "similar" | "text";
const EMPTY = { keyword: "", customer: "", material: "", category_id: "", status: "", process: "", dim_min: "", dim_max: "", date_from: "", date_to: "" };
const MODES: { value: Mode; label: string; note: string }[] = [
  { value: "conditions", label: "条件検索", note: "図番・品名・顧客・材質など" },
  { value: "similar", label: "類似形状検索", note: "基準図面を選択し、形状の近い順に表示" },
  { value: "text", label: "図面内文字検索", note: "注記・加工指示のキーワードで検索" },
];

export default function DrawingsPage() {
  const { meta } = useMeta();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const initial = params.get("mode") as Mode;
  const [mode, setModeState] = useState<Mode>(MODES.some((m) => m.value === initial) ? initial : "conditions");
  const setMode = (m: Mode) => { setModeState(m); setParams(m === "conditions" ? {} : { mode: m }, { replace: true }); };
  const [view, setView] = useState<"preview" | "list">("preview");
  const [more, setMore] = useState(false);
  const [f, setF] = useState<any>(EMPTY);
  const [attrs, setAttrs] = useState<any>({});
  const [text, setText] = useState("");
  const [base, setBase] = useState(params.get("base") || "");
  const [all, setAll] = useState<any[] | null>(null);
  const [items, setItems] = useState<any[] | null>(null);
  const [similar, setSimilar] = useState<any[] | null>(null);
  const [error, setError] = useState<any>(null);
  const set = (k: string, v: any) => setF((x: any) => ({ ...x, [k]: v }));

  useEffect(() => {
    get("/api/drawings").then(setAll).catch(setError);
  }, []);
  useEffect(() => {
    const p = new URLSearchParams();
    if (mode === "conditions") Object.entries(f).forEach(([k, v]) => v && p.set(k, String(v)));
    if (mode === "text" && text.trim()) p.set("text", text.trim());
    const custom = Object.fromEntries(Object.entries(attrs).filter(([, v]) => v !== "" && v !== null));
    if (mode === "conditions" && Object.keys(custom).length) p.set("attrs", JSON.stringify(custom));
    const t = setTimeout(() => get(`/api/drawings?${p}`).then(setItems).catch(setError), 200);
    return () => clearTimeout(t);
  }, [f, attrs, text, mode]);
  useEffect(() => {
    if (mode !== "similar" || !base) return setSimilar(null);
    get(`/api/drawings/${base}/similar`).then(setSimilar).catch(setError);
  }, [mode, base]);

  const custom = (meta?.attributes || []).filter((a) => !a.builtin && a.searchable);
  const builtin = useMemo(() => new Set((meta?.attributes || []).filter((a) => a.builtin && a.searchable).map((a) => a.key)), [meta]);
  const customers = useMemo(() => Array.from(new Set((all || []).map((d) => d.customer).filter(Boolean))).sort(), [all]);
  const categories = (meta?.categories || []).flatMap((g) => g.children.map((c) => ({ ...c, group: g.name })));
  const shown = mode === "similar" ? similar : items;
  const total = (all || []).length;

  return (
    <div>
      <div className="row between" style={{ marginBottom: 12 }}>
        <span className="small muted">登録図面 {total}件</span>
        <button className="btn" onClick={() => navigate("/drawings/register")}>図面登録</button>
      </div>
      <div className="searchmodes" role="tablist" aria-label="検索方法">
        {MODES.map((m) => (
          <button key={m.value} role="tab" aria-selected={mode === m.value} className={mode === m.value ? "on" : ""} onClick={() => setMode(m.value)}>
            <b>{m.label}</b><span>{m.note}</span>
          </button>
        ))}
      </div>

      {mode === "conditions" && (
        <>
          <div className="filters">
            <Field label="図番・品名"><input type="search" aria-label="図番・品名" placeholder="例: DB-25 ブラケット" value={f.keyword} onChange={(e) => set("keyword", e.target.value)} /></Field>
            {builtin.has("customer") && (
              <Field label="顧客"><select aria-label="顧客" value={f.customer} onChange={(e) => set("customer", e.target.value)}>
                <option value="">すべて</option>{customers.map((c) => <option key={c}>{c}</option>)}</select></Field>
            )}
            {builtin.has("material") && (
              <Field label="材質"><select aria-label="材質" value={f.material} onChange={(e) => set("material", e.target.value)}>
                <option value="">すべて</option>{(meta?.materials || []).map((m) => <option key={m.code} value={m.code}>{m.code}</option>)}</select></Field>
            )}
            <Field label="分類"><select aria-label="分類" value={f.category_id} onChange={(e) => set("category_id", e.target.value)}>
              <option value="">すべての図面</option>{categories.map((c) => <option key={c.id} value={c.id}>{c.group} / {c.name}（{c.count}）</option>)}</select></Field>
            <button className="btn" onClick={() => setMore(!more)} aria-expanded={more}>{more ? "条件を閉じる" : "条件を追加"}</button>
            <button className="btn link" onClick={() => { setF(EMPTY); setAttrs({}); }}>条件をクリア</button>
          </div>
          {more && (
            <div className="filters panel" style={{ padding: "12px 14px" }}>
              {builtin.has("status") && (
                <Field label="ステータス"><select aria-label="ステータス" value={f.status} onChange={(e) => set("status", e.target.value)}>
                  <option value="">すべて</option><option>未作成</option>{(meta?.statuses || []).map((s) => <option key={s.id}>{s.name}</option>)}</select></Field>
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
                <Field label="登録日"><div className="row" style={{ flexWrap: "nowrap", gap: 4 }}>
                  <input aria-label="登録日 から" type="date" value={f.date_from} onChange={(e) => set("date_from", e.target.value)} />〜
                  <input aria-label="登録日 まで" type="date" value={f.date_to} onChange={(e) => set("date_to", e.target.value)} /></div></Field>
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
              <Link to="/settings/categories" className="small" style={{ alignSelf: "center" }}><b>検索項目・分類を設定</b></Link>
            </div>
          )}
        </>
      )}
      {mode === "similar" && (
        <div className="filters">
          <Field label="基準図面"><select aria-label="基準の図面" value={base} onChange={(e) => setBase(e.target.value)} style={{ width: 340 }}>
            <option value="">基準にする図面を選択…</option>
            {(all || []).map((d) => <option key={d.id} value={d.id}>{d.drawing_no} {d.name}</option>)}
          </select></Field>
          <span className="small muted" style={{ alignSelf: "center" }}>材料が同じ・曲げ数の差1以内・穴数の差2以内を、差の小さい順に表示</span>
        </div>
      )}
      {mode === "text" && (
        <div className="filters">
          <Field label="図面内の文字"><input type="search" aria-label="図面内の文字" placeholder="例: M4タップ、SPCC" value={text} onChange={(e) => setText(e.target.value)} style={{ width: 340 }} /></Field>
          <span className="small muted" style={{ alignSelf: "center" }}>文字データのある図面PDFが対象（スキャン画像は対象外）</span>
        </div>
      )}

      <div className="row" style={{ margin: "6px 0 12px" }}>
        <b>{shown ? `${shown.length}件` : ""}</b>
        <span className="spacer" />
        <Tabs value={view} onChange={setView} options={[{ value: "preview", label: "サムネイル" }, { value: "list", label: "リスト" }]} />
      </div>
      <ErrorBox error={error} />
      {!shown ? (mode === "similar" && !base ? <div className="empty">基準にする図面を選択してください。</div> : <Loading />) : shown.length === 0 ? (
        <div className="empty">{total === 0 ? <>図面がまだありません。<Link to="/drawings/register">図面登録</Link>から登録してください。</> : "条件に合う図面はありません。"}</div>
      ) : view === "preview" ? (
        <div className="cards" data-testid="drawing-cards">
          {shown.map((d: any, i: number) => mode === "similar" ? <SimilarCard key={i} s={d} /> : (
            <article key={d.id} className="card" onClick={() => navigate(`/drawings/${d.id}`)}>
              <div className="pv hatch">{d.has_pdf ? <Thumb revisionId={d.revision_id} hasPdf className="" /> : d.flat_pattern ? <FlatPattern flat={d.flat_pattern} height={116} /> : null}</div>
              <div className="body">
                <div className="row between"><span className="small">{d.drawing_no} {d.revision && `Rev.${d.revision}`}</span>
                  {d.status ? <StatusTag name={d.status.name} color={d.status.color} /> : <span className="tag plain">見積未作成</span>}</div>
                <h3>{d.name || "（品名未設定）"}</h3>
                <div className="small muted">{[d.customer, d.material, d.processes.slice(0, 2).join("・")].filter(Boolean).join(" ・ ")}</div>
                <div className="price"><span className="num">{d.unit_price ? yen(d.unit_price) : d.quote_id ? "未計算" : "—"}</span><span className="small muted">{d.quote_id ? `単価・${d.quantity ?? "—"}個` : `登録 ${day(d.created_at)}`}</span></div>
                <div className="acts" onClick={(e) => e.stopPropagation()}>
                  {d.quote_id ? <Link to={`/estimates/${d.quote_id}`}>見積を表示 →</Link> : <Link to={`/estimates/new?drawing=${d.id}`}>新規見積 →</Link>}
                  <Link to={`/drawings/${d.id}`}>図面を表示</Link>
                </div>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <table className="rule">
          <thead><tr><th>図面</th><th>図番</th><th>品名</th><th>顧客</th><th>材質</th><th>穴・曲げ</th><th>ステータス</th><th className="right">単価</th><th>登録日</th><th /></tr></thead>
          <tbody>
            {shown.map((d: any, i: number) => {
              const drawingId = mode === "similar" ? d.drawing_id : d.id;
              return (
                <tr key={i} className={drawingId ? "click" : ""} onClick={() => drawingId && navigate(`/drawings/${drawingId}`)}>
                  <td><Thumb revisionId={d.revision_id} hasPdf={d.has_pdf} /></td>
                  <td>{d.drawing_no} {d.revision && `Rev.${d.revision}`}</td>
                  <td>{d.name}</td>
                  <td>{d.customer}</td>
                  <td>{d.material}</td>
                  <td>{mode === "similar" ? `${d.holes}・${d.bends}（${d.difference}）` : `${d.metrics?.hole_count ?? "—"}・${d.metrics?.bend_count ?? "—"}`}</td>
                  <td>{mode === "similar" ? d.status : d.status ? <StatusTag name={d.status.name} color={d.status.color} /> : "見積未作成"}</td>
                  <td className="right">{d.unit_price ? yen(d.unit_price) : "—"}</td>
                  <td>{day(d.created_at || d.date)}</td>
                  <td onClick={(e) => e.stopPropagation()}>{mode !== "similar" && (d.quote_id ? <Link to={`/estimates/${d.quote_id}`}>見積を表示</Link> : <Link to={`/estimates/new?drawing=${d.id}`}>新規見積</Link>)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function SimilarCard({ s }: { s: any }) {
  const navigate = useNavigate();
  return (
    <article className="card" onClick={() => s.drawing_id && navigate(`/drawings/${s.drawing_id}`)} style={{ cursor: s.drawing_id ? "pointer" : "default" }}>
      <div className="pv hatch">{s.has_pdf && <Thumb revisionId={s.revision_id} hasPdf className="" />}</div>
      <div className="body">
        <div className="row between"><span className="small">{s.drawing_no || s.quote_no} {s.revision && `Rev.${s.revision}`}</span><span className="tag">{s.difference}</span></div>
        <h3>{s.name || "—"}</h3>
        <div className="small muted">{s.customer} ・ {s.material} ・ 穴{s.holes} ・ 曲げ{s.bends}</div>
        <div className="price"><span className="num">{s.unit_price ? yen(s.unit_price) : "—"}</span><span className="small muted">{s.quantity ? `×${s.quantity}個` : ""}</span></div>
        <div className="small muted">{s.kind === "drawing" ? "登録図面" : "過去見積"} ・ {s.status} ・ {day(s.date)}</div>
      </div>
    </article>
  );
}
