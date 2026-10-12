import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { day, post, put } from "../api";
import { ErrorBox, Loading, Tabs, useLoad, useMeta, useToast } from "../ui";

const SCOPE: Record<string, string> = { per_part: "1個ごと", per_order: "1注文に1回" };
const CALC: Record<string, string> = { per_mm: "長さ（mm）あたり", per_hole: "穴あたり", per_bend: "曲げあたり", per_location: "か所あたり",
  per_piece: "個あたり", per_point: "点あたり", flat: "1式" };
const UNIT: Record<string, string> = { mm: "mm", hole: "穴", bend: "曲げ", location: "か所", piece: "か所", point: "点", job: "式", part: "個" };
const CHOICES: Record<string, Record<string, string>> = { charge_scope: SCOPE, calculation_type: CALC, unit: UNIT };

const TABLES: Record<string, { label: string; columns: [string, string][]; keyed?: boolean }> = {
  materials: { label: "材料", columns: [["display_name", "名称"], ["density_kg_m3", "密度 kg/m³"], ["price_per_kg", "kg単価 円"], ["waste_factor", "歩留まり係数"], ["charge_scope", "課金の範囲"], ["aliases", "別名"]] },
  processes: { label: "工程（追加加工を含む）", columns: [["display_name", "名称"], ["calculation_type", "計算のしかた"], ["unit_price", "単価 円"], ["unit", "単位"], ["charge_scope", "課金の範囲"], ["aliases", "別名"]] },
  surface_treatments: { label: "表面処理", columns: [["display_name", "名称"], ["unit_price", "単価 円/個"], ["charge_scope", "課金の範囲"], ["aliases", "別名"]] },
  pricing_policy: { label: "価格方針", columns: [["value", "値"]], keyed: true },
  company: { label: "自社情報", columns: [["value", "値"]], keyed: true },
};

/** A rate of the pricing policy (0.25) is shown and entered as a percentage (25%). */
const isRate = (table: string, code: string) => table === "pricing_policy" && code.endsWith("_rate");
const itemName = (r: any) => (r.description || r.code).replace(/（.*$/, "");

function shown(table: string, r: any, k: string): string {
  const v = r[k];
  if (v === null || v === undefined || v === "") return "";
  if (CHOICES[k]) return CHOICES[k][v] ?? v;
  if (k === "aliases") return String(v).split("|").join("、");
  if (k === "value" && isRate(table, r.code)) return `${+(Number(v) * 100).toFixed(4)}%`;
  return String(v);
}

export default function MastersPage() {
  const toast = useToast();
  const { reload: reloadMeta } = useMeta();
  const [params, setParams] = useSearchParams();
  const initial = params.get("tab") || "materials";
  const [table, setTableState] = useState(TABLES[initial] ? initial : "materials");
  const setTable = (t: string) => { setTableState(t); setParams({ tab: t }, { replace: true }); };
  const { data, error, setData } = useLoad<any[]>(`/api/master-tables/${table}`);
  const [edit, setEdit] = useState<any>(null);
  const [saveError, setSaveError] = useState<any>(null);
  const spec = TABLES[table];
  async function save() {
    try {
      const values = Object.fromEntries(spec.columns.map(([k]) => [k, edit[k] ?? ""]));
      if (isRate(table, edit.code) && values.value !== "") values.value = String(Number(values.value) / 100);
      if (values.aliases !== undefined) values.aliases = String(values.aliases).replace(/[,，]/g, "、");
      const rows = edit.isNew ? await post(`/api/master-tables/${table}`, { code: edit.code, values }) : await put(`/api/master-tables/${table}/${encodeURIComponent(edit.code)}`, { values, version: edit.version });
      setData(rows);
      setEdit(null);
      setSaveError(null);
      reloadMeta();
      toast("保存しました");
    } catch (e) {
      setSaveError(e);
    }
  }
  return (
    <div>
      <div className="row between" style={{ marginBottom: 14 }}>
        <Tabs value={table} onChange={(t) => { setTable(t); setEdit(null); }} options={Object.entries(TABLES).map(([k, v]) => ({ value: k, label: v.label }))} />
        {!spec.keyed && <button className="btn" onClick={() => setEdit({ isNew: true, code: "", charge_scope: "per_part", calculation_type: "flat", unit: "part" })}>＋ 行を追加</button>}
      </div>
      <ErrorBox error={error || saveError} />
      {!data ? <Loading /> : (
        <table className="rule" data-testid="master-table">
          <thead><tr><th>{spec.keyed ? "項目" : "コード"}</th>{spec.columns.map(([, l]) => <th key={l}>{l}</th>)}<th>更新日</th><th /></tr></thead>
          <tbody>
            {edit?.isNew && <EditRow spec={spec} edit={edit} setEdit={setEdit} save={save} isNew />}
            {data.map((r) => edit && !edit.isNew && edit.code === r.code ? <EditRow key={r.code} spec={spec} edit={edit} setEdit={setEdit} save={save} /> : (
              <tr key={r.code}>
                <td><b>{spec.keyed ? itemName(r) : r.code}</b></td>
                {spec.columns.map(([k]) => <td key={k} className={k === "aliases" ? "small" : ""} style={k === "aliases" ? { maxWidth: 280 } : undefined}>{shown(table, r, k)}</td>)}
                <td className="small">{day(r.updated_at)}</td>
                <td><button className="btn link small" onClick={() => setEdit(editable(table, r))}>編集</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="small muted">変更はこれから計算する見積に使われます。発行済みの帳票の金額は変わりません。</p>
    </div>
  );
}

function editable(table: string, r: any) {
  const out = { ...r };
  if (isRate(table, r.code) && r.value !== "") out.value = String(+(Number(r.value) * 100).toFixed(4));
  if (r.aliases !== undefined) out.aliases = String(r.aliases || "").split("|").filter(Boolean).join("、");
  return out;
}

function EditRow({ spec, edit, setEdit, save, isNew }: any) {
  return (
    <tr>
      <td>{isNew ? <input aria-label="コード" value={edit.code} onChange={(e) => setEdit({ ...edit, code: e.target.value })} /> : <b>{spec.keyed ? itemName(edit) : edit.code}</b>}</td>
      {spec.columns.map(([k, label]: [string, string]) => (
        <td key={k}>{CHOICES[k] ? (
          <select aria-label={label} value={edit[k] ?? ""} onChange={(e) => setEdit({ ...edit, [k]: e.target.value })}>
            {Object.entries(CHOICES[k]).filter(([v], i, all) => k !== "unit" || all.findIndex(([, l]) => l === CHOICES[k][v]) === i || v === edit[k]).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        ) : (
          <span className="row" style={{ gap: 4, flexWrap: "nowrap" }}>
            <input aria-label={label} value={edit[k] ?? ""} onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} />
            {k === "value" && spec.keyed && edit.code?.endsWith("_rate") && <span>%</span>}
          </span>
        )}</td>
      ))}
      <td />
      <td className="nowrap"><button className="btn small primary" onClick={save}>保存</button><button className="btn link small" onClick={() => setEdit(null)}>キャンセル</button></td>
    </tr>
  );
}
