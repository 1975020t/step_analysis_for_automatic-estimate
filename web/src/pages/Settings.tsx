import { Link } from "react-router-dom";
import { useMeta } from "../ui";

/** 設定: every settings screen on one page, grouped, each with what it affects. */
export default function SettingsPage() {
  const { meta } = useMeta();
  const pct = (k: string) => `${Math.round((meta?.policy[k] || 0) * 100)}%`;
  const groups: { title: string; note?: string; items: [string, string, string][] }[] = [
    {
      title: "金額関連",
      note: "変えた単価は、これから計算する見積に使われます。発行済みの帳票の金額への影響なし",
      items: [
        ["/settings/masters?tab=materials", "材料マスタ", "材質別の kg 単価・密度（材料費に反映）"],
        ["/settings/masters?tab=processes", "工程マスタ", "切断・穴・曲げ・溶接・タップ等の単価（加工費に反映）"],
        ["/settings/masters?tab=surface_treatments", "表面処理マスタ", "塗装・めっき等の1個あたり単価"],
        ["/settings/masters?tab=pricing_policy", "価格方針", meta ? `現在：粗利 ${pct("margin_rate")}・特急割増 ${pct("rush_surcharge_rate")}（全見積に適用）` : "粗利率・特急割増・消費税率"],
        ["/settings/logic", "見積ロジック", "費目別の計算式（参照のみ）"],
      ],
    },
    {
      title: "帳票関連",
      items: [
        ["/settings/masters?tab=company", "自社情報", "会社名・住所・電話・登録番号（帳票に印字）"],
        ["/settings/templates", "見積書テンプレート", "記載項目、顧客別の適用"],
        ["/settings/partners", "取引先管理", "顧客・協力会社の台帳（記録と表示のみ。金額への影響なし）"],
      ],
    },
    {
      title: "運用",
      items: [
        ["/settings/staff", "担当者", "担当者一覧、この端末の操作担当者"],
        ["/settings/statuses", "案件ステータス", "ステータスの名称・順序・色・フェーズ（進捗とTo Doリストに反映）"],
        ["/settings/categories", "分類・属性項目", "顧客別・製品別等の分類、図面の検索項目"],
      ],
    },
    {
      title: "データ",
      items: [
        ["/settings/import", "過去見積の取り込み", "CSVから取り込み（類似実績・実績分析に反映）"],
        ["/search?add=1", "書類登録", "ミルシート・社内規格・仕様書等（上部の検索欄から検索可能）"],
      ],
    },
  ];
  return (
    <div className="settings-groups" style={{ maxWidth: 1180 }}>
      {groups.map((g) => (
        <section key={g.title}>
          <h2>{g.title}</h2>
          {g.items.map(([to, label, note]) => (
            <Link key={label} className="tile" to={to}>
              <b className="link">{label}</b>
              <div className="note">{note}</div>
            </Link>
          ))}
          {g.note && <p className="small muted" style={{ margin: "4px 0 0" }}>{g.note}</p>}
        </section>
      ))}
    </div>
  );
}
