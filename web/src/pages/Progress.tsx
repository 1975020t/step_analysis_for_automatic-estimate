import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { get, num, yen } from "../api";
import { ErrorBox, Flow } from "../ui";
import { WIZARD } from "./NewEstimate";

const STAGES = ["CADデータの解析", "図面の読み取り", "マスタ照合", "見積計算", "類似実績の検索"];

export default function ProgressPage() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [estimate, setEstimate] = useState<any>(null);
  const [job, setJob] = useState<any>(null);
  const [error, setError] = useState<any>(null);
  const [start] = useState(Date.now());
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    get(`/api/estimates/${id}`).then(setEstimate).catch(setError);
  }, [id]);
  const jobId = params.get("job") || estimate?.job?.job_id;
  useEffect(() => {
    if (!jobId) return;
    let live = true;
    const tick = async () => {
      try {
        const j = await get(`/api/jobs/${jobId}`);
        if (!live) return;
        setJob(j);
        setNow(Date.now());
        if (j.status === "done") setTimeout(() => live && navigate(`/estimates/${id}`), 1400);
        else if (j.status !== "failed") setTimeout(tick, 350);
      } catch (e) {
        setError(e);
      }
    };
    tick();
    return () => {
      live = false;
    };
  }, [jobId, id, navigate]);

  const p = job?.progress || {};
  const done = job?.status === "done";
  const step = done ? STAGES.length : Math.max(0, (p.step || 1) - 1);
  const pct = Math.round((step / STAGES.length) * 100);
  const v = p.values || {};
  const elapsed = Math.floor((now - start) / 1000);
  const shape = v.shape || {};
  return (
    <div>
      <Flow steps={WIZARD} now={done ? 4 : 3} />
      <p className="small muted">
        {estimate?.number} ・ {estimate?.customer} ・ 数量 {estimate?.inputs?.quantity ?? "—"}個 ・ 希望納期 {estimate?.due_date?.replace(/-/g, "/") || "—"}
      </p>
      <h1 style={{ fontSize: 30, marginBottom: 18 }}>
        {estimate?.drawing?.name} <span className="num" style={{ fontSize: 26, marginLeft: 12 }}>{estimate?.drawing?.drawing_no} {estimate?.drawing?.revision ? `Rev.${estimate.drawing.revision}` : ""}</span>
      </h1>
      <ErrorBox error={error || (job?.status === "failed" ? job.error : null)} />
      <div className="panel" style={{ padding: "22px 28px" }}>
        <div className="row" style={{ alignItems: "flex-end", gap: 34 }}>
          <div className="num" style={{ fontSize: 76, lineHeight: 0.95, color: "var(--accent-dark)" }} data-testid="progress-pct">{pct}%</div>
          <div className="spacer">
            <div className="small muted">ステップ {Math.min(step + (done ? 0 : 1), STAGES.length)} / {STAGES.length}</div>
            <h2 style={{ fontSize: 28 }}>{done ? "完了" : STAGES[step] || "受付中"}</h2>
            {done && <div className="muted">見積結果を開きます…</div>}
          </div>
          <span className="num">経過 {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}</span>
        </div>
        <div className="steps" style={{ gridTemplateColumns: `repeat(${STAGES.length}, 1fr)` }}>
          {STAGES.map((s, i) => <div key={s} className={i < step ? "done" : i === step && !done ? "now" : ""} />)}
        </div>
        <div className="stepnames" style={{ gridTemplateColumns: `repeat(${STAGES.length}, 1fr)` }}>
          {STAGES.map((s, i) => <span key={s} className={i <= step ? "" : "off"}>{String(i + 1).padStart(2, "0")} {s}</span>)}
        </div>
      </div>
      <div className="section-title"><h2>解析・計算の結果（途中経過）</h2></div>
      <div className="figures" style={{ gridTemplateColumns: "repeat(5, 1fr)" }}>
        <div>
          <span className="label">CADデータ</span>
          <div className="value">{shape.thickness_mm ? `t${shape.thickness_mm}` : "—"}</div>
          <span className="small muted">{shape.hole_count !== undefined && shape.hole_count !== null ? `穴 ${shape.hole_count} ・ 曲げ ${shape.bend_count} ・ 展開 ${num(shape.blank_area_mm2, 0)}mm²` : v.shape_status === "解析不可" ? "寸法を求められませんでした" : v.shape_status === "CADデータなし" ? "CADデータなし" : "待機中"}</span>
        </div>
        <div>
          <span className="label">図面の読み取り</span>
          <div className="value">{v.reading ? `${v.reading.items}項目` : step > 1 ? "なし" : "—"}</div>
          <span className="small muted">{v.reading ? `要確認 ${v.reading.review}` : step > 1 ? "図面の読み取りなし" : "待機中"}</span>
        </div>
        <div>
          <span className="label">マスタ照合</span>
          <div className="value">{v.matching ? v.matching.material || "未選択" : "—"}</div>
          <span className="small muted">{v.matching ? `追加加工 ${v.matching.processes}・未登録 ${v.matching.unregistered}` : "待機中"}</span>
        </div>
        <div>
          <span className="label">金額（税抜）</span>
          <div className="value">{v.price ? yen(v.price) : v.missing ? `未入力 ${v.missing}` : "—"}</div>
          <span className="small muted">{v.price ? "計算済み" : v.missing ? "見積結果で入力してください" : "待機中"}</span>
        </div>
        <div>
          <span className="label">類似実績</span>
          <div className="value">{v.similar ? `${v.similar.count}件` : "—"}</div>
          <span className="small muted">{v.similar?.top ? `${v.similar.top} ほか` : "待機中"}</span>
        </div>
      </div>
      {(done || job?.status === "failed") && (
        <p style={{ marginTop: 18 }}><button className="btn primary" onClick={() => navigate(`/estimates/${id}`)}>見積結果を開く</button></p>
      )}
    </div>
  );
}
