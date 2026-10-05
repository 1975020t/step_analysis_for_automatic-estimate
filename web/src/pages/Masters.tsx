import { useState } from "react";
import { day, post, put } from "../api";
import { ErrorBox, Loading, Tabs, useLoad, useMeta, useToast } from "../ui";

const TABLES: Record<string, { label: string; columns: [string, string][] }> = {
  materials: { label: "材料", columns: [["display_name", "名称"], ["density_kg_m3", "密度 kg/m³"], ["price_per_kg", "kg単価 円"], ["waste_factor", "歩留まり係数"], ["charge_scope", "課金の範囲"], ["aliases", "別名（|区切り）"]] },
  processes: { label: "工程（追加加工を含む）", columns: [["display_name", "名称"], ["calculation_type", "計算の種類"], ["unit_price", "単価 円"], ["unit", "単位"], ["charge_scope", "課金の範囲"], ["aliases", "別名（|区切り）"]] },
  surface_treatments: { label: "表面処理", columns: [["display_name", "名称"], ["unit_price", "単価 円/個"], ["charge_scope", "課金の範囲"], ["aliases", "別名（|区切り）"]] },
  pricing_policy: { label: "価格方針", columns: [["value", "値"], ["description", "説明"]] },
  company: { label: "自社情報", columns: [["value", "値"], ["description", "説明"]] },
};

export default function MastersPage() {
  const toast = useToast();
  const { reload: reloadMeta } = useMeta();
  const [table, setTable] = useState("materials");
  const { data, error, setData } = useLoad<any[]>(`/api/master-tables/${table}`);
  const [edit, setEdit] = useState<any>(null);
  const [saveError, setSaveError] = useState<any>(null);
  const spec = TABLES[table];
  async function save() {
    try {
      const values = Object.fromEntries(spec.columns.map(([k]) => [k, edit[k] ?? ""]));
      const rows = edit.isNew ? await post(`/api/master-tables/${table}`, { code: edit.code, values }) : await put(`/api/master-tables/${table}/${encodeURIComponent(edit.code)}`, { values, version: edit.version });
      setData(rows);
      setEdit(null);
      setSaveError(null);
      reloadMeta();
      toast("マスターを保存しました（これから計算する見積に使われます）");
    } catch (e) {
      setSaveError(e);
    }
  }
  return (
    <div>
      <div className="row between" style={{ marginBottom: 14 }}>
        <Tabs value={table} onChange={(t) => { setTable(t); setEdit(null); }} options={Object.entries(TABLES).map(([k, v]) => ({ value: k, label: v.label }))} />
        <button className="btn" onClick={() => setEdit({ isNew: true, code: "", charge_scope: "per_part" })}>＋ 行を追加</button>
      </div>
      <ErrorBox error={error || saveError} />
      {!data ? <Loading /> : (
        <table className="rule" data-testid="master-table">
          <thead><tr><th>コード</th>{spec.columns.map(([, l]) => <th key={l}>{l}</th>)}<th>更新日</th><th /></tr></thead>
          <tbody>
            {edit?.isNew && <EditRow spec={spec} edit={edit} setEdit={setEdit} save={save} isNew />}
            {data.map((r) => edit && !edit.isNew && edit.code === r.code ? <EditRow key={r.code} spec={spec} edit={edit} setEdit={setEdit} save={save} /> : (
              <tr key={r.code}>
                <td><b>{r.code}</b></td>
                {spec.columns.map(([k]) => <td key={k} className={k === "aliases" ? "small" : ""} style={k === "aliases" ? { maxWidth: 280 } : undefined}>{r[k]}</td>)}
                <td className="small">{day(r.updated_at)}</td>
                <td><button className="btn link small" onClick={() => setEdit({ ...r })}>編集</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="small muted">見積の計算はこのマスターの値だけを使います。変更はこれから計算する見積に使われ、発行済みの帳票の金額は変わりません。マスターにない材料・加工・表面処理は、見積ごとに単価を入力します（マスターには自動で登録しません）。</p>
    </div>
  );
}

function EditRow({ spec, edit, setEdit, save, isNew }: any) {
  return (
    <tr>
      <td>{isNew ? <input aria-label="コード" value={edit.code} onChange={(e) => setEdit({ ...edit, code: e.target.value })} /> : <b>{edit.code}</b>}</td>
      {spec.columns.map(([k, label]: [string, string]) => (
        <td key={k}>{k === "charge_scope" ? (
          <select aria-label={label} value={edit[k] || "per_part"} onChange={(e) => setEdit({ ...edit, [k]: e.target.value })}><option value="per_part">1個ごと</option><option value="per_order">1注文に1回</option></select>
        ) : <input aria-label={label} value={edit[k] ?? ""} onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} />}</td>
      ))}
      <td />
      <td className="nowrap"><button className="btn small primary" onClick={save}>保存</button><button className="btn link small" onClick={() => setEdit(null)}>やめる</button></td>
    </tr>
  );
}
