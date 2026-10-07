import { useNavigate } from "react-router-dom";
import { day, yen } from "../api";
import { ErrorBox, Loading, Marked, StatusDot, Thumb, useLoad } from "../ui";

export default function EstimatesPage() {
  const navigate = useNavigate();
  const { data, error } = useLoad<any[]>("/api/estimates");
  return (
    <div>
      <div className="row between" style={{ marginBottom: 14 }}>
        <span />
        <div className="row">
          <button className="btn" onClick={() => navigate("/review")}>振り返り分析</button>
          <Marked><button className="btn primary" onClick={() => navigate("/estimates/new")}>＋ 新規見積作成</button></Marked>
        </div>
      </div>
      <ErrorBox error={error} />
      {!data ? <Loading /> : data.length === 0 ? <div className="empty">見積はまだありません。</div> : (
        <table className="rule" data-testid="estimates">
          <thead><tr><th>図面</th><th>見積番号</th><th>品名・図番</th><th>顧客</th><th>状態</th><th className="right">数量／金額</th><th>納期</th><th>担当</th><th>注意</th></tr></thead>
          <tbody>
            {data.map((q) => (
              <tr key={q.id} className="click" onClick={() => navigate(`/estimates/${q.id}`)}>
                <td><Thumb revisionId={q.revision_id} hasPdf={q.has_pdf} /></td>
                <td className="num" style={{ color: "var(--accent-dark)" }}>{q.number}</td>
                <td>{q.name || q.title}<span className="sub">{q.drawing_no} {q.revision && `Rev.${q.revision}`}</span></td>
                <td>{q.customer}</td>
                <td><StatusDot name={q.status.name} color={q.status.color} /></td>
                <td className="right nowrap">{q.quantity ?? "—"}個 ／ {q.subtotal ? yen(q.subtotal) : "未作成"}</td>
                <td>{day(q.due_date)}</td>
                <td>{q.staff || "—"}</td>
                <td className="row" style={{ gap: 4 }}>
                  {q.missing > 0 && <span className="tag solid">未入力 {q.missing}</span>}
                  {q.warnings.map((w: any) => <span key={w.kind} className={w.kind === "stale" ? "tag plain" : "tag"}>{w.label}</span>)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
