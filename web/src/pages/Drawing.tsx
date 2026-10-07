import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { day, del, get, num, post, put, stamp, yen } from "../api";
import { ApiImage, ErrorBox, LineTabs, Loading, Marked, StatusTag, useMeta, useToast } from "../ui";
import { FlatPattern, Model3D } from "../viewers";

export default function DrawingPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const { meta } = useMeta();
  const [d, setD] = useState<any>(null);
  const [error, setError] = useState<any>(null);
  const [revId, setRevId] = useState<number | null>(null);
  const [view, setView] = useState<"pdf" | "3d" | "flat">("pdf");
  const [tab, setTab] = useState<"info" | "revisions" | "usage" | "docs" | "memos">("info");
  const [showNotes, setShowNotes] = useState(true);
  const [placing, setPlacing] = useState<{ x: number; y: number } | "armed" | null>(null);
  const [noteText, setNoteText] = useState("");
  const [similar, setSimilar] = useState<any[] | null>(null);
  const similarRef = useRef<HTMLDivElement>(null);

  const load = () => get(`/api/drawings/${id}`).then((x) => { setD(x); setError(null); setRevId((r) => r ?? x.revision_id); }).catch(setError);
  useEffect(() => {
    setRevId(null);
    load();
    get(`/api/drawings/${id}/similar`).then(setSimilar).catch(() => setSimilar([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  useEffect(() => {
    if (d?.revisions?.some((r: any) => r.processing)) {
      const t = setTimeout(load, 1500);
      return () => clearTimeout(t);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d]);

  if (!d) return error ? <ErrorBox error={error} /> : <Loading />;
  const rev = d.revisions.find((r: any) => r.id === revId) || d.revisions[0];
  const views = [rev?.pdf_file_id && "pdf", rev?.shape_kind === "step" && "3d", rev?.analysis?.flat_pattern && "flat"].filter(Boolean) as string[];
  const current = views.includes(view) ? view : views[0];

  async function addNote() {
    if (!placing || placing === "armed" || !noteText.trim()) return;
    try {
      await post(`/api/revisions/${rev.id}/notes`, { ...placing, text: noteText });
      setPlacing(null);
      setNoteText("");
      load();
    } catch (e) {
      setError(e);
    }
  }

  return (
    <div>
      <Link to="/drawings" className="small"><b>← 図面一覧</b></Link>
      <div className="row between" style={{ alignItems: "flex-end", margin: "6px 0 16px" }}>
        <div>
          <h1 style={{ fontSize: 30 }}>{d.name || "（品名未設定）"}<span className="num" style={{ fontSize: 26, marginLeft: 14 }}>{d.drawing_no} {rev?.revision && `Rev.${rev.revision}`}</span></h1>
          <div className="small muted">{d.customer || "顧客未設定"} ／ {d.material || "材質未設定"} ／ {d.processes.join("・") || "—"} ／ 状態 {d.status?.name || "未作成"}</div>
        </div>
        <div className="row">
          <button className="btn" onClick={() => similarRef.current?.scrollIntoView({ behavior: "smooth" })}>似た図面を探す</button>
          <button className="btn" onClick={() => navigate(`/drawings/register`)}>新しい版を登録</button>
          <Marked><button className="btn primary" onClick={() => navigate(`/estimates/new?drawing=${d.id}`)}>この図面で見積を作成</button></Marked>
        </div>
      </div>
      <ErrorBox error={error} />
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.25fr) minmax(320px, 1fr)", gap: 26 }}>
        <div>
          <div className="row" style={{ marginBottom: 8 }}>
            <span className="small">版</span>
            {d.revisions.map((r: any) => (
              <button key={r.id} className={`btn small ${r.id === rev?.id ? "primary" : ""}`} onClick={() => setRevId(r.id)}>{r.revision ? `Rev.${r.revision}` : `版${r.id}`}{r.id === d.revision_id ? "（最新）" : ""}</button>
            ))}
            <span className="spacer" />
            {views.length > 1 && views.map((v) => <button key={v} className={`btn small ${current === v ? "primary" : ""}`} onClick={() => setView(v as any)}>{({ pdf: "図面PDF", "3d": "3D", flat: "展開図" } as any)[v]}</button>)}
            {current === "pdf" && <button className="btn link" onClick={() => setShowNotes(!showNotes)}>{showNotes ? "書き込みを隠す" : "書き込みを表示"}</button>}
            {current === "pdf" && <button className="btn small" onClick={() => setPlacing("armed")}>書き込みを追加</button>}
          </div>
          <Marked className="hatch" style={{ position: "relative", minHeight: 360, background: current === "pdf" ? "#fff" : undefined }}>
            {current === "pdf" && (
              <div style={{ position: "relative", cursor: placing === "armed" ? "crosshair" : "default" }}
                onClick={(e) => {
                  if (placing !== "armed") return;
                  const box = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
                  setPlacing({ x: (e.clientX - box.left) / box.width, y: (e.clientY - box.top) / box.height });
                }}>
                <ApiImage src={`/api/revisions/${rev.id}/preview.png?width=1100`} alt="図面プレビュー" className="" />
                {showNotes && rev.notes.map((n: any, i: number) => (
                  <div key={n.id} className="pin" style={{ left: `${n.x * 100}%`, top: `${n.y * 100}%` }}>
                    <b>{i + 1}</b><span>{n.text}<button className="btn link small" title="消す" onClick={async () => { await del(`/api/notes/${n.id}`); load(); }}>✕</button></span>
                  </div>
                ))}
                {placing && placing !== "armed" && <div className="pin" style={{ left: `${placing.x * 100}%`, top: `${placing.y * 100}%` }}><b>＋</b></div>}
              </div>
            )}
            {current === "3d" && <Model3D fileId={rev.shape_file_id} height={380} />}
            {current === "flat" && <FlatPattern flat={rev.analysis.flat_pattern} height={380} />}
            {!current && <div className="empty">プレビューがありません。</div>}
          </Marked>
          {placing === "armed" && <p className="small">図面上の書き込みたい位置をクリックしてください。</p>}
          {placing && placing !== "armed" && (
            <div className="row" style={{ marginTop: 8 }}>
              <input aria-label="書き込み" placeholder="書き込む内容" value={noteText} onChange={(e) => setNoteText(e.target.value)} style={{ flex: 1, width: "auto" }} />
              <button className="btn primary" onClick={addNote}>書き込む</button>
              <button className="btn" onClick={() => setPlacing(null)}>やめる</button>
            </div>
          )}
          {rev?.processing && <p className="small muted">CADデータを解析中です…</p>}
        </div>

        <div>
          <LineTabs value={tab} onChange={setTab} options={[
            { value: "info", label: "図面情報" }, { value: "revisions", label: "改訂履歴" }, { value: "usage", label: `使用先 ${d.usage.length}` },
            { value: "docs", label: `関連書類 ${d.documents.length}` }, { value: "memos", label: `注意・不具合メモ ${d.memos.length}` },
          ]} />
          {tab === "info" && <Info d={d} rev={rev} meta={meta} onSaved={(x: any) => { setD(x); toast("図面情報を保存しました"); }} onError={setError} />}
          {tab === "revisions" && (
            <table className="rule">
              <thead><tr><th>版</th><th>ファイル</th><th>登録</th></tr></thead>
              <tbody>{d.revisions.map((r: any) => (
                <tr key={r.id}><td>{r.revision ? `Rev.${r.revision}` : "—"} {r.id === d.revision_id && <span className="tag soft">最新</span>}</td>
                  <td className="small">{[r.pdf_name, r.shape_name].filter(Boolean).join(" ／ ")}</td>
                  <td className="small">{stamp(r.created_at)} {r.created_by}</td></tr>
              ))}</tbody>
            </table>
          )}
          {tab === "usage" && (d.usage.length === 0 ? <div className="small muted">この図面を使った見積はまだありません。</div> : (
            <table className="rule"><thead><tr><th>見積番号</th><th>顧客</th><th>状態</th><th className="right">金額</th></tr></thead>
              <tbody>{d.usage.map((u: any) => (
                <tr key={u.quote_id}><td><Link to={`/estimates/${u.quote_id}`}>{u.number}</Link><span className="sub">{u.revision && `Rev.${u.revision}`} {u.title}</span></td>
                  <td>{u.customer}</td><td><StatusTag name={u.status} color={u.status_color} /></td><td className="right">{u.total ? yen(u.total) : "未作成"}</td></tr>
              ))}</tbody></table>
          ))}
          {tab === "docs" && (
            <div>
              {d.documents.length === 0 && <div className="small muted">関連書類はありません。<Link to="/search">書類の登録</Link>で、この図面を関連づけられます。</div>}
              {d.documents.map((doc: any) => (
                <div key={doc.id} className="row" style={{ borderBottom: "1px solid var(--line)", padding: "6px 0" }}>
                  <span className="tag plain">{doc.kind}</span><a href={`/api/library/${doc.id}/file`} target="_blank" rel="noreferrer">{doc.title}</a><span className="small muted">{doc.file_type}</span>
                </div>
              ))}
            </div>
          )}
          {tab === "memos" && <Memos d={d} onChanged={(x: any) => setD(x)} onError={setError} />}
        </div>
      </div>

      <div ref={similarRef} className="section-title"><h2>似た図面の過去実績と比べる</h2></div>
      {!similar ? <Loading /> : (
        <table className="rule" data-testid="drawing-similar">
          <thead><tr><th>図番</th><th>品名・顧客</th><th>材質</th><th>穴・曲げ</th><th>今回との差</th><th className="right">数量</th><th className="right">単価</th><th>結果</th><th>日付</th></tr></thead>
          <tbody>
            <tr className="total"><td>{d.drawing_no} {rev?.revision && `Rev.${rev.revision}`}</td><td>{d.name}</td><td>{d.material}</td>
              <td>{d.metrics.hole_count ?? "—"}・{d.metrics.bend_count ?? "—"}</td><td>基準</td><td className="right">{d.quantity ?? "—"}</td><td className="right">{d.unit_price ? yen(d.unit_price) : "—"}</td><td><span className="tag">今回</span></td><td /></tr>
            {similar.length === 0 && <tr><td colSpan={9} className="empty">{d.metrics.hole_count === null ? "CADデータの寸法がないため、類似は探せません。" : "条件に合う図面・実績はありません。"}</td></tr>}
            {similar.map((s, i) => (
              <tr key={i}>
                <td>{s.drawing_id ? <Link to={`/drawings/${s.drawing_id}`}>{s.drawing_no}</Link> : s.drawing_no || s.quote_no}</td>
                <td>{s.name}<span className="sub">{s.customer}</span></td><td>{s.material}</td><td>{s.holes}・{s.bends}</td><td>{s.difference}</td>
                <td className="right">{s.quantity ?? "—"}</td><td className="right">{s.unit_price ? yen(s.unit_price) : "—"}</td><td><span className="tag plain">{s.status}</span></td><td className="small">{day(s.date)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Info({ d, rev, meta, onSaved, onError }: any) {
  const [f, setF] = useState<any>(null);
  const [editing, setEditing] = useState(false);
  useEffect(() => setF({ drawing_no: d.drawing_no, name: d.name, customer: d.customer, material: rev?.material || "", surface_treatment: rev?.surface_treatment || "",
    revision: rev?.revision || "", attrs: { ...(d.attrs || {}) }, category_ids: d.category_ids }), [d, rev]);
  if (!f) return null;
  const custom = (meta?.attributes || []).filter((a: any) => !a.builtin);
  const m = rev?.metrics || {};
  const readMark = (field: string) => rev?.reading_items?.some((i: any) => i.field === field && i.status !== "記載なし") ? <span className="tag plain" title="図面PDFから読み取った値">読取</span> : null;
  async function save() {
    try {
      onSaved(await put(`/api/drawings/${d.id}`, { version: d.version, ...f }));
      setEditing(false);
    } catch (e) {
      onError(e);
    }
  }
  const row = (label: string, value: any, input?: any, mark?: any) => (
    <tr key={label}><td style={{ width: 120 }} className="muted">{label}</td><td>{editing && input ? input : value || "—"}</td><td className="right">{mark}</td></tr>
  );
  const categories = (meta?.categories || []).flatMap((g: any) => g.children.map((c: any) => ({ ...c, group: g.name })));
  return (
    <div>
      <div className="row between"><span className="small muted">「読取」は図面から読み取った値です。</span>
        {editing ? <div className="row"><button className="btn small primary" onClick={save}>保存</button><button className="btn small" onClick={() => setEditing(false)}>やめる</button></div>
          : <button className="btn small" onClick={() => setEditing(true)}>属性を編集</button>}</div>
      <table className="rule">
        <tbody>
          {row("図番（必須）", d.drawing_no, <input value={f.drawing_no} onChange={(e) => setF({ ...f, drawing_no: e.target.value })} />)}
          {row("品名", d.name, <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />)}
          {row("顧客名", d.customer, <input value={f.customer} onChange={(e) => setF({ ...f, customer: e.target.value })} />)}
          {row("改訂", rev?.revision, <input value={f.revision} onChange={(e) => setF({ ...f, revision: e.target.value })} />)}
          {row("ステータス", d.status?.name || "未作成")}
          {row("材質", d.material_name || d.material, <input list="mats" value={f.material} onChange={(e) => setF({ ...f, material: e.target.value })} />, readMark("material"))}
          {row("表面処理", d.surface_treatment_name || d.surface_treatment, <select value={f.surface_treatment} onChange={(e) => setF({ ...f, surface_treatment: e.target.value })}>
            <option value="">—</option>{(meta?.surface_treatments || []).map((s: any) => <option key={s.code} value={s.code}>{s.name}</option>)}</select>, readMark("surface_treatment"))}
          {row("加工", d.processes.join("・"))}
          {row("板厚", m.thickness_mm ? `${m.thickness_mm} mm` : "—")}
          {row("穴数・曲げ数", m.hole_count !== null && m.hole_count !== undefined ? `穴 ${m.hole_count} ・ 曲げ ${m.bend_count}` : "—")}
          {row("展開面積・切断長", m.blank_area_mm2 ? `${num(m.blank_area_mm2, 0)} mm² ・ ${num(m.cut_length_mm, 0)} mm` : "—")}
          {row("最大寸法", d.max_dimension_mm ? `${d.max_dimension_mm} mm` : "—")}
          {row("登録日", day(d.created_at))}
          {row("分類", categories.filter((c: any) => d.category_ids.includes(c.id)).map((c: any) => `${c.group}/${c.name}`).join("、"),
            <select multiple value={f.category_ids.map(String)} onChange={(e) => setF({ ...f, category_ids: Array.from(e.target.selectedOptions).map((o) => Number(o.value)) })} style={{ height: 90 }}>
              {categories.map((c: any) => <option key={c.id} value={c.id}>{c.group} / {c.name}</option>)}</select>)}
          {custom.map((a: any) => row(a.label, d.attrs?.[a.key],
            a.input_type === "select" ? <select value={f.attrs[a.key] || ""} onChange={(e) => setF({ ...f, attrs: { ...f.attrs, [a.key]: e.target.value } })}><option value="">—</option>{a.options.map((o: string) => <option key={o}>{o}</option>)}</select>
              : <input type={a.input_type === "number_range" ? "number" : a.input_type === "date_range" ? "date" : "text"} value={f.attrs[a.key] || ""} onChange={(e) => setF({ ...f, attrs: { ...f.attrs, [a.key]: e.target.value } })} />))}
        </tbody>
      </table>
      <datalist id="mats">{(meta?.materials || []).map((m: any) => <option key={m.code} value={m.code}>{m.name}</option>)}</datalist>
      {rev?.reading_items?.length > 0 && (
        <>
          <h3 style={{ margin: "14px 0 4px" }}>図面PDFの読み取り結果</h3>
          <table className="rule"><tbody>{rev.reading_items.map((i: any) => (
            <tr key={i.field}><td className="muted" style={{ width: 120 }}>{i.label}</td><td>{i.read}</td><td className="right"><span className={i.status === "確定" ? "tag soft" : i.status === "要確認" ? "tag solid" : "tag grey"}>{i.status_label}</span></td></tr>
          ))}</tbody></table>
        </>
      )}
    </div>
  );
}

function Memos({ d, onChanged, onError }: any) {
  const [kind, setKind] = useState("注意");
  const [text, setText] = useState("");
  return (
    <div className="stack">
      <div className="row">
        <select value={kind} onChange={(e) => setKind(e.target.value)} style={{ width: 90 }}><option>注意</option><option>不具合</option></select>
        <input aria-label="メモ" value={text} onChange={(e) => setText(e.target.value)} placeholder="例：曲げ順に注意（干渉する）" style={{ flex: 1, width: "auto" }} />
        <button className="btn" disabled={!text.trim()} onClick={async () => { try { onChanged(await post(`/api/drawings/${d.id}/memos`, { kind, text })); setText(""); } catch (e) { onError(e); } }}>追加</button>
      </div>
      {d.memos.map((m: any) => (
        <div key={m.id} style={{ borderBottom: "1px solid var(--line)", paddingBottom: 6 }}>
          <span className={m.kind === "不具合" ? "tag solid" : "tag"}>{m.kind}</span> {m.text}
          <div className="small muted">{stamp(m.created_at)} {m.created_by}</div>
        </div>
      ))}
    </div>
  );
}
