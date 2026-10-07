import { useEffect, useState } from "react";
import { put } from "../api";
import { ErrorBox, Loading, useLoad, useMeta, useToast } from "../ui";

export default function StatusesPage() {
  const { meta, reload: reloadMeta } = useMeta();
  const toast = useToast();
  const { data, reload } = useLoad<any[]>("/api/statuses");
  const [rows, setRows] = useState<any[] | null>(null);
  const [error, setError] = useState<any>(null);
  useEffect(() => setRows(data ? data.map((x) => ({ ...x })) : null), [data]);
  if (!rows) return <Loading />;
  const set = (i: number, k: string, v: any) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= rows.length) return;
    const next = [...rows];
    [next[i], next[j]] = [next[j], next[i]];
    setRows(next);
  };
  const dirty = JSON.stringify(rows) !== JSON.stringify(data);
  async function save() {
    try {
      await put("/api/statuses", { statuses: rows!.map((r) => ({ id: r.id, name: r.name, color: r.color, phase: r.phase, visible: r.visible })) });
      setError(null);
      reload();
      reloadMeta();
      toast("ステータスを保存しました");
    } catch (e) {
      setError(e);
    }
  }
  return (
    <div style={{ maxWidth: 1080 }}>
      <p className="lead">フェーズは、ホームの進捗・To Doリストと「見積・案件」の絞り込みに使います（1 見積作成中／2 見積確認中／3 回答待ち／4 製造・出荷／完了分）。</p>
      <div className="row between" style={{ marginBottom: 12 }}>
        <span />
        <div className="row">
          <button className="btn" onClick={() => setRows([...rows, { id: null, name: "", color: "blue", phase: "drafting", visible: true, count: 0, role: "" }])}>＋ ステータスを追加</button>
          <button className="btn primary" disabled={!dirty} onClick={save}>変更を保存</button>
        </div>
      </div>
      <ErrorBox error={error} />
      <table className="rule">
        <thead><tr><th>順番</th><th>ステータス名</th><th>色</th><th>フェーズ</th><th>表示</th><th className="right">案件数</th><th /></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.id ?? `n${i}`}>
              <td className="nowrap small">{String(i + 1).padStart(2, "0")}
                <button className="btn link small" aria-label="上へ" onClick={() => move(i, -1)}>↑</button><button className="btn link small" aria-label="下へ" onClick={() => move(i, 1)}>↓</button></td>
              <td><input aria-label="ステータス名" value={r.name} onChange={(e) => set(i, "name", e.target.value)} />
                {r.role && <span className="sub">{({ drafting: "見積を作ると最初にこの状態", issued: "見積書を発行するとこの状態", won: "受注", lost: "失注（理由を記録）", hold: "保留", shipped: "出荷済", done: "完了" } as any)[r.role]}</span>}</td>
              <td className="nowrap"><i className={`dot ${r.color}`} />
                <select aria-label="色" value={r.color} onChange={(e) => set(i, "color", e.target.value)} style={{ width: 110 }}>
                  {Object.entries(meta?.colors || {}).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></td>
              <td><select aria-label="フェーズ" value={r.phase} onChange={(e) => set(i, "phase", e.target.value)}>
                {(meta?.phases || []).map((p) => <option key={p.key} value={p.key}>{p.number ? `${p.number} ` : ""}{p.label}</option>)}</select></td>
              <td><input type="checkbox" aria-label="表示" checked={r.visible} onChange={(e) => set(i, "visible", e.target.checked)} /></td>
              <td className="right">{r.count ? `${r.count}件` : "—"}</td>
              <td><button className="btn link small" disabled={!!r.count || ["drafting", "issued", "won", "lost"].includes(r.role)} onClick={() => setRows(rows.filter((_, j) => j !== i))}>削除</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="small muted">全{rows.length}件 ・ 案件のあるステータスと、見積の作成・発行・受注・失注に使うステータスは削除できません。</p>
    </div>
  );
}
