import { useState } from "react";
import { Link } from "react-router-dom";
import { ErrorBox, Loading, useLoad } from "../ui";

export default function LogicPage() {
  const { data, error } = useLoad<any>("/api/logic");
  const [item, setItem] = useState(0);
  if (!data) return error ? <ErrorBox error={error} /> : <Loading />;
  const current = data.items[item];
  return (
    <div>
      <div className="panel" style={{ marginBottom: 18 }}>
        <span className="small muted">現在の計算式（表示のみ）</span>
        <div style={{ fontWeight: 700, fontSize: 15 }}>{data.summary}</div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "250px minmax(0, 1fr)", gap: 22, alignItems: "start" }}>
        <div style={{ border: "1px solid var(--line-strong)" }}>
          {data.items.map((it: any, i: number) => (
            <button key={it.name} onClick={() => setItem(i)} className="row between" style={{ width: "100%", padding: "10px 12px", border: 0, borderBottom: "1px solid var(--line)", background: i === item ? "var(--accent-soft)" : "var(--panel)", font: "inherit", cursor: "pointer" }}>
              {it.name}<span className="tag soft">有効</span>
            </button>
          ))}
        </div>
        <div>
          <h2 style={{ marginBottom: 10 }}>{current.name}</h2>
          <table className="rule">
            <thead><tr><th>積算項目</th><th>計算式</th><th>条件・備考</th></tr></thead>
            <tbody>{current.rows.map((r: any) => <tr key={r.item}><td><b>{r.item}</b></td><td>{r.formula}</td><td className="small">{r.note}</td></tr>)}</tbody>
          </table>
          <ul className="small muted">{data.notes.map((n: string) => <li key={n}>{n}</li>)}</ul>
          <Link to="/settings/masters" className="small"><b>単価・率はマスタで変更する →</b></Link>
        </div>
      </div>
    </div>
  );
}
