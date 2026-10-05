import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { get, post } from "../api";
import { ErrorBox, Field, Marked, Thumb, useLoad, useMeta } from "../ui";

export default function NewEstimatePage() {
  const { meta } = useMeta();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { data: drawings } = useLoad<any[]>("/api/drawings");
  const [drawingId, setDrawingId] = useState<string>(params.get("drawing") || "");
  const [drawing, setDrawing] = useState<any>(null);
  const [form, setForm] = useState<any>({ customer: "", title: "", quantity: "", due_date: "", staff: "", material: "", custom_name: "",
    custom_price: "", custom_density: "", finish: "", k_factor: "0.33", k_confirmed: false, thickness: "", flat: false });
  const [error, setError] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));

  useEffect(() => {
    if (!drawingId) return setDrawing(null);
    get(`/api/drawings/${drawingId}`).then((d) => {
      setDrawing(d);
      const known = meta?.materials.some((m) => m.code === d.material);
      const finishKnown = meta?.surface_treatments.some((m) => m.code === d.surface_treatment);
      setForm((f: any) => ({
        ...f,
        customer: f.customer || d.customer,
        title: f.title || [d.name, "見積"].filter(Boolean).join(" "),
        material: d.material ? (known ? d.material : "__custom") : "",
        custom_name: d.material && !known ? d.material : "",
        finish: finishKnown ? d.surface_treatment : "",
        thickness: d.revisions?.[0]?.thickness_mm ? String(d.revisions[0].thickness_mm) : f.thickness,
      }));
    }).catch(setError);
  }, [drawingId, meta]);

  const rev = drawing?.revisions?.find((r: any) => r.id === drawing.revision_id) || drawing?.revisions?.[0];
  const kind = rev?.shape_kind || "";
  const missing = useMemo(() => [!drawingId && "図面", !form.customer.trim() && "顧客", !(Number(form.quantity) > 0) && "数量"].filter(Boolean), [drawingId, form]);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const body: any = { drawing_id: Number(drawingId), revision_id: rev?.id, customer: form.customer.trim(), title: form.title.trim(),
        quantity: Number(form.quantity), due_date: form.due_date || null, staff: form.staff };
      if (form.material === "__custom") body.custom_material = { name: form.custom_name || "マスターにない材質",
        price_per_kg: form.custom_price ? Number(form.custom_price) : null, density_kg_m3: form.custom_density ? Number(form.custom_density) : null };
      else if (form.material) body.material = form.material;
      if (form.finish) body.surface_treatment = form.finish;
      if (kind === "step") Object.assign(body, { k_factor: Number(form.k_factor) || 0.33, k_factor_confirmed: form.k_confirmed });
      if (kind === "dxf") Object.assign(body, { thickness_mm: form.thickness ? Number(form.thickness) : null, flat_confirmed: form.flat });
      const created = await post("/api/estimates", body);
      navigate(`/estimates/${created.estimate_id}/progress?job=${created.job_id}`);
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  }

  return (
    <div className="grid2" style={{ gap: 28, alignItems: "start" }}>
      <section>
        <h2 style={{ marginBottom: 12 }}>見積する図面</h2>
        <div style={{ borderTop: "1px solid var(--line)", borderBottom: "1px solid var(--line)", padding: "12px 0", minHeight: 70 }}>
          {drawing ? (
            <div className="row">
              <Thumb revisionId={rev?.id} hasPdf={!!rev?.pdf_file_id} className="thumb" />
              <div className="spacer">
                <b>{drawing.drawing_no} {rev?.revision ? `Rev.${rev.revision}` : ""} {drawing.name}</b>
                <div className="small muted">{drawing.customer || "顧客未設定"} ・ {drawing.material_name || "材質未設定"} ・ {kind ? kind.toUpperCase() : "形状ファイルなし"}</div>
              </div>
              <span className="tag soft">主図面</span>
              <button className="btn link" onClick={() => setDrawingId("")}>外す</button>
            </div>
          ) : (
            <span className="muted">図面が選ばれていません。</span>
          )}
        </div>
        <Field label="登録済みの図面から選ぶ">
          <select aria-label="図面を選ぶ" value={drawingId} onChange={(e) => setDrawingId(e.target.value)}>
            <option value="">図番・品名で選ぶ…</option>
            {(drawings || []).map((d) => (
              <option key={d.id} value={d.id}>{d.drawing_no} {d.revision ? `Rev.${d.revision}` : ""} {d.name}（{d.customer || "顧客未設定"}）</option>
            ))}
          </select>
        </Field>
        <p className="small muted">
          未登録の図面は先に <Link to="/drawings/register">図面を登録</Link> してください。図面の読み取り結果・形状解析・改訂履歴は登録時の情報を引き継ぎます。
        </p>
      </section>
      <Marked className="panel">
        <h2 style={{ marginBottom: 14 }}>案件情報</h2>
        <Field label="顧客（必須）"><input aria-label="顧客" value={form.customer} onChange={(e) => set("customer", e.target.value)} /></Field>
        <Field label="案件名"><input aria-label="案件名" value={form.title} onChange={(e) => set("title", e.target.value)} /></Field>
        <div className="grid2">
          <Field label="数量（必須）"><input aria-label="数量" type="number" min={1} value={form.quantity} onChange={(e) => set("quantity", e.target.value)} /></Field>
          <Field label="希望納期" hint="金額には影響しません"><input aria-label="希望納期" type="date" value={form.due_date} onChange={(e) => set("due_date", e.target.value)} /></Field>
        </div>
        <div className="grid2">
          <Field label="材質（図面の値が初期値）">
            <select aria-label="材質" value={form.material} onChange={(e) => set("material", e.target.value)}>
              <option value="">（図面の読み取りに任せる）</option>
              {(meta?.materials || []).map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}
              <option value="__custom">マスターにない材質…</option>
            </select>
          </Field>
          <Field label="表面処理（図面の値が初期値）">
            <select aria-label="表面処理" value={form.finish} onChange={(e) => set("finish", e.target.value)}>
              <option value="">（図面の読み取りに任せる）</option>
              {(meta?.surface_treatments || []).map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}
            </select>
          </Field>
        </div>
        {form.material === "__custom" && (
          <div className="grid3">
            <Field label="材質の名前"><input value={form.custom_name} onChange={(e) => set("custom_name", e.target.value)} /></Field>
            <Field label="kg単価（円）"><input type="number" value={form.custom_price} onChange={(e) => set("custom_price", e.target.value)} /></Field>
            <Field label="密度（kg/m³）"><input type="number" value={form.custom_density} onChange={(e) => set("custom_density", e.target.value)} /></Field>
          </div>
        )}
        <Field label="担当">
          <select aria-label="担当" value={form.staff} onChange={(e) => set("staff", e.target.value)}>
            <option value="">（未設定）</option>
            {(meta?.staff || []).map((s) => <option key={s.id}>{s.name}</option>)}
          </select>
        </Field>
        {kind === "step" && (
          <div className="grid2" style={{ borderTop: "1px solid var(--line)", paddingTop: 12 }}>
            <Field label="Kファクター（STEP）"><input type="number" step="0.01" min={0} max={1} value={form.k_factor} onChange={(e) => set("k_factor", e.target.value)} /></Field>
            <label className="check" style={{ marginTop: 18 }}>
              <input type="checkbox" checked={form.k_confirmed} onChange={(e) => set("k_confirmed", e.target.checked)} />
              指定済み加工条件として扱う
            </label>
          </div>
        )}
        {kind === "dxf" && (
          <div className="grid2" style={{ borderTop: "1px solid var(--line)", paddingTop: 12 }}>
            <Field label="板厚 mm（DXFには板厚がありません）"><input aria-label="板厚" type="number" step="0.1" value={form.thickness} onChange={(e) => set("thickness", e.target.value)} /></Field>
            <label className="check" style={{ marginTop: 18 }}>
              <input type="checkbox" checked={form.flat} onChange={(e) => set("flat", e.target.checked)} />
              曲げなし（平板）
            </label>
          </div>
        )}
        <ErrorBox error={error} />
        {missing.length > 0 && <p className="small warn">未入力：{missing.join("・")}</p>}
        <Marked style={{ marginTop: 14 }}>
          <button className="btn primary big" disabled={busy || missing.length > 0} onClick={submit}>
            見積を開始
          </button>
        </Marked>
      </Marked>
    </div>
  );
}
