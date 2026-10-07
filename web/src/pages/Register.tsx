import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { get, post, upload, waitJob } from "../api";
import { ErrorBox, useMeta, useToast } from "../ui";

type Row = {
  key: string;
  pdf?: { file_id: string; filename: string };
  shape?: { file_id: string; filename: string; kind: string };
  drawing_no: string;
  name: string;
  customer: string;
  revision: string;
  category_id: string;
  thickness: string;
  material: string;
  reading: "none" | "reading" | "done" | "failed" | "unavailable";
  readingJob: string;
  readNote: string;
  same: any[];
  newRevisionOf: number | null;
};

const ACCEPT = [".pdf", ".step", ".stp", ".dxf"];
const stem = (name: string) => name.replace(/\.[^.]+$/, "").trim().toLowerCase();

export default function RegisterPage() {
  const { meta } = useMeta();
  const toast = useToast();
  const navigate = useNavigate();
  const input = useRef<HTMLInputElement>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<any>(null);
  const [skipped, setSkipped] = useState<string[]>([]);
  const [done, setDone] = useState<any[] | null>(null);

  const patch = (key: string, change: Partial<Row>) => setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...change } : r)));

  async function checkSame(key: string, drawingNo: string) {
    if (!drawingNo.trim()) return patch(key, { same: [], newRevisionOf: null });
    const same = await get(`/api/drawings/same-number?drawing_no=${encodeURIComponent(drawingNo)}`).catch(() => []);
    patch(key, { same, newRevisionOf: same.length ? same[0].id : null });
  }

  async function addFiles(files: File[]) {
    setError(null);
    const bad = files.filter((f) => !ACCEPT.some((ext) => f.name.toLowerCase().endsWith(ext)));
    setSkipped(bad.map((f) => f.name));
    const good = files.filter((f) => !bad.includes(f));
    if (!good.length) return;
    setBusy(true);
    try {
      const uploaded = [];
      for (const f of good) uploaded.push(await upload("/api/files", f));
      const next = [...rows];
      for (const u of uploaded) {
        const key = stem(u.filename);
        let row = next.find((r) => r.key === key && !(u.kind === "pdf" ? r.pdf : r.shape));
        if (!row) {
          row = { key: `${key}`, drawing_no: "", name: "", customer: "", revision: "", category_id: "", thickness: "", material: "",
            reading: "none", readingJob: "", readNote: "", same: [], newRevisionOf: null };
          if (next.some((r) => r.key === key)) row.key = `${key}#${next.length}`;
          next.push(row);
        }
        if (u.kind === "pdf") row.pdf = u;
        else row.shape = u;
        if (!row.drawing_no) row.drawing_no = u.filename.replace(/\.[^.]+$/, "");
      }
      setRows(next);
      for (const row of next) {
        if (row.pdf && row.reading === "none") readDrawing(row);
        else if (!row.pdf) checkSame(row.key, row.drawing_no);
      }
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function readDrawing(row: Row) {
    const available = meta ? meta.drawing_reader : (await get("/api/health").catch(() => ({}))).drawing_reader;
    if (!available) {
      patch(row.key, { reading: "unavailable", readNote: "図面の自動読み取りは使えません。手で入力してください。" });
      checkSame(row.key, row.drawing_no);
      return;
    }
    patch(row.key, { reading: "reading", readNote: "図面を読み取り中…" });
    try {
      const job = await post("/api/drawings/readings", { file_id: row.pdf!.file_id });
      patch(row.key, { readingJob: job.job_id });
      const done = await waitJob(job.job_id);
      if (done.status !== "done") throw new Error(done.error || "読み取りに失敗しました");
      const r = done.result.reading;
      const change: Partial<Row> = { reading: "done", readNote: "読み取り結果を下書きに入れました（確認してください）" };
      if (r.drawing_no) change.drawing_no = r.drawing_no;
      if (r.revision) change.revision = r.revision;
      if (r.material) change.material = r.material === "UNREGISTERED" ? r.source_texts?.material || "" : r.material;
      if (row.shape?.kind === "dxf" && r.thickness_mm && !row.thickness) change.thickness = String(r.thickness_mm);
      patch(row.key, change);
      checkSame(row.key, change.drawing_no || row.drawing_no);
    } catch {
      patch(row.key, { reading: "failed", readNote: "読み取れませんでした。手で入力してください。" });
      checkSame(row.key, row.drawing_no);
    }
  }

  const ready = rows.filter((r) => r.drawing_no.trim() && (r.pdf || r.shape));

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const items = ready.map((r) => ({
        pdf_file_id: r.pdf?.file_id || "", shape_file_id: r.shape?.file_id || "", drawing_no: r.drawing_no.trim(), name: r.name.trim(),
        customer: r.customer.trim(), revision: r.revision.trim(), category_ids: r.category_id ? [Number(r.category_id)] : [],
        thickness_mm: r.thickness ? Number(r.thickness) : null, new_revision_of: r.newRevisionOf, reading_job_id: r.readingJob,
        material: r.material,
      }));
      const result = await post("/api/drawings/register", { items });
      setDone(result.map((x: any, i: number) => ({ ...x, drawing_no: items[i].drawing_no, name: items[i].name })));
      setRows([]);
      toast(`${result.length}件の図面を登録しました`);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const children = (meta?.categories || []).flatMap((g) => g.children.map((c) => ({ ...c, group: g.name })));

  return (
    <div>
      <p className="lead">図面のみを登録します（見積は後から作成可能）。見積まで続けて行う場合は <Link to="/estimates/new">新規見積</Link> から。</p>
      <div
        className={`drop ${over ? "over" : ""}`}
        onDragOver={(e: any) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e: any) => {
          e.preventDefault();
          setOver(false);
          addFiles(Array.from(e.dataTransfer.files));
        }}
      >
        <h2>図面PDF・CADデータをドラッグ＆ドロップ</h2>
        <div className="kinds">
          <span className="tag">図面PDF</span>
          <span className="tag">STEP / STP</span>
          <span className="tag">DXF（展開図）</span>
        </div>
        <p className="small muted">同じ名前の図面PDFとCADデータは1件にまとめます。</p>
        <div className="row" style={{ justifyContent: "center" }}>
          <button className="btn" onClick={() => input.current?.click()} disabled={busy}>
            ファイルを選択
          </button>
          <input ref={input} type="file" multiple accept={ACCEPT.join(",")} hidden data-testid="file-input"
            onChange={(e) => { addFiles(Array.from(e.target.files || [])); e.target.value = ""; }} />
        </div>
      </div>
      {skipped.length > 0 && <div className="error">取り込めない種類のファイルは除きました：{skipped.join("、")}（図面PDF・STEP・DXFのみ）</div>}
      <ErrorBox error={error} />

      <div className="section-title">
        <h2>登録内容の確認</h2>
        {rows.length > 0 && <span className="tag solid">未入力 {rows.length - ready.length}件</span>}
      </div>
      {rows.length === 0 ? (
        <div className="empty">
          {done ? (
            <div>
              登録しました。
              <ul style={{ textAlign: "left", display: "inline-block" }}>
                {done.map((d) => (
                  <li key={d.revision_id}>
                    <Link to={`/drawings/${d.drawing_id}`}>{d.drawing_no} {d.name}</Link>
                    <Link to={`/estimates/new?drawing=${d.drawing_id}`} style={{ marginLeft: 10 }}>新規見積 →</Link>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            "ファイルを追加すると、ここに1件ずつ並びます。"
          )}
        </div>
      ) : (
        <table className="rule" data-testid="register-table">
          <thead>
            <tr>
              <th>ファイル</th>
              <th>図番（必須）</th>
              <th>品名</th>
              <th>顧客</th>
              <th>改訂</th>
              <th>分類</th>
              <th>材質・板厚</th>
              <th>登録のしかた</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <td style={{ minWidth: 190 }}>
                  {r.pdf && <div>📄 {r.pdf.filename}</div>}
                  {r.shape && <div>◆ {r.shape.filename}</div>}
                  {!r.shape && <span className="small muted">CADデータなし</span>}
                  {r.readNote && <span className={`small ${r.reading === "failed" ? "warn" : "muted"}`} style={{ display: "block" }}>{r.readNote}</span>}
                </td>
                <td><input aria-label="図番" value={r.drawing_no} className={r.drawing_no.trim() ? "" : "missing"}
                  onChange={(e) => patch(r.key, { drawing_no: e.target.value })} onBlur={(e) => checkSame(r.key, e.target.value)} /></td>
                <td><input aria-label="品名" value={r.name} onChange={(e) => patch(r.key, { name: e.target.value })} /></td>
                <td><input aria-label="顧客" value={r.customer} onChange={(e) => patch(r.key, { customer: e.target.value })} /></td>
                <td style={{ width: 70 }}><input aria-label="改訂" value={r.revision} onChange={(e) => patch(r.key, { revision: e.target.value })} /></td>
                <td>
                  <select aria-label="分類" value={r.category_id} onChange={(e) => patch(r.key, { category_id: e.target.value })}>
                    <option value="">（なし）</option>
                    {children.map((c) => (
                      <option key={c.id} value={c.id}>{c.group} / {c.name}</option>
                    ))}
                  </select>
                </td>
                <td style={{ minWidth: 120 }}>
                  <input aria-label="材質" placeholder="材質" value={r.material} onChange={(e) => patch(r.key, { material: e.target.value })} list="materials" />
                  {r.shape?.kind === "dxf" && (
                    <input aria-label="板厚" placeholder="板厚 mm" type="number" step="0.1" value={r.thickness} style={{ marginTop: 4 }}
                      onChange={(e) => patch(r.key, { thickness: e.target.value })} />
                  )}
                </td>
                <td style={{ minWidth: 150 }}>
                  {r.same.length > 0 ? (
                    <label className="check">
                      <input type="checkbox" checked={!!r.newRevisionOf} onChange={(e) => patch(r.key, { newRevisionOf: e.target.checked ? r.same[0].id : null })} />
                      新しい版として登録
                      <span className="small muted">（既存 {r.same[0].drawing_no} {r.same[0].revisions.filter(Boolean).map((x: string) => `Rev.${x}`).join("・")}）</span>
                    </label>
                  ) : (
                    <span className="tag plain">新規の図面</span>
                  )}
                </td>
                <td><button className="btn link" onClick={() => setRows(rows.filter((x) => x.key !== r.key))}>外す</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <datalist id="materials">
        {(meta?.materials || []).map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}
      </datalist>
      <div className="row between" style={{ marginTop: 16 }}>
        <span className="small muted">図番が未入力の行は登録されません。</span>
        <div className="row">
          <button className="btn" onClick={() => navigate("/drawings")}>図面へ</button>
          <button className="btn primary" disabled={busy || ready.length === 0 || rows.some((r) => r.reading === "reading")} onClick={submit}>
            確認済み {ready.length} 件を登録
          </button>
        </div>
      </div>
    </div>
  );
}
