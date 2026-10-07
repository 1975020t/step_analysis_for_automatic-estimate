import { useEffect, useState } from "react";
import { del, post, put } from "../api";
import { ApiImage, ErrorBox, Field, useMeta, useToast } from "../ui";

const OPTIONS: [string, string][] = [["breakdown", "費目の内訳を表示（項目名と数量のみ。金額は出しません）"], ["unit_and_quantity", "単価・数量を表示"], ["drawing_no", "図番・改訂を表示"],
  ["validity", "有効期限を表示"], ["remarks", "備考欄を表示"], ["seal", "社印（押印欄）を表示"]];

export default function TemplatesPage() {
  const { meta, reload } = useMeta();
  const toast = useToast();
  const [sel, setSel] = useState<number | null>(null);
  const [f, setF] = useState<any>(null);
  const [error, setError] = useState<any>(null);
  const [v, setV] = useState(0);
  const templates = meta?.templates || [];
  useEffect(() => {
    const t = templates.find((x) => x.id === sel) || templates[0];
    if (t) {
      setSel(t.id);
      setF({ ...t, customers: t.customers.join("、") });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta, sel]);
  async function save(next = f) {
    try {
      await put(`/api/templates/${next.id}`, { name: next.name, customers: next.customers.split(/[、,]/).map((s: string) => s.trim()).filter(Boolean), options: next.options, is_default: next.is_default });
      setError(null);
      reload();
      setV((x) => x + 1);
      toast("テンプレートを保存しました");
    } catch (e) {
      setError(e);
    }
  }
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.2fr)", gap: 26, alignItems: "start" }}>
      <section>
        <div style={{ border: "1px solid var(--line-strong)" }}>
          {templates.map((t) => (
            <button key={t.id} onClick={() => setSel(t.id)} style={{ display: "block", width: "100%", textAlign: "left", padding: "10px 12px", border: 0, borderBottom: "1px solid var(--line)", background: t.id === sel ? "var(--accent-soft)" : "var(--panel)", font: "inherit", cursor: "pointer" }}>
              <b>{t.name}</b><div className="small muted">適用：{t.customers.length ? t.customers.join("、") : "全顧客"}{t.is_default ? "（既定）" : ""}</div>
            </button>
          ))}
        </div>
        <button className="btn small" style={{ marginTop: 8 }} onClick={async () => { try { await post("/api/templates", { name: "新しいテンプレート", customers: [], options: {} }); reload(); } catch (e) { setError(e); } }}>＋ テンプレートを追加</button>
        {f && (
          <>
            <div className="grid2" style={{ marginTop: 16 }}>
              <Field label="名前"><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
              <Field label="適用する顧客（、区切り。空なら全顧客）"><input value={f.customers} onChange={(e) => setF({ ...f, customers: e.target.value })} /></Field>
            </div>
            <h2 style={{ margin: "8px 0" }}>表示する項目</h2>
            <div className="stack" style={{ gap: 10 }}>
              {OPTIONS.map(([k, label]) => (
                <label key={k} className="check"><input type="checkbox" checked={!!f.options[k]} onChange={(e) => { const next = { ...f, options: { ...f.options, [k]: e.target.checked } }; setF(next); save(next); }} />{label}</label>
              ))}
              <label className="check muted"><input type="checkbox" disabled checked={false} />原価・粗利は記載しない（固定）</label>
              <label className="check"><input type="checkbox" checked={f.is_default} onChange={(e) => setF({ ...f, is_default: e.target.checked })} />既定のテンプレートにする</label>
            </div>
            <ErrorBox error={error} />
            <div className="row" style={{ marginTop: 14 }}>
              <button className="btn primary" onClick={() => save()}>保存</button>
              {!f.is_default && <button className="btn link" onClick={async () => { try { await del(`/api/templates/${f.id}`); setSel(null); reload(); } catch (e) { setError(e); } }}>削除</button>}
            </div>
          </>
        )}
      </section>
      <section className="preview-paper">
        {sel ? <ApiImage key={`${sel}-${v}`} src={`/api/templates/${sel}/preview.png?v=${v}`} alt="テンプレートのプレビュー" /> : null}
      </section>
    </div>
  );
}
