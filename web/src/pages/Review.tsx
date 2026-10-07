import { useState } from "react";
import { yen } from "../api";
import { ErrorBox, Loading, Tabs, useLoad } from "../ui";

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);

export default function ReviewPage() {
  const [months, setMonths] = useState("6");
  const { data, error } = useLoad<any>(`/api/review?months=${months}`);
  const cur = data?.current;
  const prev = data?.previous;
  const diff = (a?: number | null, b?: number | null, f = (x: number) => `${x >= 0 ? "+" : ""}${Math.round(x * 100)}%`) =>
    a === null || a === undefined || b === null || b === undefined || b === 0 ? "—" : f(a / b - 1);
  return (
    <div>
      <Tabs value={months} onChange={setMonths} options={[{ value: "3", label: "直近3か月" }, { value: "6", label: "直近6か月" }, { value: "12", label: "直近12か月" }]} />
      <ErrorBox error={error} />
      {!data ? <Loading /> : (
        <>
          <div className="figures" style={{ gridTemplateColumns: "repeat(4, 1fr)", margin: "16px 0 22px" }} data-testid="review-figures">
            <div><span className="label">見積件数</span><div className="value big">{cur.count}件</div><span className="small muted">前期比 {diff(cur.count, prev.count)}</span></div>
            <div><span className="label">受注率</span><div className="value big">{pct(cur.win_rate)}</div>
              <span className="small muted">受注 {cur.won}件・失注 {cur.lost}件 ／ 前期 {pct(prev.win_rate)}</span></div>
            <div><span className="label">平均回答日数（依頼から見積書の発行まで）</span><div className="value big">{cur.answer_days === null ? "—" : `${cur.answer_days.toFixed(1)}日`}</div>
              <span className="small muted">前期 {prev.answer_days === null ? "—" : `${prev.answer_days.toFixed(1)}日`}（この画面から発行した見積）</span></div>
            <div><span className="label">見積総額</span><div className="value big">{yen(cur.total)}</div><span className="small muted">受注 {yen(cur.won_total)}</span></div>
          </div>
          <p className="small muted">期間 {data.from.replace(/-/g, "/")}〜{data.to.replace(/-/g, "/")}</p>
          <div className="grid3" style={{ gap: 26 }}>
            <section>
              <h2 style={{ borderBottom: "1px solid var(--line)", paddingBottom: 8 }}>顧客別の受注率</h2>
              {data.customers.map((c: any) => <Bar key={c.name} name={c.name} rate={c.win_rate} right={`${c.count}件`} />)}
            </section>
            <section>
              <h2 style={{ borderBottom: "1px solid var(--line)", paddingBottom: 8 }}>担当者別</h2>
              {data.staff.map((s: any) => <Bar key={s.name} name={s.name} rate={s.win_rate} right={`${s.count}件${s.answer_days !== null ? ` 回答${s.answer_days.toFixed(1)}日` : ""}`} />)}
            </section>
            <section>
              <h2 style={{ borderBottom: "1px solid var(--line)", paddingBottom: 8 }}>失注の理由</h2>
              {data.lost_reasons.map((r: any) => <Bar key={r.reason} name={r.reason} rate={r.share} grey right={`${r.count}件`} />)}
              {data.lost_reasons.length === 0 && <div className="small muted">理由を記録した失注はありません。</div>}
              {data.lost_unrecorded > 0 && <div className="small muted" style={{ marginTop: 6 }}>理由の記録がない失注 {data.lost_unrecorded}件（取り込んだ過去見積など）は割合に含めていません。</div>}
            </section>
          </div>
        </>
      )}
    </div>
  );
}

function Bar({ name, rate, right, grey }: { name: string; rate: number | null; right: string; grey?: boolean }) {
  return (
    <div className="row" style={{ borderBottom: "1px solid var(--line)", padding: "9px 0", flexWrap: "nowrap" }}>
      <span style={{ width: 150, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={name}>{name}</span>
      <div className={`bar ${grey ? "grey" : ""}`} style={{ flex: 1 }}><i style={{ width: `${Math.round((rate || 0) * 100)}%` }} /></div>
      <span className="small" style={{ width: 40, textAlign: "right" }}>{pct(rate)}</span>
      <span className="small muted nowrap" style={{ width: 110, textAlign: "right" }}>{right}</span>
    </div>
  );
}
