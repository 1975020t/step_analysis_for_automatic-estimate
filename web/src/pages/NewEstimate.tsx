import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { get, post, upload, waitJob } from "../api";
import { ErrorBox, Field, Flow, Loading, Mark, Thumb, useLoad, useMeta } from "../ui";

export const WIZARD = [{ label: "図面選択" }, { label: "条件入力" }, { label: "解析・計算", note: "自動・約1分" }, { label: "確認・発行", note: "最終" }];
const ACCEPT = [".pdf", ".step", ".stp", ".dxf"];
type Up = { file_id: string; filename: string; kind: string };
type Source = { type: "files"; pdf?: Up; shape?: Up; readingJob?: string } | { type: "drawing"; drawingId: number };

/** 新規見積: 1 図面選択 → 2 条件入力 → 3 解析・計算（解析の進捗画面）→ 4 確認・発行（見積結果）.
 * A new file is registered as a drawing on the way (the drawing-only registration stays at 図面登録). */
export default function NewEstimatePage() {
  const [params, setParams] = useSearchParams();
  const preset = params.get("drawing");
  const [source, setSource] = useState<Source | null>(preset ? { type: "drawing", drawingId: Number(preset) } : null);
  const [step, setStep] = useState(preset ? 2 : 1);
  return (
    <div style={{ maxWidth: 1240 }}>
      {step === 1 ? <Link to="/" className="back">← ホーム</Link> : (
        <button className="btn link back" style={{ padding: 0 }} onClick={() => { setStep(1); setParams({}, { replace: true }); }}>← 図面選択に戻る</button>
      )}
      <Flow steps={WIZARD} now={step} />
      {step === 1 ? (
        <SelectDrawing source={source} setSource={setSource} onNext={() => setStep(2)} />
      ) : (
        source && <Conditions source={source} onBack={() => { setStep(1); setParams({}, { replace: true }); }} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 1 図面選択
function SelectDrawing({ source, setSource, onNext }: { source: Source | null; setSource: (s: Source | null) => void; onNext: () => void }) {
  const { meta } = useMeta();
  const input = useRef<HTMLInputElement>(null);
  const { data: drawings } = useLoad<any[]>("/api/drawings");
  const [q, setQ] = useState("");
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<any>(null);
  const files = source?.type === "files" ? source : null;

  async function addFiles(list: File[]) {
    setError(null);
    const bad = list.filter((f) => !ACCEPT.some((ext) => f.name.toLowerCase().endsWith(ext)));
    if (bad.length) return setError(`取り込めない種類のファイルです：${bad.map((f) => f.name).join("、")}（図面PDF・STEP・DXFのみ）`);
    const pdfs = list.filter((f) => f.name.toLowerCase().endsWith(".pdf"));
    const shapes = list.filter((f) => !f.name.toLowerCase().endsWith(".pdf"));
    if (pdfs.length > 1 || shapes.length > 1) return setError("新規見積は1図面ずつです（図面PDF 1つ＋CADデータ 1つまで）。複数の図面は「図面登録」でまとめて登録できます。");
    setBusy(true);
    try {
      const next: any = { type: "files", ...(files || {}) };
      for (const f of list) {
        const u = await upload("/api/files", f);
        if (u.kind === "pdf") {
          next.pdf = u;
          next.readingJob = undefined;
          const reader = meta ? meta.drawing_reader : (await get("/api/health").catch(() => ({}))).drawing_reader;
          if (reader) next.readingJob = (await post("/api/drawings/readings", { file_id: u.file_id })).job_id;
        } else next.shape = u;
      }
      setSource(next);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const stem = (n?: string) => (n || "").replace(/\.[^.]+$/, "").toLowerCase();
  const shown = (drawings || []).filter((d) => !q.trim() || [d.drawing_no, d.name, d.customer].some((v) => (v || "").toUpperCase().includes(q.trim().toUpperCase())));
  const ready = !!(files && (files.pdf || files.shape)) || source?.type === "drawing";

  return (
    <>
      <div className="grid2" style={{ gap: 24, gridTemplateColumns: "minmax(0, 3fr) minmax(0, 2fr)", alignItems: "start" }}>
        <section>
          <h2 style={{ marginBottom: 10 }}>新規図面から作成</h2>
          <div className={`drop ${over ? "over" : ""}`} style={{ background: "var(--paper)" }}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); addFiles(Array.from(e.dataTransfer.files)); }}>
            <h2>図面PDF・STEP・DXF をドラッグ＆ドロップ</h2>
            <p className="small muted">同名のPDFとCADデータは1件に統合。片方のみでも見積可能</p>
            <button className="btn" onClick={() => input.current?.click()} disabled={busy}>ファイルを選択</button>
            <input ref={input} type="file" multiple accept={ACCEPT.join(",")} hidden data-testid="file-input"
              onChange={(e) => { addFiles(Array.from(e.target.files || [])); e.target.value = ""; }} />
          </div>
          {files && (files.pdf || files.shape) && (
            <div className="list" style={{ marginTop: 10 }} data-testid="selected-files">
              {files.pdf && <div className="item"><span className="tag">図面PDF</span><span className="what">{files.pdf.filename}</span>
                <button className="btn link small" onClick={() => setSource(files.shape ? { type: "files", shape: files.shape } : null)}>外す</button></div>}
              {files.shape && <div className="item"><span className="tag">{files.shape.kind.toUpperCase()}</span><span className="what">{files.shape.filename}</span>
                <button className="btn link small" onClick={() => setSource(files.pdf ? { type: "files", pdf: files.pdf, readingJob: files.readingJob } : null)}>外す</button></div>}
              {files.pdf && files.shape && stem(files.pdf.filename) !== stem(files.shape.filename) && (
                <div className="item small warn">ファイル名が違います。同じ図面のPDFとCADデータか確認してください。</div>
              )}
            </div>
          )}
          <div className="panel white small" style={{ marginTop: 10, display: "flex", gap: "6px 24px", flexWrap: "wrap" }}>
            <span><b>STEP</b>　寸法・穴・曲げを自動解析</span>
            <span><b>DXF</b>　板厚のみ次の手順で入力</span>
            <span><b>PDFのみ</b>　寸法値は見積結果で入力</span>
          </div>
          <ErrorBox error={error} />
        </section>

        <section>
          <h2 style={{ marginBottom: 10 }}>登録済み図面から選択</h2>
          <div className="list">
            <div className="item"><input type="search" aria-label="図面を検索" placeholder="図番・品名・顧客で検索" value={q} onChange={(e) => setQ(e.target.value)} /></div>
            {!drawings ? <div className="item muted">読み込み中…</div> : shown.length === 0 ? <div className="item muted">該当する図面はありません。</div> : shown.slice(0, 6).map((d) => (
              <label key={d.id} className="item" style={{ cursor: "pointer", flexWrap: "nowrap" }}>
                <input type="radio" name="drawing" aria-label={`${d.drawing_no} ${d.name}`} checked={source?.type === "drawing" && source.drawingId === d.id}
                  onChange={() => setSource({ type: "drawing", drawingId: d.id })} style={{ width: 16 }} />
                <Thumb revisionId={d.revision_id} hasPdf={d.has_pdf} />
                <span className="what">
                  <b>{d.name || "（品名未設定）"}</b>　{d.drawing_no}{d.revision ? ` Rev.${d.revision}` : ""}
                  <span className="small muted" style={{ display: "block" }}>
                    {[d.customer || "顧客未設定", d.material, d.quote_id ? `前回 ${d.status?.name || ""}` : "見積未作成"].filter(Boolean).join("・")}
                  </span>
                </span>
              </label>
            ))}
            <div className="item"><Link to="/drawings">全図面を表示（{(drawings || []).length}件）→</Link></div>
          </div>
        </section>
      </div>
      <div className="row" style={{ justifyContent: "flex-end", borderTop: "1px solid var(--line)", paddingTop: 16, marginTop: 20 }}>
        <span className="small muted">{ready ? (source?.type === "drawing" ? "登録済みの図面を選択中" : "新しい図面は、この流れの中で登録します") : "図面を1件選択すると次へ進めます"}</span>
        <button className="btn primary" disabled={!ready || busy} onClick={onNext}>次へ：条件入力 →</button>
      </div>
    </>
  );
}

// ---------------------------------------------------------------- 2 条件入力
function Conditions({ source, onBack }: { source: Source; onBack: () => void }) {
  const { meta } = useMeta();
  const navigate = useNavigate();
  const [draft, setDraft] = useState<any>(null);
  const [drawing, setDrawing] = useState<any>(null);
  const [f, setF] = useState<any>(null);
  const [same, setSame] = useState<any[]>([]);
  const [newRevisionOf, setNewRevisionOf] = useState<number | null>(null);
  const [error, setError] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const set = (k: string, v: any) => setF((x: any) => ({ ...x, [k]: v }));
  const isFiles = source.type === "files";
  const kind = isFiles ? source.shape?.kind || "" : draft?.shape_kind || "";

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        let d;
        if (source.type === "drawing") {
          setDrawing(await get(`/api/drawings/${source.drawingId}`));
          d = await get(`/api/estimate-draft?drawing_id=${source.drawingId}`);
        } else {
          const name = source.pdf?.filename || source.shape?.filename || "";
          if (source.readingJob) {
            setBusy("図面を読み取り中…");
            await waitJob(source.readingJob);
          }
          d = await get(`/api/estimate-draft?${new URLSearchParams({ reading_job_id: source.readingJob || "", file_name: name })}`);
        }
        if (!live) return;
        setBusy("");
        setDraft(d);
        const v = d.fields;
        setF({
          customer: v.customer.value || "", title: v.name?.value ? `${v.name.value} 製作` : "", quantity: v.quantity.value ?? "", due_date: "", staff: "",
          drawing_no: v.drawing_no.value || "", revision: v.revision.value || "", name: v.name.value || "",
          material: v.material.value || "", surface_treatment: v.surface_treatment.value || "", rush: !!v.rush.value,
          processes: v.processes.processes.map((p: any) => ({ ...p, source: "drawing" })),
          custom_processes: v.processes.custom_processes.map((p: any) => ({ ...p, source: "drawing" })),
          k_factor: "", thickness: d.thickness_mm ? String(d.thickness_mm) : "", flat: false,
        });
        if (source.type === "files" && v.drawing_no.value) checkSame(v.drawing_no.value);
      } catch (e) {
        if (live) { setError(e); setBusy(""); }
      }
    })();
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function checkSame(no: string) {
    const found = no.trim() ? await get(`/api/drawings/same-number?drawing_no=${encodeURIComponent(no.trim())}`).catch(() => []) : [];
    setSame(found);
    setNewRevisionOf(found.length ? found[0].id : null);
  }

  if (!f) return busy ? <Loading text={busy} /> : error ? <ErrorBox error={error} /> : <Loading />;
  const fields = draft.fields;
  const missing = [!f.customer.trim() && "顧客", !(Number(f.quantity) > 0) && "数量", isFiles && !f.drawing_no.trim() && "図番"].filter(Boolean);

  async function submit() {
    setError(null);
    try {
      let drawingId = source.type === "drawing" ? source.drawingId : 0;
      if (source.type === "files") {
        setBusy("図面を登録中…");
        const reg = (await post("/api/drawings/register", { items: [{
          pdf_file_id: source.pdf?.file_id || "", shape_file_id: source.shape?.file_id || "", drawing_no: f.drawing_no.trim(), name: f.name.trim(),
          customer: f.customer.trim(), revision: f.revision.trim(), thickness_mm: kind === "dxf" && f.thickness ? Number(f.thickness) : null,
          new_revision_of: newRevisionOf, reading_job_id: source.readingJob || "", material: f.material, surface_treatment: f.surface_treatment,
        }] }))[0];
        await waitJob(reg.job_id);
        drawingId = reg.drawing_id;
      }
      setBusy("見積を開始中…");
      const body: any = { drawing_id: drawingId, customer: f.customer.trim(), title: f.title.trim(), quantity: Number(f.quantity), due_date: f.due_date || null,
        staff: f.staff, rush: f.rush, processes: f.processes.map((p: any) => ({ ...p, quantity: p.quantity ? Number(p.quantity) : null })),
        custom_processes: f.custom_processes.filter((p: any) => p.name.trim()).map((p: any) => ({ ...p, quantity: p.quantity ? Number(p.quantity) : null })) };
      if (f.material) body.material = f.material;
      if (f.surface_treatment) body.surface_treatment = f.surface_treatment;
      if (kind === "step" && f.k_factor !== "") Object.assign(body, { k_factor: Number(f.k_factor), k_factor_confirmed: true });
      if (kind === "dxf") Object.assign(body, { thickness_mm: f.thickness ? Number(f.thickness) : null, flat_confirmed: f.flat });
      const created = await post("/api/estimates", body);
      navigate(`/estimates/${created.estimate_id}/progress?job=${created.job_id}`);
    } catch (e) {
      setError(e);
      setBusy("");
    }
  }

  const note = (fl: any) => fl?.note ? <span className={`note ${fl.mark === "review" ? "review" : ""}`}>{fl.mark === "review" ? `要確認：${fl.note}` : fl.note}</span> : null;
  const readerNote = { done: "図面PDFの読み取り結果を入れました。誤りがあれば修正（未修正でも続行可能）",
    none: "図面PDFがないため、読み取り結果はありません", later: "図面の読み取りは解析時に行います",
    unavailable: "図面の自動読み取りは使えません", failed: "図面を読み取れませんでした。必要な項目を入力してください",
    pending: "図面を読み取り中です" } as Record<string, string>;
  const rev = drawing?.revisions?.find((r: any) => r.id === drawing.revision_id) || drawing?.revisions?.[0];

  return (
    <div className="grid2" style={{ gap: 24, gridTemplateColumns: "minmax(0, 3fr) minmax(280px, 1.3fr)", alignItems: "start" }}>
      <section className="panel white stack" style={{ gap: 20 }}>
        <div>
          <h2 style={{ marginBottom: 10 }}>必須項目</h2>
          <div className="grid2">
            <Field label={<>顧客<span className="mark required">必須</span>{fields.customer.mark && <Mark mark={fields.customer.mark} />}</>}>
              <input aria-label="顧客" value={f.customer} className={f.customer.trim() ? "" : "missing"} onChange={(e) => set("customer", e.target.value)} />
            </Field>
            <Field label={<>数量（個）<span className="mark required">必須</span><Mark mark={fields.quantity.mark} /></>}>
              <input aria-label="数量" type="number" min={1} value={f.quantity} className={Number(f.quantity) > 0 ? "" : "missing"} onChange={(e) => set("quantity", e.target.value)} />
              {note(fields.quantity)}
            </Field>
          </div>
        </div>

        <div>
          <h2>図面の読み取り結果</h2>
          <p className="small muted" style={{ margin: "2px 0 10px" }}>{readerNote[isFiles && source.pdf && !source.readingJob ? "unavailable" : draft.reader] || ""}</p>
          <div className="grid3">
            <Field label={<>図番<Mark mark={fields.drawing_no.mark} /></>}>
              {isFiles ? <input aria-label="図番" value={f.drawing_no} className={f.drawing_no.trim() ? "" : "missing"} onChange={(e) => set("drawing_no", e.target.value)} onBlur={(e) => checkSame(e.target.value)} />
                : <b>{f.drawing_no}</b>}
              {note(fields.drawing_no)}
            </Field>
            <Field label={<>改訂<Mark mark={fields.revision.mark} /></>}>
              {isFiles ? <input aria-label="改訂" value={f.revision} onChange={(e) => set("revision", e.target.value)} /> : <b>{f.revision || "—"}</b>}
            </Field>
            <Field label={<>品名{fields.name.mark && <Mark mark={fields.name.mark} />}</>}>
              {isFiles ? <input aria-label="品名" value={f.name} onChange={(e) => set("name", e.target.value)} /> : <b>{f.name || "—"}</b>}
            </Field>
          </div>
          {isFiles && same.length > 0 && (
            <label className="check small" style={{ marginBottom: 12 }}>
              <input type="checkbox" checked={!!newRevisionOf} onChange={(e) => setNewRevisionOf(e.target.checked ? same[0].id : null)} />
              同じ図番の図面があります。新しい版として登録（既存 {same[0].drawing_no} {same[0].revisions.filter(Boolean).map((x: string) => `Rev.${x}`).join("・")}）
            </label>
          )}
          <div className="grid2">
            <Field label={<>材質<Mark mark={fields.material.mark} /></>}>
              <select aria-label="材質" value={f.material} onChange={(e) => set("material", e.target.value)}>
                <option value="">（未選択）</option>
                {(meta?.materials || []).map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}
              </select>
              {note(fields.material)}
            </Field>
            <Field label={<>表面処理<Mark mark={fields.surface_treatment.mark} /></>}>
              <select aria-label="表面処理" value={f.surface_treatment} onChange={(e) => set("surface_treatment", e.target.value)}>
                <option value="">（未選択）</option>
                {(meta?.surface_treatments || []).map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}
              </select>
              {note(fields.surface_treatment)}
            </Field>
          </div>
          <div className="field">
            <label>追加加工<Mark mark={fields.processes.mark} /></label>
            {note(fields.processes)}
            <table className="rule" data-testid="draft-processes">
              <tbody>
                {f.processes.map((p: any, i: number) => (
                  <tr key={`p${i}`}>
                    <td><select aria-label="追加加工" value={p.code} onChange={(e) => { const ps = [...f.processes]; ps[i] = { ...p, code: e.target.value, source: "user" }; set("processes", ps); }}>
                      {(meta?.processes || []).map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}</select></td>
                    <td style={{ width: 150 }}><input aria-label="箇所数" type="number" min={0} placeholder="1個あたりの箇所数" value={p.quantity ?? ""} onChange={(e) => { const ps = [...f.processes]; ps[i] = { ...p, quantity: e.target.value }; set("processes", ps); }} /></td>
                    <td style={{ width: 50 }}><button className="btn link small" onClick={() => set("processes", f.processes.filter((_: any, j: number) => j !== i))}>外す</button></td>
                  </tr>
                ))}
                {f.custom_processes.map((p: any, i: number) => (
                  <tr key={`c${i}`}>
                    <td>{p.name}<span className="sub">マスタ未登録：単価は見積結果で入力</span></td>
                    <td><input aria-label="未登録の加工の箇所数" type="number" min={0} placeholder="1個あたりの箇所数" value={p.quantity ?? ""} onChange={(e) => { const ps = [...f.custom_processes]; ps[i] = { ...p, quantity: e.target.value }; set("custom_processes", ps); }} /></td>
                    <td><button className="btn link small" onClick={() => set("custom_processes", f.custom_processes.filter((_: any, j: number) => j !== i))}>外す</button></td>
                  </tr>
                ))}
                {f.processes.length + f.custom_processes.length === 0 && <tr><td className="muted small">なし</td></tr>}
              </tbody>
            </table>
            <div><button className="btn small" style={{ marginTop: 6 }} onClick={() => set("processes", [...f.processes, { code: meta?.processes[0]?.code, quantity: 1, source: "user" }])}>＋ 追加加工</button></div>
          </div>
          <label className="check">
            <input type="checkbox" checked={f.rush} onChange={(e) => set("rush", e.target.checked)} />
            特急（割増 {Math.round((meta?.policy.rush_surcharge_rate || 0) * 100)}%）<Mark mark={fields.rush.mark} />
          </label>
          {note(fields.rush)}
        </div>

        <div>
          <h2 style={{ marginBottom: 10 }}>任意項目（後から入力可能）</h2>
          <div className="grid3">
            <Field label="案件名"><input aria-label="案件名" value={f.title} onChange={(e) => set("title", e.target.value)} /></Field>
            <Field label="希望納期" hint="金額への影響なし"><input aria-label="希望納期" type="date" value={f.due_date} onChange={(e) => set("due_date", e.target.value)} /></Field>
            <Field label="担当">
              <select aria-label="担当" value={f.staff} onChange={(e) => set("staff", e.target.value)}>
                <option value="">（未設定）</option>
                {(meta?.staff || []).map((s) => <option key={s.id}>{s.name}</option>)}
              </select>
            </Field>
            {kind === "step" && (
              <Field label="Kファクター（曲げの伸び）" hint="未入力の場合、標準値0.33で展開し概算">
                <input aria-label="Kファクター" type="number" step="0.01" min={0} max={1} placeholder="0.33（標準値）" value={f.k_factor} onChange={(e) => set("k_factor", e.target.value)} />
              </Field>
            )}
            {kind === "dxf" && (
              <>
                <Field label={<>板厚（mm）{draft.thickness_mm && <Mark mark="read" />}</>} hint="展開図（DXF）には板厚がないため入力">
                  <input aria-label="板厚" type="number" step="0.1" value={f.thickness} onChange={(e) => set("thickness", e.target.value)} />
                </Field>
                <label className="check" style={{ marginTop: 22 }}>
                  <input type="checkbox" checked={f.flat} onChange={(e) => set("flat", e.target.checked)} />曲げなし（平板）
                </label>
              </>
            )}
          </div>
        </div>
      </section>

      <aside className="stack">
        <div className="side-box">
          <h3>選択中の図面</h3>
          {source.type === "drawing" ? (
            <>
              <Thumb revisionId={rev?.id} hasPdf={!!rev?.pdf_file_id} className="thumb-big" />
              <div className="small" style={{ marginTop: 6 }}>{drawing?.drawing_no} {rev?.revision ? `Rev.${rev.revision}` : ""} {drawing?.name}</div>
              <div className="small muted">{[rev?.pdf_name, rev?.shape_name].filter(Boolean).join(" ＋ ") || "ファイルなし"}</div>
            </>
          ) : (
            <>
              <div className="hatch" style={{ height: 120 }} />
              <div className="small" style={{ marginTop: 6 }}>{[source.pdf?.filename, source.shape?.filename].filter(Boolean).join(" ＋ ")}</div>
              <div className="small muted">「解析・計算を実行」で図面として登録します</div>
            </>
          )}
        </div>
        <div className="side-box">
          <h3>次の手順（自動処理）</h3>
          <ol className="small" style={{ margin: 0, paddingLeft: 18 }}>
            {kind ? <li>CADデータから板厚・穴・曲げ・切断長を算出</li> : <li>CADデータがないため、寸法の値は見積結果で入力</li>}
            <li>マスタ単価で金額を計算</li>
            <li>類似実績を検索</li>
          </ol>
        </div>
        <ErrorBox error={error} />
        {missing.length > 0 && <p className="small warn" style={{ margin: 0 }}>未入力：{missing.join("・")}</p>}
        <div className="row">
          <button className="btn" onClick={onBack}>戻る</button>
          <button className="btn primary" style={{ flex: 1, justifyContent: "center" }} disabled={!!busy || missing.length > 0} onClick={submit}>{busy || "解析・計算を実行 →"}</button>
        </div>
      </aside>
    </div>
  );
}
