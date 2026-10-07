import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ApiError, blobUrl, day, get, num, post, put, stamp, yen } from "../api";
import { actionHref, ErrorBox, Field, Flow, LineTabs, Loading, Modal, StatusTag, useMeta, useToast } from "../ui";
import { FlatPattern, Model3D } from "../viewers";

const SHAPE: [string, string, string][] = [
  ["thickness_mm", "板厚", "mm"], ["blank_area_mm2", "展開面積", "mm²"], ["cut_length_mm", "切断長", "mm"], ["hole_count", "穴数", ""], ["bend_count", "曲げ数", ""],
];

const TABS = ["cost", "conditions", "shape", "similar", "history"] as const;
type Tab = (typeof TABS)[number];
const FLOW = [{ label: "図面" }, { label: "条件" }, { label: "解析・計算" }, { label: "内容確認" }, { label: "見積書発行", note: "次" }, { label: "受注・失注の登録", note: "回答後" }];
const DOC_LABEL: Record<string, string> = { quote: "見積書", delivery: "納品書", invoice: "請求書" };

/** Where a missing item or a hint is changed: [tab, element id]. */
function placeOf(field: string | null): [Tab, string] | null {
  if (!field) return null;
  if (field === "quantity") return ["conditions", "c-quantity"];
  if (field === "material") return ["conditions", "c-material"];
  if (field === "custom_material") return ["conditions", "c-custom-material"];
  if (field === "surface_treatment") return ["conditions", "c-finish"];
  if (field === "custom_finish") return ["conditions", "c-custom-finish"];
  if (field === "rush") return ["conditions", "c-rush"];
  if (field === "k_factor") return ["conditions", "c-kfactor"];
  if (field.startsWith("processes")) return ["conditions", `c-processes${field.includes(".") ? "-" + field.split(".")[1] : ""}`];
  if (field.startsWith("custom_processes")) return ["conditions", `c-custom-${field.split(".")[1] || "0"}`];
  if (field.startsWith("shape.")) return ["conditions", `c-shape-${field.split(".")[1]}`];
  if (field === "shape" || field === "thickness_mm") return ["shape", "shape-panel"];
  return null;
}

export default function EstimatePage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState<any>(null);
  const [error, setError] = useState<any>(null);
  const [customerMode, setCustomerMode] = useState(false);
  const initial = params.get("tab") as Tab;
  const [tab, setTabState] = useState<Tab>(TABS.includes(initial) ? initial : "cost");
  const setTab = (t: Tab) => { setTabState(t); setParams(t === "cost" ? {} : { tab: t }, { replace: true }); };

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
  useEffect(() => {
    if (q && location.hash === "#outcome") document.getElementById("next")?.scrollIntoView({ block: "start" });
  }, [q, location.hash]);

  function goTo(field: string | null) {
    const place = placeOf(field);
    if (!place) return;
    setTab(place[0]);
    setTimeout(() => {
      const el = document.getElementById(place[1]);
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
      (el as HTMLInputElement | null)?.focus?.();
    }, 60);
  }

  async function save(inputs: any, extra: any = {}) {
    try {
      const d = await put(`/api/estimates/${id}`, { version: q.version, inputs, ...extra });
      setQ(d);
      setError(null);
      toast("条件を保存し、計算し直しました");
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.code === "CONFLICT") {
        toast(e.message);
        await load();
      } else setError(e);
      return false;
    }
  }

  if (!q) return error ? <ErrorBox error={error} /> : <Loading />;
  const r = q.result;
  const price = r.price;
  const inp = q.inputs;
  const shape = r.shape;
  const tabs: { value: Tab; label: string }[] = customerMode ? [{ value: "cost", label: "金額" }] : [
    { value: "cost", label: "金額内訳" }, { value: "conditions", label: "条件修正" }, { value: "shape", label: "CADデータ" },
    { value: "similar", label: `類似実績 ${q.similar.length}` }, { value: "history", label: "修正履歴" },
  ];
  const shownTab = customerMode ? "cost" : tab;
  return (
    <div>
      <Link to="/cases" className="back">← 見積・案件</Link>
      <div className="page-head" style={{ marginBottom: 6 }}>
        <div>
          <h1>
            {q.drawing?.name || q.title}
            <span className="num">{q.drawing?.drawing_no} {q.drawing?.revision ? `Rev.${q.drawing.revision}` : ""}</span>
          </h1>
          <div className="small muted">
            {q.number}　{q.customer}　{materialText(inp)} {shape.thickness_mm.value ? `t${num(shape.thickness_mm.value, 2)}` : ""}　{inp.quantity ?? "—"}個　希望納期 {day(q.due_date)}　担当 {q.staff || "—"}
          </div>
        </div>
        <div className="row">
          <StatusTag name={q.status.name} color={q.status.color} />
          {q.job && (q.job.status === "queued" || q.job.status === "running") && <span className="tag">解析中…</span>}
        </div>
      </div>
      <Flow steps={FLOW} now={q.flow_step} />
      <ErrorBox error={error} />

      <div className="est">
        <div style={{ minWidth: 0 }}>
          {!customerMode && <NextAction q={q} goTo={goTo} onChanged={load} onError={setError} />}

          <section className="block" data-testid="shape-views">
            <ShapeViews q={q} />
            <div className="shape-line">
              <span className={q.analysis?.state === "確定" ? "tag soft" : "tag solid"}>{q.analysis ? q.analysis.state : "CADデータなし"}</span>
              <span>板厚 <b>{shape.thickness_mm.value === null ? "—" : num(shape.thickness_mm.value, 2)}</b> mm</span>
              <span>穴 <b>{shape.hole_count.value ?? "—"}</b></span>
              <span>曲げ <b>{shape.bend_count.value ?? "—"}</b></span>
              <span>切断長 <b>{shape.cut_length_mm.value === null ? "—" : num(shape.cut_length_mm.value, 1)}</b> mm<span className="small muted">（1個あたり）</span></span>
              <span className="spacer" />
              {q.drawing?.pdf_file_id && <PdfLink fileId={q.drawing.pdf_file_id} />}
            </div>
          </section>

          <LineTabs value={shownTab} onChange={setTab} options={tabs} />
          {shownTab === "cost" && (!customerMode ? <CostTable r={r} inp={inp} /> : <CustomerTable q={q} />)}
          {shownTab === "conditions" && (
            <>
              <DrawingItems items={r.items} />
              <Conditions q={q} onSave={save} />
            </>
          )}
          {shownTab === "shape" && <ShapePanel q={q} />}
          {shownTab === "similar" && <SimilarList items={q.similar} rates={q.similar_compare?.rates || []} />}
          {shownTab === "history" && <History q={q} />}
        </div>

        <aside>
          <div className="totals">
            <div className="label">総額（税抜）・{inp.quantity ?? "—"}個</div>
            <div className="big" data-testid="total">{price ? yen(price.subtotal) : "未計算"}</div>
            {price ? (
              <>
                <div className="kv"><span>単価</span><b>{yen(price.unit_price)}</b></div>
                <div className="kv"><span>消費税（{Math.round(price.tax_rate * 100)}%）</span><span>{yen(price.tax)}</span></div>
                <div className="kv"><span>税込</span><b>{yen(price.total)}</b></div>
              </>
            ) : <div className="small muted">未入力の項目を入力すると計算します</div>}
          </div>
          {!customerMode && (
            <div className="side-box">
              <h3>類似実績との比較</h3>
              <Compare c={q.similar_compare} />
              {q.similar.length > 0 && <button className="btn link small" style={{ padding: 0 }} onClick={() => setTab("similar")}>類似実績 {q.similar.length}件を表示 →</button>}
            </div>
          )}
          <label className="check side-box" style={{ display: "flex" }}>
            <input type="checkbox" checked={customerMode} onChange={(e) => setCustomerMode(e.target.checked)} aria-label="顧客提示モード" />
            <span><b>顧客提示モード</b><span className="small muted" style={{ display: "block" }}>原価・粗利を非表示</span></span>
          </label>
          {!customerMode && (
            <div className="side-box">
              <h3>チャットで条件変更</h3>
              <Chat q={q} onDone={(d) => { setQ(d); setError(null); }} onError={setError} />
            </div>
          )}
          {!customerMode && q.documents.length > 0 && (
            <div className="side-box">
              <h3>発行済みの帳票</h3>
              {q.documents.map((d: any) => (
                <div key={d.id} className="row small" style={{ borderTop: "1px solid var(--line)", padding: "5px 0" }}>
                  <span className="tag plain">{DOC_LABEL[d.kind]}</span>
                  <DocLink url={d.url} label={d.number} />
                  <span className="spacer" />{yen(d.total)}
                </div>
              ))}
            </div>
          )}
          {!customerMode && <button className="btn" onClick={() => navigate(`/documents?quote=${q.id}`)}>帳票発行へ</button>}
        </aside>
      </div>
    </div>
  );
}

/** 次のアクション: what the API says is next (input the missing items / check and issue / record the outcome /
 * issue the delivery note and invoice), with a link from each item to the place to change it. */
function NextAction({ q, goTo, onChanged, onError }: { q: any; goTo: (f: string | null) => void; onChanged: () => void; onError: (e: any) => void }) {
  const { meta } = useMeta();
  const navigate = useNavigate();
  const toast = useToast();
  const [lost, setLost] = useState<any>(null);
  const r = q.result;
  const a = q.next_action;
  async function outcome(role: "won" | "lost", extra: any = {}) {
    const st = q.outcome_statuses[role];
    if (!st) return;
    try {
      await put(`/api/cases/${q.case_id}/status`, { status_id: st.id, version: q.case_version, ...extra });
      toast(`${q.number} を「${st.name}」にしました`);
      setLost(null);
      onChanged();
    } catch (e) {
      onError(e);
    }
  }
  if (a.kind === "progress") {
    return <div className="next" id="next"><h2>解析・計算中です</h2><p className="small muted" style={{ margin: 0 }}>完了すると金額を表示します。</p></div>;
  }
  if (r.missing.length > 0) {
    return (
      <div className="next warnbox" id="next" data-testid="missing">
        <h2>次のアクション：未入力の項目 {r.missing.length}件を入力</h2>
        <ul>
          {r.missing.map((m: any, i: number) => (
            <li key={i}>
              <span className="reason missing">未入力</span>
              <span className="msg">{m.message}</span>
              {placeOf(m.field) && <button className="btn link small" onClick={() => goTo(m.field)}>入力 →</button>}
            </li>
          ))}
        </ul>
        <div className="foot"><span className="small muted">すべて入力すると金額を計算し、見積書を発行できます</span>
          <button className="btn primary" disabled title="未入力の項目があります">見積書の発行へ →</button></div>
      </div>
    );
  }
  const hints = r.hint_items || [];
  const hintList = hints.length > 0 && (
    <ul>
      {hints.map((h: any, i: number) => (
        <li key={i}>
          <span className="reason due_soon">確認</span>
          <span className="msg">{h.message}</span>
          {placeOf(h.field) && <button className="btn link small" onClick={() => goTo(h.field)}>{h.label}を修正 →</button>}
        </li>
      ))}
    </ul>
  );
  if (a.kind === "outcome") {
    return (
      <div className="next" id="next" data-testid="next-outcome">
        <h2>次のアクション：受注・失注の登録</h2>
        <p className="small muted" style={{ margin: "0 0 10px" }}>見積書は発行済みです（{q.status.name}）。顧客の回答を登録します。</p>
        <div className="row">
          <button className="btn primary" disabled={!q.outcome_statuses.won} onClick={() => outcome("won")}>受注を登録</button>
          <button className="btn" disabled={!q.outcome_statuses.lost} onClick={() => setLost({ reason: "", price: "", note: "" })}>失注を登録</button>
          <span className="spacer" />
          <Link to={`/documents?quote=${q.id}&kind=quote`} className="small">見積書を再発行</Link>
        </div>
        {lost && (
          <Modal title="失注を記録" onClose={() => setLost(null)}>
            <p className="small muted">{q.number} {q.drawing?.name}（{q.customer}）。理由と他社価格は実績分析に使います。</p>
            <Field label="失注の理由（必須）">
              <select aria-label="失注の理由" value={lost.reason} onChange={(e) => setLost({ ...lost, reason: e.target.value })}>
                <option value="">選択してください</option>
                {(meta?.lost_reasons || []).map((x) => <option key={x}>{x}</option>)}
              </select>
            </Field>
            <Field label="他社価格（判明していれば、税抜の合計）"><input aria-label="他社価格" type="number" value={lost.price} onChange={(e) => setLost({ ...lost, price: e.target.value })} /></Field>
            <Field label="メモ"><input value={lost.note} onChange={(e) => setLost({ ...lost, note: e.target.value })} /></Field>
            <div className="row">
              <button className="btn primary" disabled={!lost.reason} onClick={() => outcome("lost", { lost_reason: lost.reason, competitor_price: lost.price ? Number(lost.price) : null, lost_note: lost.note })}>失注を登録</button>
              <button className="btn" onClick={() => setLost(null)}>キャンセル</button>
            </div>
          </Modal>
        )}
      </div>
    );
  }
  if (a.kind === "documents") {
    return (
      <div className="next" id="next">
        <h2>次のアクション：{a.label}</h2>
        <p className="small muted" style={{ margin: "0 0 10px" }}>受注済み（{q.status.name}）。見積書と同じ金額で作成します。</p>
        <button className="btn primary" onClick={() => navigate(actionHref(a))}>{a.label}へ →</button>
      </div>
    );
  }
  if (a.kind === "issue" || (a.kind === "estimate" && ["drafting", "checking"].includes(q.phase))) {
    const issued = q.documents.some((d: any) => d.kind === "quote");
    return (
      <div className="next" id="next" data-testid="next-issue">
        <h2>次のアクション：{hints.length ? `確認事項 ${hints.length}件を確認のうえ、` : ""}見積書を{issued ? "再発行" : "発行"}</h2>
        {hintList}
        <div className="foot">
          <span className="small muted">{hints.length ? "いずれも未対応のまま発行可能。" : ""}未入力項目なし</span>
          <button className="btn primary" onClick={() => navigate(`/documents?quote=${q.id}&kind=quote`)}>見積書の発行へ →</button>
        </div>
      </div>
    );
  }
  return (
    <div className="next" id="next">
      <h2>{q.phase === "closed" ? `この案件は「${q.status.name}」です` : `ステータス：${q.status.name}`}</h2>
      {a.kind === "new_estimate" ? <Link to={actionHref(a)}>{a.label} →</Link>
        : ["delivery", "invoice"].every((k) => q.documents.some((d: any) => d.kind === k)) ? <span className="small muted">納品書・請求書は発行済みです。</span> : null}
    </div>
  );
}

function Compare({ c }: { c: any }) {
  if (!c || !c.item) return <div className="small muted">比較できる類似実績（単価あり）はありません。</div>;
  const it = c.item;
  const rate = c.rate === null || c.rate === undefined ? "" : `今回は ${c.rate >= 0 ? "+" : ""}${Math.round(c.rate * 100)}%`;
  return (
    <div className="small" data-testid="similar-compare">
      {c.same_drawing ? "同一図番の前回実績" : "最も近い類似実績"}（{day(it.date)}・{it.quantity ?? "—"}個・{it.status}）：単価 {yen(it.unit_price)}。{rate}
      <div className="muted">{it.drawing_no} {it.name} ・ {it.customer} ・ {it.difference}</div>
    </div>
  );
}

function PdfLink({ fileId }: { fileId: string }) {
  return <button className="btn link small" onClick={async () => window.open(await blobUrl(`/api/files/${fileId}/raw`), "_blank")}>図面PDFを表示</button>;
}

function DocLink({ url, label }: { url: string; label: string }) {
  return <button className="btn link small" style={{ padding: 0 }} onClick={async () => window.open(await blobUrl(url), "_blank")}>{label}</button>;
}

function CostTable({ r, inp }: { r: any; inp: any }) {
  const price = r.price;
  return (
    <>
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
              <tr><td className="muted small">小計</td><td>原価合計{inp.rush ? "（特急割増を含む）" : ""}</td><td /><td /><td className="right">{yen(r.subtotal_cost)}</td></tr>
              <tr><td className="muted small">粗利</td><td>小計 × 粗利率</td><td className="right">{Math.round(r.margin_rate * 100)}%</td><td /><td className="right">{yen(r.margin)}</td></tr>
              <tr className="total"><td>合計</td><td>数量 {price.quantity}個（単価は1円未満切り上げ）</td><td /><td className="right">{yen(price.unit_price)}</td><td className="right">{yen(price.subtotal)}</td></tr>
              <tr><td /><td>消費税（{Math.round(price.tax_rate * 100)}%、1円未満切り捨て）</td><td /><td /><td className="right">{yen(price.tax)}</td></tr>
              <tr><td /><td><b>合計（税込）</b></td><td /><td /><td className="right"><b>{yen(price.total)}</b></td></tr>
            </>
          )}
          {!price && <tr><td colSpan={5} className="empty">未入力の項目があるため、金額は未計算です。</td></tr>}
        </tbody>
      </table>
      <p className="small"><Link to="/settings/logic">計算式を表示</Link></p>
    </>
  );
}

function CustomerTable({ q }: { q: any }) {
  const price = q.result.price;
  const inp = q.inputs;
  return (
    <>
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
  );
}

function DrawingItems({ items }: { items: any[] }) {
  return (
    <section className="block">
      <h3 style={{ marginBottom: 6 }}>図面の読み取り結果</h3>
      {items.length === 0 ? <div className="small muted">図面の読み取り結果はありません。</div> : (
        <table className="rule" data-testid="drawing-items">
          <thead><tr><th>項目</th><th>状態</th><th>図面の値</th><th>見積に使う値</th></tr></thead>
          <tbody>
            {items.map((it: any) => (
              <tr key={it.field}>
                <td>{it.label}</td>
                <td><span className={it.status === "確定" ? "tag soft" : it.status === "要確認" ? "tag solid" : "tag grey"}>{it.status_label}</span></td>
                <td>{it.read}{it.reasons.length > 0 && <span className="sub">{it.reasons.join("・")}</span>}</td>
                <td>{it.used}{it.notice && <span className="sub warn">⚠ {it.notice}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function History({ q }: { q: any }) {
  return (
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

function SimilarList({ items, rates }: { items: any[]; rates: (number | null)[] }) {
  if (!items.length) return <div className="small muted">材料が同じで、曲げ数の差が1以内・穴数の差が2以内の実績はありません。</div>;
  return (
    <div data-testid="similar">
      <table className="rule">
        <thead><tr><th>図番</th><th>差</th><th className="right">数量</th><th className="right">単価</th><th className="right">今回との差</th><th>結果</th></tr></thead>
        <tbody>
          {items.map((s, i) => (
            <tr key={i}>
              <td>{s.drawing_id ? <Link to={`/drawings/${s.drawing_id}`}>{s.drawing_no}</Link> : s.drawing_no || s.quote_no}
                <span className="sub">{s.name} ・ {s.customer} ・ {day(s.date)}</span></td>
              <td className="small">{s.difference}</td>
              <td className="right">{s.quantity ?? "—"}</td>
              <td className="right">{s.unit_price ? yen(s.unit_price) : "—"}</td>
              <td className="right small">{rates[i] === null || rates[i] === undefined ? "—" : `今回 ${rates[i]! >= 0 ? "+" : ""}${Math.round(rates[i]! * 100)}%`}</td>
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
  const statusText = !a ? "CADデータなし" : a.state;
  return (
    <div className="stack" id="shape-panel" tabIndex={-1}>
      <div className="row"><span className={a?.state === "確定" ? "tag soft" : "tag solid"}>{statusText}</span>
        <span className="small muted">{q.drawing?.shape_name}</span></div>
      {a?.failure && <div className="small">{a.failure}。「条件修正」で寸法の値を入力してください。</div>}
      {(a?.notes || []).length > 0 && <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{a.notes.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>}
      <table className="rule">
        <tbody>
          {SHAPE.map(([k, label, unit]) => (
            <tr key={k}><td>{label}</td><td className="right">{shape[k].value === null ? <span className="warn">未入力</span> : `${num(shape[k].value, 2)} ${unit}`}</td>
              <td className="small muted">{shape[k].source === "input" ? "入力値" : ""}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ShapeViews({ q }: { q: any }) {
  const has3d = q.drawing?.shape_kind === "step" && !!q.drawing.shape_file_id;
  const flat = q.analysis?.flat_pattern;
  return (
    <div className="shapes">
      <div>
        <div className="row between"><h3>形状（3D）</h3><span className="small muted">{has3d ? "ドラッグで回転・ホイールで拡大" : ""}</span></div>
        {has3d ? <Model3D fileId={q.drawing.shape_file_id} height={280} /> : (
          <div className="hatch" style={{ height: 280, display: "grid", placeItems: "center" }}><span className="muted small">{q.drawing?.shape_kind === "dxf" ? "展開図（DXF）のため3D表示なし" : "CADデータなし"}</span></div>
        )}
      </div>
      <div>
        <div className="row between"><h3>展開図（2D）</h3><span className="small muted">{flat ? "赤い破線は曲げ線" : ""}</span></div>
        {flat ? <FlatPattern flat={flat} height={280} /> : (
          <div className="hatch" style={{ height: 280, display: "grid", placeItems: "center" }}><span className="muted small">展開図なし</span></div>
        )}
      </div>
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
      <p className="small muted" style={{ margin: 0 }}>例：「皿もみを4か所追加」「数量を200に」</p>
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
    <section id="conditions" className="panel">
      <h2>条件修正</h2>
      <div className="grid3" style={{ marginTop: 12 }}>
        <div className="field"><label>顧客</label><input aria-label="顧客" value={f.customer} onChange={(e) => set("customer", e.target.value)} /></div>
        <div className="field"><label>案件名</label><input aria-label="案件名" value={f.title} onChange={(e) => set("title", e.target.value)} /></div>
        <div className="field"><label>担当</label>
          <select aria-label="担当" value={f.staff} onChange={(e) => set("staff", e.target.value)}>
            <option value="">（未設定）</option>{(meta?.staff || []).map((s) => <option key={s.id}>{s.name}</option>)}
          </select></div>
        <div className="field"><label>数量（必須）</label><input id="c-quantity" aria-label="数量" type="number" min={1} className={missingField("quantity") ? "missing" : ""} value={f.quantity ?? ""} onChange={(e) => set("quantity", e.target.value)} /></div>
        <div className="field"><label>希望納期</label><input aria-label="希望納期" type="date" value={f.due_date} onChange={(e) => set("due_date", e.target.value)} /></div>
        <label className="check" style={{ marginTop: 18 }}><input id="c-rush" type="checkbox" checked={f.rush} onChange={(e) => set("rush", e.target.checked)} />特急（割増 {Math.round((meta?.policy.rush_surcharge_rate || 0) * 100)}%）</label>
        <div className="field"><label>材質（必須）</label>
          <select id="c-material" aria-label="材質" className={missingField("material") || missingField("custom_material") ? "missing" : ""} value={materialValue}
            onChange={(e) => e.target.value === "__custom" ? setF({ ...f, material: null, custom_material: f.custom_material || { name: "", price_per_kg: null, density_kg_m3: null } })
              : setF({ ...f, material: e.target.value || null, custom_material: null })}>
            <option value="">未選択</option>
            {(meta?.materials || []).map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}
            <option value="__custom">マスタにない材質（単価を入力）</option>
          </select></div>
        <div className="field"><label>表面処理</label>
          <select id="c-finish" aria-label="表面処理" className={missingField("custom_finish") ? "missing" : ""} value={finishValue}
            onChange={(e) => e.target.value === "__custom" ? setF({ ...f, surface_treatment: null, custom_finish: f.custom_finish || { name: "", unit_price: null } })
              : setF({ ...f, surface_treatment: e.target.value, custom_finish: null })}>
            {(meta?.surface_treatments || []).map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}
            <option value="__custom">マスタにない表面処理（単価を入力）</option>
          </select></div>
      </div>
      {materialValue === "__custom" && (
        <div className="grid3">
          <div className="field"><label>材質の名前</label><input aria-label="材質の名前" value={f.custom_material.name} onChange={(e) => set("custom_material", { ...f.custom_material, name: e.target.value })} /></div>
          <div className="field"><label>kg単価（円）</label><input id="c-custom-material" aria-label="kg単価" type="number" className={f.custom_material.price_per_kg ? "" : "missing"} value={f.custom_material.price_per_kg ?? ""} onChange={(e) => set("custom_material", { ...f.custom_material, price_per_kg: e.target.value })} /></div>
          <div className="field"><label>密度（kg/m³）</label><input aria-label="密度" type="number" className={f.custom_material.density_kg_m3 ? "" : "missing"} value={f.custom_material.density_kg_m3 ?? ""} onChange={(e) => set("custom_material", { ...f.custom_material, density_kg_m3: e.target.value })} /></div>
        </div>
      )}
      {finishValue === "__custom" && (
        <div className="grid3">
          <div className="field"><label>表面処理の名前</label><input aria-label="表面処理の名前" value={f.custom_finish.name} onChange={(e) => set("custom_finish", { ...f.custom_finish, name: e.target.value })} /></div>
          <div className="field"><label>単価（円／個）</label><input id="c-custom-finish" aria-label="表面処理の単価" type="number" className={f.custom_finish.unit_price === null || f.custom_finish.unit_price === "" ? "missing" : ""} value={f.custom_finish.unit_price ?? ""} onChange={(e) => set("custom_finish", { ...f.custom_finish, unit_price: e.target.value })} /></div>
        </div>
      )}

      <h3 id="c-processes" tabIndex={-1} style={{ margin: "10px 0 6px" }}>追加加工</h3>
      <table className="rule">
        <thead><tr><th>加工</th><th style={{ width: 120 }}>1個あたりの箇所数</th><th style={{ width: 150 }}>単価（円／か所）</th><th /></tr></thead>
        <tbody>
          {(f.processes || []).map((p: any, i: number) => (
            <tr key={`p${i}`}>
              <td><select aria-label="追加加工" value={p.code} onChange={(e) => { const ps = [...f.processes]; ps[i] = { ...p, code: e.target.value }; set("processes", ps); }}>
                {(meta?.processes || []).map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}</select></td>
              <td><input id={`c-processes-${i}`} aria-label="箇所数" type="number" min={0} className={p.quantity ? "" : "missing"} value={p.quantity ?? ""} onChange={(e) => { const ps = [...f.processes]; ps[i] = { ...p, quantity: e.target.value }; set("processes", ps); }} /></td>
              <td className="small muted">マスタの単価</td>
              <td><button className="btn link" onClick={() => set("processes", f.processes.filter((_: any, j: number) => j !== i))}>外す</button></td>
            </tr>
          ))}
          {(f.custom_processes || []).map((p: any, i: number) => (
            <tr key={`c${i}`}>
              <td><input aria-label="未登録の加工の名前" value={p.name} onChange={(e) => { const ps = [...f.custom_processes]; ps[i] = { ...p, name: e.target.value }; set("custom_processes", ps); }} />
                <span className="sub">マスタ未登録（この見積だけの単価）</span></td>
              <td><input aria-label="未登録の加工の箇所数" type="number" min={0} className={p.quantity ? "" : "missing"} value={p.quantity ?? ""} onChange={(e) => { const ps = [...f.custom_processes]; ps[i] = { ...p, quantity: e.target.value }; set("custom_processes", ps); }} /></td>
              <td><input id={`c-custom-${i}`} aria-label="未登録の加工の単価" type="number" min={0} className={p.unit_price === null || p.unit_price === "" || p.unit_price === undefined ? "missing" : ""} value={p.unit_price ?? ""} onChange={(e) => { const ps = [...f.custom_processes]; ps[i] = { ...p, unit_price: e.target.value }; set("custom_processes", ps); }} /></td>
              <td><button className="btn link" onClick={() => set("custom_processes", f.custom_processes.filter((_: any, j: number) => j !== i))}>外す</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row" style={{ marginTop: 6 }}>
        <button className="btn small" onClick={() => set("processes", [...(f.processes || []), { code: meta?.processes[0]?.code, quantity: 1, source: "user" }])}>＋ マスタの加工</button>
        <button className="btn small" onClick={() => set("custom_processes", [...(f.custom_processes || []), { name: "", quantity: 1, unit_price: null, source: "user" }])}>＋ マスタにない加工</button>
      </div>

      {shapeNeeded.length > 0 && (
        <>
          <h3 style={{ margin: "16px 0 6px" }}>寸法の値（CADデータから求められなかった値）</h3>
          <div className="grid3">
            {shapeNeeded.map(([k, label, unit]) => (
              <div className="field" key={k}><label>{label}{unit && `（${unit}）`}</label>
                <input id={`c-shape-${k}`} aria-label={`寸法 ${label}`} type="number" min={0} className={shape[k].value === null ? "missing" : ""} value={f.shape?.[k] ?? ""}
                  onChange={(e) => set("shape", { ...(f.shape || {}), [k]: e.target.value })} /></div>
            ))}
          </div>
        </>
      )}
      {kind === "step" && (
        <div className="row" style={{ marginTop: 10 }}>
          <div className="field" style={{ width: 160 }}><label>Kファクター</label><input id="c-kfactor" aria-label="Kファクター" type="number" step="0.01" min={0} max={1} value={f.k_factor} onChange={(e) => set("k_factor", e.target.value)} /></div>
          <label className="check"><input type="checkbox" checked={f.k_factor_confirmed} onChange={(e) => set("k_factor_confirmed", e.target.checked)} />自社の加工条件で決まった値</label>
        </div>
      )}
      {kind === "dxf" && (
        <div className="row" style={{ marginTop: 10 }}>
          <div className="field" style={{ width: 160 }}><label>板厚（mm）</label><input aria-label="DXFの板厚" type="number" step="0.1" value={f.thickness_mm ?? ""} onChange={(e) => set("thickness_mm", e.target.value)} /></div>
          <label className="check"><input type="checkbox" checked={f.flat_confirmed} onChange={(e) => set("flat_confirmed", e.target.checked)} />曲げなし（平板）</label>
        </div>
      )}
      <div className="row" style={{ marginTop: 14 }}>
        <button className="btn primary" onClick={submit}>保存して計算し直す</button>
      </div>
    </section>
  );
}

function num0(v: any) {
  return v === "" || v === null || v === undefined ? null : Number(v);
}
