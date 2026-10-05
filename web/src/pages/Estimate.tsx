import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, day, get, num, post, put, stamp, yen } from "../api";
import { ErrorBox, LineTabs, Loading, Marked, StatusTag, useMeta, useToast } from "../ui";
import { FlatPattern, Model3D } from "../viewers";

const SHAPE: [string, string, string][] = [
  ["thickness_mm", "板厚", "mm"], ["blank_area_mm2", "展開面積", "mm²"], ["cut_length_mm", "切断長", "mm"], ["hole_count", "穴数", ""], ["bend_count", "曲げ数", ""],
];

export default function EstimatePage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const [q, setQ] = useState<any>(null);
  const [error, setError] = useState<any>(null);
  const [customerMode, setCustomerMode] = useState(false);
  const [tab, setTab] = useState<"similar" | "history" | "chat" | "shape">("similar");

  const load = () => get(`/api/estimates/${id}`).then((d) => { setQ(d); setError(null); }).catch(setError);
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  useEffect(() => {
    if (q?.job && (q.job.status === "queued" || q.job.status === "running")) {
      const t = setTimeout(load, 800);
      return () => clearTimeout(t);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  async function save(inputs: any, extra: any = {}) {
    try {
      const d = await put(`/api/estimates/${id}`, { version: q.version, inputs, ...extra });
      setQ(d);
      setError(null);
      toast("条件を保存し、計算し直しました");
      return true;
    } catch (e) {
      setError(e);
      return false;
    }
  }

  if (!q) return error ? <ErrorBox error={error} /> : <Loading />;
  const r = q.result;
  const price = r.price;
  const inp = q.inputs;
  const conflict = error instanceof ApiError && error.status === 409 && error.code === "CONFLICT";
  return (
    <div>
      <div className="row">
        <span className="small muted">{q.number}</span>
        <StatusTag name={q.status.name} color={q.status.color} />
        {q.job && (q.job.status === "queued" || q.job.status === "running") && <span className="tag">解析中…</span>}
      </div>
      <div className="row between" style={{ alignItems: "flex-end", margin: "4px 0 14px" }}>
        <div>
          <h1 style={{ fontSize: 30 }}>
            {q.drawing?.name || q.title}
            <span className="num" style={{ fontSize: 26, marginLeft: 14 }}>{q.drawing?.drawing_no} {q.drawing?.revision ? `Rev.${q.drawing.revision}` : ""}</span>
          </h1>
          <div className="small muted">
            {q.customer} ／ {materialText(inp)} ／ {q.analysis?.thickness_mm ? `t${q.analysis.thickness_mm}` : "板厚—"} ／ 希望納期 {day(q.due_date)} ／ 担当 {q.staff || "—"}
          </div>
        </div>
        <div className="row">
          <button className={`btn ${customerMode ? "primary" : ""}`} onClick={() => setCustomerMode(!customerMode)} aria-pressed={customerMode}>顧客提示モード</button>
          <button className="btn" onClick={() => document.getElementById("conditions")?.scrollIntoView({ behavior: "smooth" })}>条件を修正</button>
          <Marked>
            <button className="btn primary" disabled={!r.issuable} title={r.issuable ? "" : "未入力の項目があります"} onClick={() => navigate(`/documents?quote=${q.id}&kind=quote`)}>
              見積書を発行
            </button>
          </Marked>
        </div>
      </div>

      <ErrorBox error={error} />
      {conflict && <p><button className="btn" onClick={load}>最新の内容を読み直す</button></p>}

      <div className="figures" style={{ gridTemplateColumns: customerMode ? "1fr 1fr 1.6fr" : `repeat(${(r.groups || []).length || 4}, 1fr) 1.8fr` }} data-testid="figures">
        {!customerMode && (r.groups || [{ name: "材料費" }, { name: "加工費" }, { name: "表面処理" }, { name: "粗利" }]).map((g: any) => (
          <div key={g.name}>
            <span className="label">{g.name}{g.rate !== undefined ? ` ${Math.round(g.rate * 100)}%` : ""}</span>
            <div className="value">{price ? yen(g.amount) : "—"}</div>
          </div>
        ))}
        {customerMode && (
          <>
            <div><span className="label">数量</span><div className="value">{inp.quantity ?? "—"}個</div></div>
            <div><span className="label">単価（税抜）</span><div className="value">{price ? yen(price.unit_price) : "—"}</div></div>
          </>
        )}
        <div className="hl">
          <span className="label">総額（税抜）・数量 {inp.quantity ?? "—"}個</span>
          <div className="value big" data-testid="total">{price ? yen(price.subtotal) : "未計算"}</div>
          <span className="small">{price ? `単価 ${yen(price.unit_price)} ／ 消費税 ${yen(price.tax)} ／ 税込 ${yen(price.total)}` : "未入力の項目を入れると計算します"}</span>
        </div>
      </div>

      {r.missing.length > 0 && (
        <div className="notice warnbox" style={{ marginTop: 14 }} data-testid="missing">
          <b>！未入力の項目が {r.missing.length} 件あります（すべて入力するまで見積書を発行できません）</b>
          <ul>{r.missing.map((m: any, i: number) => <li key={i}>{m.message}</li>)}</ul>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.9fr) minmax(300px, 1fr)", gap: 26, marginTop: 20 }}>
        <div>
          {!customerMode ? (
            <>
              <h2 style={{ marginBottom: 8 }}>原価内訳</h2>
              <table className="rule" data-testid="cost-lines">
                <thead><tr><th>区分</th><th>項目</th><th className="right">数量</th><th className="right">単価</th><th className="right">金額</th></tr></thead>
                <tbody>
                  {r.lines.map((l: any, i: number) => (
                    <tr key={i}>
                      <td className="muted small">{i === 0 || r.lines[i - 1].group !== l.group ? l.group : ""}</td>
                      <td>{l.name}</td>
                      <td className="right nowrap">{num(l.quantity, 2)} {unitLabel(l.unit)}</td>
                      <td className="right nowrap">{l.unit_price === null ? "" : `¥${num(l.unit_price, 2)}`}</td>
                      <td className="right nowrap">{yen(l.amount)}</td>
                    </tr>
                  ))}
                  {price && (
                    <>
                      <tr><td className="muted small">小計</td><td>原価の合計{inp.rush ? "（特急割増を含む）" : ""}</td><td /><td /><td className="right">{yen(r.subtotal_cost)}</td></tr>
                      <tr><td className="muted small">粗利</td><td>小計 × 粗利率</td><td className="right">{Math.round(r.margin_rate * 100)}%</td><td /><td className="right">{yen(r.margin)}</td></tr>
                      <tr className="total"><td>合計</td><td>数量 {price.quantity}個（単価は1円未満切り上げ）</td><td /><td className="right">{yen(price.unit_price)}</td><td className="right">{yen(price.subtotal)}</td></tr>
                      <tr><td /><td>消費税（{Math.round(price.tax_rate * 100)}%、1円未満切り捨て）</td><td /><td /><td className="right">{yen(price.tax)}</td></tr>
                      <tr><td /><td><b>合計（税込）</b></td><td /><td /><td className="right"><b>{yen(price.total)}</b></td></tr>
                    </>
                  )}
                  {!price && <tr><td colSpan={5} className="empty">未入力の項目があるため、金額はまだ計算していません。</td></tr>}
                </tbody>
              </table>
            </>
          ) : (
            <>
              <h2 style={{ marginBottom: 8 }}>お見積り（顧客提示）</h2>
              <table className="rule">
                <thead><tr><th>品名・図番</th><th className="right">数量</th><th className="right">単価</th><th className="right">金額</th></tr></thead>
                <tbody>
                  <tr><td>{q.drawing?.name} <span className="sub">{q.drawing?.drawing_no} {q.drawing?.revision ? `Rev.${q.drawing.revision}` : ""}</span></td>
                    <td className="right">{inp.quantity}個</td><td className="right">{price ? yen(price.unit_price) : "—"}</td><td className="right">{price ? yen(price.subtotal) : "—"}</td></tr>
                  <tr className="total"><td>合計（税込）</td><td /><td /><td className="right">{price ? yen(price.total) : "—"}</td></tr>
                </tbody>
              </table>
              <p className="small muted">原価・粗利は表示していません。</p>
            </>
          )}

          <div className="section-title"><h2>図面から読み取った条件</h2></div>
          {r.items.length === 0 ? (
            <div className="empty">図面PDFの読み取り結果はありません（条件は下で入力します）。</div>
          ) : (
            <table className="rule" data-testid="drawing-items">
              <thead><tr><th>項目</th><th>状態</th><th>図面の値</th><th>見積に使う値</th></tr></thead>
              <tbody>
                {r.items.map((it: any) => (
                  <tr key={it.field}>
                    <td>{it.label}</td>
                    <td><span className={it.status === "確定" ? "tag soft" : it.status === "要確認" ? "tag solid" : "tag grey"}>{it.status_label}</span></td>
                    <td>{it.read}{it.reasons.length > 0 && <span className="sub">{it.reasons.join("・")}</span>}</td>
                    <td>{it.used}{it.notice && <span className="sub warn">⚠ {it.notice}（形状の板厚で計算）</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <Conditions q={q} onSave={save} />
        </div>

        <aside>
          {r.hints.length > 0 && (
            <Marked className="panel" style={{ marginBottom: 18 }}>
              <h3 style={{ marginBottom: 6 }}>確認のヒント</h3>
              <ul style={{ margin: 0, paddingLeft: 18 }} className="small">{r.hints.map((h: string, i: number) => <li key={i}>{h}</li>)}</ul>
              <p className="small muted" style={{ marginBottom: 0 }}>要確認は発行を止めません。入力された値で見積書を発行できます。</p>
            </Marked>
          )}
          <LineTabs value={tab} onChange={setTab} options={[
            { value: "similar", label: `類似実績 ${q.similar.length}` }, { value: "shape", label: "形状解析" },
            { value: "history", label: "修正履歴" }, { value: "chat", label: "チャット" },
          ]} />
          {tab === "similar" && <SimilarList items={q.similar} unitPrice={price?.unit_price} />}
          {tab === "shape" && <ShapePanel q={q} />}
          {tab === "history" && (
            <div className="stack">
              {q.edit_log.length === 0 && <span className="muted">修正はまだありません。</span>}
              {q.edit_log.map((e: any, i: number) => (
                <div key={i} style={{ borderBottom: "1px solid var(--line)", paddingBottom: 8 }}>
                  <div className="small muted">{stamp(e.at)} {e.actor && `・ ${e.actor}`}</div>
                  <div>{e.summary}</div>
                  <div className="small">{e.total_before !== null || e.total_after !== null ? `${yen(e.total_before)} → ${yen(e.total_after)}` : ""}</div>
                </div>
              ))}
            </div>
          )}
          {tab === "chat" && <Chat q={q} onDone={(d) => { setQ(d); setError(null); }} onError={setError} />}
          {q.documents.length > 0 && (
            <>
              <h3 style={{ margin: "20px 0 6px" }}>発行した帳票</h3>
              {q.documents.map((d: any) => (
                <div key={d.id} className="row small" style={{ borderBottom: "1px solid var(--line)", padding: "5px 0" }}>
                  <span className="tag plain">{({ quote: "見積書", delivery: "納品書", invoice: "請求書" } as any)[d.kind]}</span>
                  <a href={d.url} target="_blank" rel="noreferrer">{d.number}</a>
                  <span className="spacer" />{yen(d.total)}
                </div>
              ))}
            </>
          )}
          <p style={{ marginTop: 18 }}><Link to="/cases">案件・進捗で見る →</Link></p>
        </aside>
      </div>
    </div>
  );
}

function unitLabel(u: string) {
  return ({ kg: "kg", mm: "mm", 穴: "穴", 曲げ: "か所", 式: "式", piece: "か所", hole: "か所", bend: "か所", point: "点", location: "か所", part: "個", job: "式", 個: "個" } as any)[u] ?? u;
}

function materialText(inp: any) {
  if (inp.material) return inp.material;
  if (inp.custom_material) return `${inp.custom_material.name}（未登録）`;
  return "材質未選択";
}

function SimilarList({ items, unitPrice }: { items: any[]; unitPrice?: number }) {
  if (!items.length) return <div className="small muted">材料が同じで、曲げ数の差が1以内・穴数の差が2以内の実績はありません。</div>;
  return (
    <div data-testid="similar">
      <p className="small muted" style={{ marginTop: 0 }}>材料が同じ・曲げ数±1・穴数±2。差の小さい順。</p>
      <table className="rule">
        <thead><tr><th>図番</th><th>差</th><th className="right">数量</th><th className="right">単価</th><th>結果</th></tr></thead>
        <tbody>
          {items.slice(0, 12).map((s, i) => (
            <tr key={i}>
              <td>{s.drawing_id ? <Link to={`/drawings/${s.drawing_id}`}>{s.drawing_no}</Link> : s.drawing_no || s.quote_no}
                <span className="sub">{s.name} ・ {s.customer} ・ {day(s.date)}</span></td>
              <td className="small">{s.difference}</td>
              <td className="right">{s.quantity ?? "—"}</td>
              <td className="right">{s.unit_price ? yen(s.unit_price) : "—"}{unitPrice && s.unit_price ? <span className="sub">{s.unit_price >= unitPrice ? "+" : ""}{Math.round(((s.unit_price - unitPrice) / unitPrice) * 100)}%</span> : null}</td>
              <td><span className="tag plain">{s.status}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ShapePanel({ q }: { q: any }) {
  const a = q.analysis;
  const shape = q.result.shape;
  const statusText = !a ? "形状ファイルなし" : a.status === "success" ? "確定" : a.status === "partial" ? "概算" : "解析不可";
  return (
    <div className="stack">
      <div className="row"><b>形状解析：</b><span className={a?.status === "success" ? "tag soft" : "tag solid"}>{statusText}</span>
        <span className="small muted">{q.drawing?.shape_name}</span></div>
      {a?.message && <div className="small">{a.message}</div>}
      {(a?.assumptions || []).length > 0 && <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{a.assumptions.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>}
      <table className="rule">
        <tbody>
          {SHAPE.map(([k, label, unit]) => (
            <tr key={k}><td>{label}</td><td className="right">{shape[k].value === null ? <span className="warn">未入力</span> : `${num(shape[k].value, 2)} ${unit}`}</td>
              <td className="small muted">{shape[k].source === "input" ? "入力値" : shape[k].source === "analysis" ? "解析" : ""}</td></tr>
          ))}
        </tbody>
      </table>
      {q.drawing?.shape_kind === "step" && <Model3D fileId={q.drawing.shape_file_id} height={240} />}
      {a?.flat_pattern && (<><span className="small muted">展開図（赤い破線は曲げ線）</span><FlatPattern flat={a.flat_pattern} height={200} /></>)}
    </div>
  );
}

function Chat({ q, onDone, onError }: { q: any; onDone: (d: any) => void; onError: (e: any) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  async function send() {
    setBusy(true);
    try {
      onDone(await post(`/api/estimates/${q.id}/chat`, { message: text, version: q.version }));
      setText("");
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="stack">
      <p className="small muted" style={{ margin: 0 }}>例：「皿もみを4か所追加」「数量を200に」。文章から条件を読み取り、金額はマスターとルールで計算し直します。</p>
      <div className="chat">
        {(q.chat || []).length === 0 && <span className="small muted">まだやりとりはありません。</span>}
        {(q.chat || []).map((m: any, i: number) => <div key={i} className={m.role === "user" ? "u" : "a"}><span>{m.content}</span></div>)}
      </div>
      <div className="row">
        <input aria-label="チャット" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && text.trim() && send()} placeholder="条件の変更を文章で" style={{ flex: 1, width: "auto" }} />
        <button className="btn" disabled={busy || !text.trim()} onClick={send}>送る</button>
      </div>
    </div>
  );
}

function Conditions({ q, onSave }: { q: any; onSave: (inputs: any, extra?: any) => Promise<boolean> }) {
  const { meta } = useMeta();
  const [f, setF] = useState<any>(null);
  useEffect(() => setF(JSON.parse(JSON.stringify({ ...q.inputs, due_date: q.inputs.due_date || "" }))), [q]);
  if (!f) return null;
  const set = (k: string, v: any) => setF((x: any) => ({ ...x, [k]: v }));
  const missingField = (name: string) => q.result.missing.some((m: any) => m.field === name || m.field.startsWith(name + "."));
  const shape = q.result.shape;
  const shapeNeeded = SHAPE.filter(([k]) => shape[k].source !== "analysis");
  const kind = q.drawing?.shape_kind;
  const materialValue = f.material || (f.custom_material ? "__custom" : "");
  const finishValue = f.custom_finish && !f.surface_treatment ? "__custom" : f.surface_treatment || "NONE";

  function submit() {
    const inputs: any = { ...f, due_date: f.due_date || null, quantity: f.quantity ? Number(f.quantity) : null };
    inputs.shape = Object.fromEntries(SHAPE.map(([k]) => [k, f.shape?.[k] === "" || f.shape?.[k] === undefined || f.shape?.[k] === null ? null : Number(f.shape[k])]));
    inputs.processes = (f.processes || []).map((p: any) => ({ ...p, quantity: p.quantity ? Number(p.quantity) : null }));
    inputs.custom_processes = (f.custom_processes || []).map((p: any) => ({ ...p, quantity: p.quantity ? Number(p.quantity) : null, unit_price: p.unit_price === "" || p.unit_price === null ? null : Number(p.unit_price) }));
    if (inputs.custom_material) inputs.custom_material = { ...inputs.custom_material, price_per_kg: num0(inputs.custom_material.price_per_kg), density_kg_m3: num0(inputs.custom_material.density_kg_m3) };
    if (inputs.custom_finish) inputs.custom_finish = { ...inputs.custom_finish, unit_price: num0(inputs.custom_finish.unit_price) };
    inputs.k_factor = Number(inputs.k_factor);
    inputs.thickness_mm = inputs.thickness_mm ? Number(inputs.thickness_mm) : null;
    const { customer, title, staff, due_date, ...rest } = inputs;
    onSave(rest, { customer, title, staff, due_date });
  }

  return (
    <section id="conditions" className="panel" style={{ marginTop: 24 }}>
      <div className="row between"><h2>見積の条件</h2><span className="small muted">保存すると、サーバーで計算し直します</span></div>
      <div className="grid3" style={{ marginTop: 12 }}>
        <div className="field"><label>顧客</label><input aria-label="顧客" value={f.customer} onChange={(e) => set("customer", e.target.value)} /></div>
        <div className="field"><label>案件名</label><input aria-label="案件名" value={f.title} onChange={(e) => set("title", e.target.value)} /></div>
        <div className="field"><label>担当</label>
          <select aria-label="担当" value={f.staff} onChange={(e) => set("staff", e.target.value)}>
            <option value="">（未設定）</option>{(meta?.staff || []).map((s) => <option key={s.id}>{s.name}</option>)}
          </select></div>
        <div className="field"><label>数量（必須）</label><input aria-label="数量" type="number" min={1} className={missingField("quantity") ? "missing" : ""} value={f.quantity ?? ""} onChange={(e) => set("quantity", e.target.value)} /></div>
        <div className="field"><label>希望納期（金額に影響しません）</label><input aria-label="希望納期" type="date" value={f.due_date} onChange={(e) => set("due_date", e.target.value)} /></div>
        <label className="check" style={{ marginTop: 18 }}><input type="checkbox" checked={f.rush} onChange={(e) => set("rush", e.target.checked)} />特急（割増 {Math.round((meta?.policy.rush_surcharge_rate || 0) * 100)}%）</label>
        <div className="field"><label>材質（必須）</label>
          <select aria-label="材質" className={missingField("material") || missingField("custom_material") ? "missing" : ""} value={materialValue}
            onChange={(e) => e.target.value === "__custom" ? setF({ ...f, material: null, custom_material: f.custom_material || { name: "", price_per_kg: null, density_kg_m3: null } })
              : setF({ ...f, material: e.target.value || null, custom_material: null })}>
            <option value="">未選択</option>
            {(meta?.materials || []).map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}
            <option value="__custom">マスターにない材質（単価を入力）</option>
          </select></div>
        <div className="field"><label>表面処理</label>
          <select aria-label="表面処理" className={missingField("custom_finish") ? "missing" : ""} value={finishValue}
            onChange={(e) => e.target.value === "__custom" ? setF({ ...f, surface_treatment: null, custom_finish: f.custom_finish || { name: "", unit_price: null } })
              : setF({ ...f, surface_treatment: e.target.value, custom_finish: null })}>
            {(meta?.surface_treatments || []).map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}
            <option value="__custom">マスターにない表面処理（単価を入力）</option>
          </select></div>
      </div>
      {materialValue === "__custom" && (
        <div className="grid3">
          <div className="field"><label>材質の名前</label><input aria-label="材質の名前" value={f.custom_material.name} onChange={(e) => set("custom_material", { ...f.custom_material, name: e.target.value })} /></div>
          <div className="field"><label>kg単価（円、歩留まり1.15を掛けます）</label><input aria-label="kg単価" type="number" className={f.custom_material.price_per_kg ? "" : "missing"} value={f.custom_material.price_per_kg ?? ""} onChange={(e) => set("custom_material", { ...f.custom_material, price_per_kg: e.target.value })} /></div>
          <div className="field"><label>密度（kg/m³）</label><input aria-label="密度" type="number" className={f.custom_material.density_kg_m3 ? "" : "missing"} value={f.custom_material.density_kg_m3 ?? ""} onChange={(e) => set("custom_material", { ...f.custom_material, density_kg_m3: e.target.value })} /></div>
        </div>
      )}
      {finishValue === "__custom" && (
        <div className="grid3">
          <div className="field"><label>表面処理の名前</label><input aria-label="表面処理の名前" value={f.custom_finish.name} onChange={(e) => set("custom_finish", { ...f.custom_finish, name: e.target.value })} /></div>
          <div className="field"><label>単価（円／個）</label><input aria-label="表面処理の単価" type="number" className={f.custom_finish.unit_price === null || f.custom_finish.unit_price === "" ? "missing" : ""} value={f.custom_finish.unit_price ?? ""} onChange={(e) => set("custom_finish", { ...f.custom_finish, unit_price: e.target.value })} /></div>
        </div>
      )}

      <h3 style={{ margin: "10px 0 6px" }}>追加加工</h3>
      <table className="rule">
        <thead><tr><th>加工</th><th style={{ width: 120 }}>1個あたりの箇所数</th><th style={{ width: 150 }}>単価（円／か所）</th><th /></tr></thead>
        <tbody>
          {(f.processes || []).map((p: any, i: number) => (
            <tr key={`p${i}`}>
              <td><select aria-label="追加加工" value={p.code} onChange={(e) => { const ps = [...f.processes]; ps[i] = { ...p, code: e.target.value }; set("processes", ps); }}>
                {(meta?.processes || []).map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}</select></td>
              <td><input aria-label="箇所数" type="number" min={0} className={p.quantity ? "" : "missing"} value={p.quantity ?? ""} onChange={(e) => { const ps = [...f.processes]; ps[i] = { ...p, quantity: e.target.value }; set("processes", ps); }} /></td>
              <td className="small muted">マスターの単価</td>
              <td><button className="btn link" onClick={() => set("processes", f.processes.filter((_: any, j: number) => j !== i))}>外す</button></td>
            </tr>
          ))}
          {(f.custom_processes || []).map((p: any, i: number) => (
            <tr key={`c${i}`}>
              <td><input aria-label="未登録の加工の名前" value={p.name} onChange={(e) => { const ps = [...f.custom_processes]; ps[i] = { ...p, name: e.target.value }; set("custom_processes", ps); }} />
                <span className="sub">マスター未登録（この見積だけの単価）</span></td>
              <td><input aria-label="未登録の加工の箇所数" type="number" min={0} className={p.quantity ? "" : "missing"} value={p.quantity ?? ""} onChange={(e) => { const ps = [...f.custom_processes]; ps[i] = { ...p, quantity: e.target.value }; set("custom_processes", ps); }} /></td>
              <td><input aria-label="未登録の加工の単価" type="number" min={0} className={p.unit_price === null || p.unit_price === "" || p.unit_price === undefined ? "missing" : ""} value={p.unit_price ?? ""} onChange={(e) => { const ps = [...f.custom_processes]; ps[i] = { ...p, unit_price: e.target.value }; set("custom_processes", ps); }} /></td>
              <td><button className="btn link" onClick={() => set("custom_processes", f.custom_processes.filter((_: any, j: number) => j !== i))}>外す</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row" style={{ marginTop: 6 }}>
        <button className="btn small" onClick={() => set("processes", [...(f.processes || []), { code: meta?.processes[0]?.code, quantity: 1, source: "user" }])}>＋ マスターの加工</button>
        <button className="btn small" onClick={() => set("custom_processes", [...(f.custom_processes || []), { name: "", quantity: 1, unit_price: null, source: "user" }])}>＋ マスターにない加工</button>
      </div>

      {shapeNeeded.length > 0 && (
        <>
          <h3 style={{ margin: "16px 0 6px" }}>形状の値（形状解析で求められなかった値を入力）</h3>
          <div className="grid3">
            {shapeNeeded.map(([k, label, unit]) => (
              <div className="field" key={k}><label>{label}{unit && `（${unit}）`}</label>
                <input aria-label={`形状 ${label}`} type="number" min={0} className={shape[k].value === null ? "missing" : ""} value={f.shape?.[k] ?? ""}
                  onChange={(e) => set("shape", { ...(f.shape || {}), [k]: e.target.value })} /></div>
            ))}
          </div>
        </>
      )}
      {kind === "step" && (
        <div className="row" style={{ marginTop: 10 }}>
          <div className="field" style={{ width: 160 }}><label>Kファクター</label><input type="number" step="0.01" min={0} max={1} value={f.k_factor} onChange={(e) => set("k_factor", e.target.value)} /></div>
          <label className="check"><input type="checkbox" checked={f.k_factor_confirmed} onChange={(e) => set("k_factor_confirmed", e.target.checked)} />指定済み加工条件として扱う</label>
        </div>
      )}
      {kind === "dxf" && (
        <div className="row" style={{ marginTop: 10 }}>
          <div className="field" style={{ width: 160 }}><label>板厚 mm（DXF）</label><input aria-label="DXFの板厚" type="number" step="0.1" value={f.thickness_mm ?? ""} onChange={(e) => set("thickness_mm", e.target.value)} /></div>
          <label className="check"><input type="checkbox" checked={f.flat_confirmed} onChange={(e) => set("flat_confirmed", e.target.checked)} />曲げなし（平板）</label>
        </div>
      )}
      <div className="row" style={{ marginTop: 14 }}>
        <button className="btn primary" onClick={submit}>保存して計算し直す</button>
        <span className="small muted">ほかの人が先に保存していた場合は、上書きせずにお知らせします。</span>
      </div>
    </section>
  );
}

function num0(v: any) {
  return v === "" || v === null || v === undefined ? null : Number(v);
}
