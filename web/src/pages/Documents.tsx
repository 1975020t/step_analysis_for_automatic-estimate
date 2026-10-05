import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { blobUrl, day, get, post, yen } from "../api";
import { ApiImage, ErrorBox, Field, Marked, Tabs, useMeta, useToast } from "../ui";

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
      if (!doc) return setError(`${LABEL[kind]}はまだ発行していません。先に発行してください。`);
      const url = await blobUrl(`${doc.url}?download=true`);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${doc.number}_${LABEL[kind]}.pdf`;
      a.click();
    } catch (e) {
      setError(e);
    }
  }

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.15fr)", gap: 26, alignItems: "start" }}>
      <section>
        <Tabs value={kind} onChange={setKind} options={KINDS} />
        <div style={{ marginTop: 14 }}>
          <Field label="対象の案件">
            <select aria-label="対象の案件" value={quoteId} onChange={(e) => setQuoteId(e.target.value)}>
              <option value="">選んでください</option>
              {estimates.map((q) => <option key={q.id} value={q.id}>{q.number} {q.name || q.title}（{q.customer}）{q.status.name}</option>)}
            </select>
          </Field>
          <Field label="テンプレート">
            <select aria-label="テンプレート" value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
              {(meta?.templates || []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </Field>
          <div className="grid2">
            <Field label="宛先の担当（部署・氏名、様をつけて印字）"><input value={person} onChange={(e) => setPerson(e.target.value)} /></Field>
            <Field label="備考（追加）"><input value={remarks} onChange={(e) => setRemarks(e.target.value)} /></Field>
          </div>
        </div>
        {blockers.length > 0 && (
          <div className="notice" data-testid="not-issuable">
            <b>！ 未入力の項目があるため発行できません</b>
            <ul>{blockers.map((b, i) => <li key={i}>{b.message}</li>)}</ul>
            {estimate && <Link to={`/estimates/${estimate.id}`}><b>見積結果で入力する →</b></Link>}
          </div>
        )}
        <ErrorBox error={error} />
        <div className="row" style={{ margin: "14px 0" }}>
          <button className="btn" onClick={download} disabled={!estimate}>PDFをダウンロード</button>
          <Marked><button className="btn primary" disabled={!canIssue || busy} onClick={issue}>{LABEL[kind]}を発行</button></Marked>
        </div>
        <h2 style={{ margin: "22px 0 8px" }}>発行履歴</h2>
        <table className="rule">
          <tbody>
            {history.length === 0 && <tr><td className="muted">まだ発行していません。</td></tr>}
            {history.map((d) => (
              <tr key={d.id}>
                <td><span className="tag plain">{LABEL[d.kind]}</span></td>
                <td><a href={d.url} target="_blank" rel="noreferrer"><b>{d.number}</b></a><span className="sub">{d.drawing_no} {d.part_name}</span></td>
                <td>{d.customer}</td>
                <td className="right">{yen(d.total)}</td>
                <td className="small">{day(d.issued_at)} {d.issued_by}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="preview-paper" data-testid="document-preview">
        {previewSrc ? <ApiImage key={previewSrc} src={previewSrc} alt={`${LABEL[kind]}のプレビュー`} />
          : <div className="hatch" style={{ height: 520, display: "grid", placeItems: "center" }}><span className="muted">{estimate ? "発行できる状態になるとプレビューを表示します" : "対象の案件を選んでください"}</span></div>}
        <p className="small muted" style={{ marginBottom: 0 }}>プレビューは発行するPDFと同じものです（番号は発行時に決まります）。原価・粗利は記載しません。</p>
      </section>
    </div>
  );
}
