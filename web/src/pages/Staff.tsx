import { useEffect, useState } from "react";
import { put, session } from "../api";
import { ErrorBox, Field, Loading, Marked, useLoad, useMeta, useToast } from "../ui";

export default function StaffPage() {
  const toast = useToast();
  const { reload: reloadMeta } = useMeta();
  const { data, reload } = useLoad<any[]>("/api/staff");
  const [rows, setRows] = useState<any[] | null>(null);
  const [actor, setActor] = useState(session.actor());
  const [token, setToken] = useState(session.token());
  const [error, setError] = useState<any>(null);
  useEffect(() => setRows(data ? data.filter((s) => s.active) : null), [data]);
  if (!rows) return <Loading />;
  async function save() {
    try {
      await put("/api/staff", { staff: rows!.map((r) => ({ id: r.id, name: r.name, active: true })) });
      setError(null);
      reload();
      reloadMeta();
      toast("担当者を保存しました");
    } catch (e) {
      setError(e);
    }
  }
  return (
    <div className="grid2" style={{ gap: 30, alignItems: "start", maxWidth: 1000 }}>
      <section>
        <h2 style={{ marginBottom: 8 }}>担当者の一覧</h2>
        <table className="rule">
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.id ?? `n${i}`}>
                <td><input aria-label="担当者名" value={r.name} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} /></td>
                <td style={{ width: 60 }}><button className="btn link small" onClick={() => setRows(rows.filter((_, j) => j !== i))}>削除</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn" onClick={() => setRows([...rows, { id: null, name: "" }])}>＋ 担当者を追加</button>
          <Marked><button className="btn primary" onClick={save}>保存</button></Marked>
        </div>
        <ErrorBox error={error} />
      </section>
      <section className="panel">
        <h2 style={{ marginBottom: 8 }}>この端末で操作する人</h2>
        <p className="small muted">修正履歴・ステータスの変更・帳票の発行に、この担当者の名前が残ります。</p>
        <Field label="操作する担当者">
          <select aria-label="操作する担当者" value={actor} onChange={(e) => { setActor(e.target.value); session.setActor(e.target.value); toast("操作する担当者を設定しました"); }}>
            <option value="">（記録しない）</option>
            {rows.filter((r) => r.id).map((r) => <option key={r.id}>{r.name}</option>)}
          </select>
        </Field>
        <Field label="接続用の合言葉（管理者から伝えられた場合だけ入力）">
          <input type="password" value={token} onChange={(e) => setToken(e.target.value)} onBlur={() => session.setToken(token.trim())} />
        </Field>
      </section>
    </div>
  );
}
