import { useState } from "react";
import { post, put } from "../api";
import { ErrorBox, Field, Loading, Modal, Tabs, useLoad, useToast } from "../ui";

const EMPTY = { kind: "協力会社", name: "", category: "", specialties: "", price_records: "", avg_lead_days: "", deal_count: 0, rating: "", notes: "" };

export default function PartnersPage() {
  const toast = useToast();
  const [kind, setKind] = useState("協力会社");
  const { data, error, reload } = useLoad<any[]>(`/api/partners?kind=${encodeURIComponent(kind)}`);
  const [edit, setEdit] = useState<any>(null);
  const [saveError, setSaveError] = useState<any>(null);
  async function save() {
    try {
      const body = { ...edit, avg_lead_days: edit.avg_lead_days === "" || edit.avg_lead_days === null ? null : Number(edit.avg_lead_days), deal_count: Number(edit.deal_count) || 0 };
      if (edit.id) await put(`/api/partners/${edit.id}`, body);
      else await post("/api/partners", body);
      setEdit(null);
      setSaveError(null);
      reload();
      toast("取引先を保存しました");
    } catch (e) {
      setSaveError(e);
    }
  }
  return (
    <div>
      <div className="row between" style={{ marginBottom: 14 }}>
        <Tabs value={kind} onChange={setKind} options={[{ value: "協力会社", label: "協力会社" }, { value: "顧客", label: "顧客" }]} />
        <button className="btn" onClick={() => setEdit({ ...EMPTY, kind })}>＋ 取引先を追加</button>
      </div>
      <ErrorBox error={error} />
      {!data ? <Loading /> : (
        <table className="rule">
          <thead><tr><th>{kind}</th><th>区分</th><th>得意な処理・加工</th><th>単価の実績</th><th>平均納期</th><th className="right">取引件数</th><th>評価</th><th /></tr></thead>
          <tbody>
            {data.map((p) => (
              <tr key={p.id}>
                <td><b>{p.name}</b>{p.notes && <span className="sub">{p.notes}</span>}</td>
                <td>{p.category}</td><td>{p.specialties}</td><td>{p.price_records}</td>
                <td>{p.avg_lead_days === null ? "—" : `${p.avg_lead_days}日`}</td>
                <td className="right">{p.deal_count}</td>
                <td>{p.rating && <span className={p.rating === "新規" ? "tag" : "tag soft"}>{p.rating}</span>}</td>
                <td><button className="btn link small" onClick={() => setEdit({ ...p, avg_lead_days: p.avg_lead_days ?? "" })}>編集</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {edit && (
        <Modal title={edit.id ? "取引先を編集" : "取引先を追加"} onClose={() => setEdit(null)}>
          <div className="grid2">
            <Field label="区分"><select value={edit.kind} onChange={(e) => setEdit({ ...edit, kind: e.target.value })}><option>協力会社</option><option>顧客</option></select></Field>
            <Field label="名前（必須）"><input aria-label="取引先名" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
            <Field label="分類（表面処理・溶接・材料など）"><input value={edit.category} onChange={(e) => setEdit({ ...edit, category: e.target.value })} /></Field>
            <Field label="得意な処理・加工"><input value={edit.specialties} onChange={(e) => setEdit({ ...edit, specialties: e.target.value })} /></Field>
            <Field label="単価の実績"><input value={edit.price_records} onChange={(e) => setEdit({ ...edit, price_records: e.target.value })} /></Field>
            <Field label="平均納期（日）"><input type="number" value={edit.avg_lead_days} onChange={(e) => setEdit({ ...edit, avg_lead_days: e.target.value })} /></Field>
            <Field label="取引件数"><input type="number" value={edit.deal_count} onChange={(e) => setEdit({ ...edit, deal_count: e.target.value })} /></Field>
            <Field label="評価（A・B・C・新規など）"><input value={edit.rating} onChange={(e) => setEdit({ ...edit, rating: e.target.value })} /></Field>
          </div>
          <Field label="メモ"><input value={edit.notes} onChange={(e) => setEdit({ ...edit, notes: e.target.value })} /></Field>
          <ErrorBox error={saveError} />
          <button className="btn primary" disabled={!edit.name.trim()} onClick={save}>保存</button>
        </Modal>
      )}
    </div>
  );
}
