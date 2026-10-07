import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { get, upload } from "../api";
import { ErrorBox, Field, Marked, Modal, useMeta, useToast } from "../ui";

function highlight(text: string, q: string) {
  const words = q.split(/\s+/).filter(Boolean);
  if (!words.length || !text) return text;
  const re = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return text.split(re).map((part, i) => (i % 2 ? <mark key={i}>{part}</mark> : part));
}

export default function SearchPage() {
  const { meta } = useMeta();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") || "");
  const [kind, setKind] = useState("");
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<any>(null);
  const [adding, setAdding] = useState(false);
  const query = params.get("q") || "";
  useEffect(() => {
    setQ(query);
    get(`/api/search?q=${encodeURIComponent(query)}`).then(setData).catch(setError);
  }, [query]);
  const results = (data?.results || []).filter((r: any) => !kind || r.kind === kind);
  return (
    <div style={{ maxWidth: 980 }}>
      <div className="row between">
        <div className="tabs"><button className="on">キーワード検索</button></div>
        <button className="btn" onClick={() => setAdding(true)}>＋ 書類を登録</button>
      </div>
      <form onSubmit={(e) => { e.preventDefault(); setParams(q.trim() ? { q: q.trim() } : {}); }} style={{ margin: "14px 0" }}>
        <input aria-label="キーワード" value={q} onChange={(e) => setQ(e.target.value)} placeholder="図番・品名・顧客・書類の文字（スペースで区切るとすべてを含むもの）" style={{ fontSize: 15, padding: 12 }} />
      </form>
      <div className="row" style={{ gap: 6 }}>
        <button className={`btn small ${kind ? "" : "primary"}`} onClick={() => setKind("")}>すべて {data?.total ?? 0}</button>
        {(meta?.document_kinds || []).map((k) => (
          <button key={k} className={`btn small ${kind === k ? "primary" : ""}`} onClick={() => setKind(k)}>{k} {data?.counts?.[k] || 0}</button>
        ))}
      </div>
      <p className="small muted">{results.length}件</p>
      <ErrorBox error={error} />
      {query && results.length === 0 && <div className="empty">見つかりませんでした。</div>}
      {!query && <div className="empty">キーワードを入れて Enter を押してください。</div>}
      {results.map((r: any, i: number) => (
        <div key={i} className="row" style={{ alignItems: "flex-start", borderTop: "1px solid var(--line)", padding: "14px 0", flexWrap: "nowrap" }}>
          <span className={`tag ${r.type === "drawing" ? "soft" : "plain"}`} style={{ width: 70, justifyContent: "center" }}>{r.kind}</span>
          <div className="spacer">
            <b>{highlight(r.title, query)}</b>
            <div>{highlight(r.snippet, query)}</div>
            <div className="small muted">{r.meta}</div>
          </div>
          {r.url.startsWith("/api/") ? <a href={r.url} target="_blank" rel="noreferrer"><b>開く</b></a> : <Link to={r.url}><b>開く</b></Link>}
        </div>
      ))}
      {adding && <AddDocument onClose={() => setAdding(false)} onDone={() => { setAdding(false); toast("書類を登録しました"); get(`/api/search?q=${encodeURIComponent(query)}`).then(setData); }} />}
    </div>
  );
}

function AddDocument({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { meta } = useMeta();
  const file = useRef<HTMLInputElement>(null);
  const [kind, setKind] = useState("仕様書");
  const [title, setTitle] = useState("");
  const [customer, setCustomer] = useState("");
  const [drawingIds, setDrawingIds] = useState<string[]>([]);
  const [drawings, setDrawings] = useState<any[]>([]);
  const [error, setError] = useState<any>(null);
  useEffect(() => {
    get("/api/drawings").then(setDrawings).catch(() => {});
  }, []);
  async function submit() {
    const f = file.current?.files?.[0];
    if (!f) return setError("ファイルを選んでください（PDF・Excel）。");
    try {
      await upload("/api/library", f, { kind, title, customer, drawing_ids: JSON.stringify(drawingIds.map(Number)) });
      onDone();
    } catch (e) {
      setError(e);
    }
  }
  return (
    <Modal title="書類を登録" onClose={onClose}>
      <Field label="ファイル（PDF・Excel .xlsx）"><input ref={file} type="file" accept=".pdf,.xlsx" aria-label="書類ファイル" /></Field>
      <div className="grid2">
        <Field label="種類"><select value={kind} onChange={(e) => setKind(e.target.value)}>{(meta?.document_kinds || []).map((k) => <option key={k}>{k}</option>)}</select></Field>
        <Field label="顧客"><input value={customer} onChange={(e) => setCustomer(e.target.value)} /></Field>
      </div>
      <Field label="題名（空ならファイル名）"><input value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
      <Field label="関連する図面（複数選べます）">
        <select multiple value={drawingIds} onChange={(e) => setDrawingIds(Array.from(e.target.selectedOptions).map((o) => o.value))} style={{ height: 110 }}>
          {drawings.map((d) => <option key={d.id} value={d.id}>{d.drawing_no} {d.name}</option>)}
        </select>
      </Field>
      <ErrorBox error={error} />
      <Marked><button className="btn primary" onClick={submit}>登録する</button></Marked>
    </Modal>
  );
}
