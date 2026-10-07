import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { del, post, put } from "../api";
import { ErrorBox, useMeta, useToast } from "../ui";

const TYPES = [{ value: "text", label: "テキスト" }, { value: "select", label: "選択肢" }, { value: "number_range", label: "数値の範囲" }, { value: "date_range", label: "日付の範囲" }];

export default function CategoriesPage() {
  const { meta, reload } = useMeta();
  const navigate = useNavigate();
  const toast = useToast();
  const [error, setError] = useState<any>(null);
  const [newGroup, setNewGroup] = useState("");
  const [newChild, setNewChild] = useState<Record<number, string>>({});
  const [attrs, setAttrs] = useState<any[]>([]);
  useEffect(() => setAttrs((meta?.attributes || []).map((a) => ({ ...a, options: a.builtin ? a.options : a.options }))), [meta]);

  const act = async (fn: () => Promise<any>, done = "保存しました") => {
    try {
      await fn();
      setError(null);
      reload();
      toast(done);
    } catch (e) {
      setError(e);
    }
  };
  const move = (i: number, dir: number) => {
    const next = [...attrs];
    const j = i + dir;
    if (j < 0 || j >= next.length) return;
    [next[i], next[j]] = [next[j], next[i]];
    setAttrs(next);
  };

  return (
    <div className="grid2" style={{ gap: 30, alignItems: "start" }}>
      <section>
        <div className="row between" style={{ borderBottom: "1px solid var(--line)", paddingBottom: 10 }}>
          <h2>分類（フォルダ）</h2>
          <div className="row">
            <input aria-label="分類のグループ名" placeholder="例：材質別" value={newGroup} onChange={(e) => setNewGroup(e.target.value)} style={{ width: 150 }} />
            <button className="btn" disabled={!newGroup.trim()} onClick={() => act(() => post("/api/categories", { name: newGroup }), "分類を追加しました").then(() => setNewGroup(""))}>＋ 分類を追加</button>
          </div>
        </div>
        <ErrorBox error={error} />
        {(meta?.categories || []).map((g) => (
          <div key={g.id} style={{ borderBottom: "1px solid var(--line)", padding: "12px 0" }}>
            <div className="row between"><b>{g.name}</b><button className="btn link small" onClick={() => act(() => del(`/api/categories/${g.id}`), "削除しました")}>削除</button></div>
            <div className="row" style={{ gap: 6, margin: "6px 0" }}>
              {g.children.map((c) => (
                <span key={c.id} className="tag plain">{c.name}（{c.count}）
                  <button className="btn link small" style={{ padding: "0 0 0 4px" }} aria-label={`${c.name}を削除`} onClick={() => act(() => del(`/api/categories/${c.id}`), "削除しました")}>✕</button></span>
              ))}
            </div>
            <div className="row">
              <input aria-label={`${g.name}に追加`} placeholder="フォルダ名" value={newChild[g.id] || ""} onChange={(e) => setNewChild({ ...newChild, [g.id]: e.target.value })} style={{ width: 200 }} />
              <button className="btn small" disabled={!newChild[g.id]?.trim()} onClick={() => act(() => post("/api/categories", { name: newChild[g.id], parent_id: g.id })).then(() => setNewChild({ ...newChild, [g.id]: "" }))}>追加</button>
            </div>
          </div>
        ))}
      </section>
      <section>
        <div className="row between" style={{ paddingBottom: 6 }}>
          <h2>図面の属性・検索項目</h2>
          <button className="btn" onClick={() => setAttrs([...attrs, { id: null, label: "", input_type: "text", options: [], unit: "", searchable: true, builtin: false }])}>＋ 項目を追加</button>
        </div>
        <p className="small muted">「検索に使う」をオンにした項目が、図面一覧の検索条件に並びます。</p>
        <table className="rule">
          <thead><tr><th>順番</th><th>項目名</th><th>入力の種類</th><th>選択肢・単位</th><th>検索</th></tr></thead>
          <tbody>
            {attrs.map((a, i) => (
              <tr key={a.id ?? `new${i}`}>
                <td className="nowrap small">{String(i + 1).padStart(2, "0")}
                  <button className="btn link small" aria-label="上へ" onClick={() => move(i, -1)}>↑</button><button className="btn link small" aria-label="下へ" onClick={() => move(i, 1)}>↓</button></td>
                <td><input aria-label="項目名" value={a.label} onChange={(e) => setAttrs(attrs.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} />{!a.builtin && a.id && <span className="sub">自社追加</span>}</td>
                <td><select aria-label="入力の種類" disabled={a.builtin} value={a.input_type} onChange={(e) => setAttrs(attrs.map((x, j) => (j === i ? { ...x, input_type: e.target.value } : x)))}>
                  {TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</select></td>
                <td>{a.builtin ? <span className="small muted">{a.unit || (a.input_type === "select" ? "マスタ・ステータスから" : "")}</span> : a.input_type === "select" ? (
                  <input aria-label="選択肢" placeholder="カンマ区切り" value={(a.options || []).join(",")} onChange={(e) => setAttrs(attrs.map((x, j) => (j === i ? { ...x, options: e.target.value.split(",") } : x)))} />
                ) : a.input_type === "number_range" ? <input aria-label="単位" placeholder="単位" value={a.unit} onChange={(e) => setAttrs(attrs.map((x, j) => (j === i ? { ...x, unit: e.target.value } : x)))} /> : null}</td>
                <td><input type="checkbox" aria-label="検索に使う" checked={a.searchable} onChange={(e) => setAttrs(attrs.map((x, j) => (j === i ? { ...x, searchable: e.target.checked } : x)))} />
                  {!a.builtin && <button className="btn link small" onClick={() => setAttrs(attrs.filter((_, j) => j !== i))}>削除</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn primary" onClick={() => act(() => put("/api/attributes", { attributes: attrs.map((a) => ({ id: a.id, label: a.label, input_type: a.input_type, options: a.builtin ? [] : (a.options || []), unit: a.unit, searchable: a.searchable })) }))}>項目を保存</button>
          <button className="btn" onClick={() => navigate("/drawings")}>図面一覧で検索条件を確認</button>
        </div>
      </section>
    </div>
  );
}
