import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { blobUrl, day, get, post, yen } from "../api";
import { ApiImage, ErrorBox, Field, useMeta, useToast } from "../ui";

const KINDS = [{ value: "quote", label: "見積書" }, { value: "delivery", label: "納品書" }, { value: "invoice", label: "請求書" }];
const LABEL: Record<string, string> = { quote: "見積書", delivery: "納品書", invoice: "請求書" };

export default function DocumentsPage() {
  const { meta } = useMeta();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const [kind, setKind] = useState(params.get("kind") || "quote");
  const [quoteId, setQuoteId] = useState(params.get("quote") || "");
  const [templateId, setTemplateId] = useState("");
  const [person, setPerson] = useState("");
  const [remarks, setRemarks] = useState("");
  const [estimates, setEstimates] = useState<any[]>([]);
  const [estimate, setEstimate] = useState<any>(null);
  const [history, setHistory] = useState<any[]>([]);
  const [error, setError] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [previewKey, setPreviewKey] = useState(0);

  const loadHistory = () => get("/api/issued?limit=30").then(setHistory).catch(() => {});
  useEffect(() => {
    get("/api/estimates").then(setEstimates).catch(setError);
    loadHistory();
  }, []);
  useEffect(() => {
    setError(null);
    if (!quoteId) return setEstimate(null);
    get(`/api/estimates/${quoteId}`).then(setEstimate).catch(setError);
    setParams({ quote: quoteId, kind }, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [quoteId, kind]);
  useEffect(() => {
    if (!estimate || templateId) return;
    const t = (meta?.templates || []).find((x) => x.customers.includes(estimate.customer)) || (meta?.templates || []).find((x) => x.is_default);
    if (t) setTemplateId(String(t.id));
  }, [estimate, meta, templateId]);

  const blockers: { message: string }[] = (estimate?.blockers?.[kind] || []).map((m: string) => ({ message: m }));
  const canIssue = estimate && blockers.length === 0;
  const previewSrc = canIssue ? `/api/estimates/${quoteId}/documents/preview.png?kind=${kind}${templateId ? `&template_id=${templateId}` : ""}&v=${previewKey}` : "";

  async function issue() {
    setBusy(true);
    try {
      const doc = await post(`/api/estimates/${quoteId}/documents`, { kind, template_id: templateId ? Number(templateId) : null, person, remarks });
      toast(`${LABEL[kind]} ${doc.number} を発行しました`);
      setError(null);
      loadHistory();
      setEstimate(await get(`/api/estimates/${quoteId}`));
      setPreviewKey((k) => k + 1);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function download() {
    try {
      const doc = history.find((d) => d.quote_id === Number(quoteId) && d.kind === kind);
      if (!doc) return setError(`${LABEL[kind]}は未発行です。先に発行してください。`);
      const url = await blobUrl(`${doc.url}?download=true`);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${doc.number}_${LABEL[kind]}.pdf`;
      a.click();
    } catch (e) {
      setError(e);
    }
  }

  const docsOf = (k: string) => (estimate?.documents || []).filter((d: any) => d.kind === k);
  const total = estimate?.result?.price?.total;

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.1fr)", gap: 26, alignItems: "start" }}>
        <section className="steps-form stack" style={{ gap: 22 }}>
          <div>
            <h2 style={{ marginBottom: 8 }}><span className="no">1</span>対象案件</h2>
            <Field label="案件">
              <select aria-label="対象の案件" value={quoteId} onChange={(e) => setQuoteId(e.target.value)}>
                <option value="">選択してください</option>
                {estimates.map((q) => <option key={q.id} value={q.id}>{q.number}　{q.name || q.title}　{q.customer}（{q.status.name}）</option>)}
              </select>
            </Field>
          </div>
          <div>
            <h2 style={{ marginBottom: 8 }}><span className="no">2</span>帳票の種類</h2>
            <div className="kinds-list" role="radiogroup" aria-label="帳票の種類">
              {KINDS.map((k) => {
                const issued = docsOf(k.value);
                const reasons: string[] = estimate?.blockers?.[k.value] || [];
                return (
                  <label key={k.value} className={kind === k.value ? "on" : ""}>
                    <input type="radio" name="kind" aria-label={k.label} checked={kind === k.value} onChange={() => setKind(k.value)} />
                    <span>
                      <b>{k.label}</b>
                      <span className="small" style={{ display: "block" }}>
                        {!estimate ? <span className="muted">案件を選択すると発行可否を表示</span>
                          : reasons.length ? <span className="warn">発行不可：{reasons[0]}{reasons.length > 1 ? ` ほか${reasons.length - 1}件` : ""}</span>
                          : <span className="ok">発行可能</span>}
                        {issued.length > 0 && <span className="muted">（発行済み {issued.map((d: any) => d.number).join("・")}）</span>}
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
            <p className="small muted" style={{ margin: "4px 0 0" }}>納品書・請求書は受注登録済みの案件のみ、見積書と同じ金額で発行可能</p>
            {blockers.length > 0 && (
              <div className="notice" data-testid="not-issuable" style={{ marginTop: 10 }}>
                <b>{LABEL[kind]}を発行できない理由</b>
                <ul>{blockers.map((b, i) => <li key={i}>{b.message}</li>)}</ul>
                {estimate && <Link to={kind === "quote" ? `/estimates/${estimate.id}?tab=conditions` : `/estimates/${estimate.id}`}><b>見積結果を開く →</b></Link>}
              </div>
            )}
          </div>
          <div>
            <h2 style={{ marginBottom: 8 }}><span className="no">3</span>宛先・テンプレート</h2>
            <div className="grid2">
              <Field label="宛先担当者（「様」は自動付与）"><input aria-label="宛先担当者" value={person} onChange={(e) => setPerson(e.target.value)} /></Field>
              <Field label="テンプレート">
                <select aria-label="テンプレート" value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
                  {(meta?.templates || []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
              </Field>
            </div>
            <Field label="備考（1行）"><input aria-label="備考" value={remarks} onChange={(e) => setRemarks(e.target.value)} /></Field>
          </div>
        </section>

        <section>
          <div className="row between" style={{ marginBottom: 8 }}><h2>発行プレビュー</h2><span className="small muted">原価・粗利は記載されません</span></div>
          <div className="preview-paper" data-testid="document-preview">
            {previewSrc ? <ApiImage key={previewSrc} src={previewSrc} alt={`${LABEL[kind]}のプレビュー`} />
              : <div className="hatch" style={{ height: 480, display: "grid", placeItems: "center" }}><span className="muted">{estimate ? "発行可能になるとプレビューを表示します" : "対象案件を選択してください"}</span></div>}
          </div>
          <div className="totals" style={{ marginTop: 12 }}>
            <div className="row between">
              <div><div className="label">合計（税込）</div><div className="big" style={{ margin: 0 }}>{canIssue && total !== undefined ? yen(total) : "—"}</div></div>
              <div className="stack" style={{ gap: 6, alignItems: "flex-end" }}>
                <button className="btn primary" disabled={!canIssue || busy} onClick={issue}>{LABEL[kind]}を発行</button>
                <button className="btn link small" onClick={download} disabled={!estimate}>発行済みのPDFをダウンロード</button>
              </div>
            </div>
            <div className="small muted">発行時に番号を採番し、PDFを保存</div>
          </div>
          <ErrorBox error={error} />
        </section>
      </div>

      <h2 style={{ margin: "28px 0 8px" }}>発行履歴</h2>
      <table className="rule" data-testid="issued">
        <tbody>
          {history.length === 0 && <tr><td className="muted">まだ発行していません。</td></tr>}
          {history.map((d) => (
            <tr key={d.id}>
              <td style={{ width: 80 }}><span className="tag plain">{LABEL[d.kind]}</span></td>
              <td><b>{d.number}</b><span className="sub">{d.drawing_no} {d.part_name}</span></td>
              <td>{d.customer}</td>
              <td className="right">{yen(d.total)}</td>
              <td className="small">{day(d.issued_at)} {d.issued_by}</td>
              <td><button className="btn link small" onClick={async () => window.open(await blobUrl(d.url), "_blank")}>PDFを開く</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
