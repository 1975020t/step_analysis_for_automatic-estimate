import { Link } from "react-router-dom";
import { day } from "../api";
import { actionHref, ErrorBox, Loading, Reasons, useLoad } from "../ui";

/** Home: the To Do list, the phases and the shortcuts. Which cases are listed, in which order, and what to do
 * next with each are decided by the API (/api/home). */
export default function HomePage() {
  const { data, error } = useLoad<any>("/api/home");
  if (!data) return error ? <ErrorBox error={error} /> : <Loading />;
  const phases = data.phases.filter((p: any) => p.key !== "closed");
  return (
    <div style={{ maxWidth: 1180 }}>
      <section className="block">
        <div className="section-head">
          <h2>To Doリスト</h2>
          <span className="small muted">基準日 {day(data.reference_date)} ・ {data.todo.length}件</span>
        </div>
        {data.todo.length === 0 ? (
          <div className="list"><div className="item muted">対応が必要な案件はありません。</div></div>
        ) : (
          <div className="list" data-testid="todo">
            {data.todo.map((c: any) => (
              <div key={c.id} className="item">
                <Reasons items={c.todo} />
                <div className="what">
                  <b>{c.name || c.title}</b>　{c.drawing_no}{c.revision ? ` Rev.${c.revision}` : ""}　{c.customer}
                  <div className="small muted">{c.number} ・ {c.todo_note}</div>
                </div>
                <Link className="go" to={actionHref(c.next_action)}>{c.next_action.label} →</Link>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="block">
        <div className="section-head">
          <h2>進捗</h2>
          <Link to="/cases?phase=closed" className="small">完了分を表示</Link>
        </div>
        <div className="tiles" data-testid="phases">
          {phases.map((p: any) => (
            <Link key={p.key} className="tile" to={`/cases?phase=${p.key}`}>
              <div className="label">{p.number} {p.label}</div>
              <div className="big">{p.count}</div>
              <div className={`note ${p.attention.count ? "warn" : ""}`}>{p.attention.label} {p.attention.count}</div>
            </Link>
          ))}
        </div>
      </section>

      <section className="block">
        <div className="section-head"><h2>ショートカット</h2></div>
        <div className="tiles">
          <Link className="tile" to="/estimates/new">
            <b className="link">新規見積</b>
            <div className="note">図面PDF・STEP・DXFのアップロード、または登録済み図面から作成</div>
          </Link>
          <Link className="tile" to="/drawings/register">
            <b className="link">図面登録</b>
            <div className="note">図面の保管・検索用。見積は後から作成可能</div>
          </Link>
          <Link className="tile" to="/drawings?mode=similar">
            <b className="link">類似実績検索</b>
            <div className="note">基準図面と形状の近い順に、過去の単価・受注結果を表示</div>
          </Link>
        </div>
      </section>
    </div>
  );
}
