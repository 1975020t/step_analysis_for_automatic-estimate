import { useState } from "react";
import { upload } from "../api";
import { ErrorBox, Field, Marked, useToast } from "../ui";

export default function ImportPage() {
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [cols, setCols] = useState<any>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  async function choose(f: File | null) {
    setFile(f);
    setResult(null);
    setError(null);
    if (!f) return setCols(null);
    try {
      const c = await upload("/api/history/columns", f);
      setCols(c);
      setMapping(c.mapping);
    } catch (e) {
      setError(e);
    }
  }
  async function run() {
    if (!file) return;
    setBusy(true);
    try {
      const r = await upload("/api/history/import", file, { mapping: JSON.stringify(Object.fromEntries(Object.entries(mapping).filter(([, v]) => v))) });
      setResult(r);
      toast(`${r.added}件を取り込みました`);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div style={{ maxWidth: 1000 }}>
      <p className="lead">今までの見積を記録したCSVを取り込みます。取り込んだ見積は、類似実績と振り返り分析に使われます。同じ見積番号・日付・顧客の行は二重に取り込みません。</p>
      <Field label="CSVファイル"><input type="file" accept=".csv" aria-label="CSVファイル" onChange={(e) => choose(e.target.files?.[0] || null)} /></Field>
      <ErrorBox error={error} />
      {cols && (
        <>
          <div className="section-title"><h2>列の対応づけ</h2><span className="small muted">{cols.rows}行 ・ 列名から自動で対応づけました。違うところだけ直してください。</span></div>
          <table className="rule">
            <thead><tr><th>項目</th><th>CSVの列</th><th>1行目の値</th></tr></thead>
            <tbody>
              {cols.fields.map((f: any) => (
                <tr key={f.key}>
                  <td>{f.label}{["date", "customer"].includes(f.key) && <span className="tag" style={{ marginLeft: 6 }}>必須</span>}</td>
                  <td><select aria-label={`${f.label}の列`} value={mapping[f.key] || ""} onChange={(e) => setMapping({ ...mapping, [f.key]: e.target.value })}>
                    <option value="">（使わない）</option>{cols.columns.map((c: string) => <option key={c}>{c}</option>)}</select></td>
                  <td className="small muted">{mapping[f.key] ? cols.sample?.[0]?.[mapping[f.key]] : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Marked style={{ display: "inline-block", marginTop: 14 }}><button className="btn primary" disabled={busy} onClick={run}>取り込む</button></Marked>
        </>
      )}
      {result && (
        <div className="notice" style={{ marginTop: 16 }}>
          <b>読み込み {result.read}件 ・ 追加 {result.added}件 ・ 既にあり {result.skipped}件</b>
          {result.problems.length > 0 && <ul>{result.problems.slice(0, 20).map((p: string) => <li key={p}>{p}</li>)}</ul>}
        </div>
      )}
    </div>
  );
}
