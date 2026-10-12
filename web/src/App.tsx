import { ReactNode, useEffect, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { get, session } from "./api";
import { Icon, MetaProvider, ToastProvider } from "./ui";
import HomePage from "./pages/Home";
import RegisterPage from "./pages/Register";
import NewEstimatePage from "./pages/NewEstimate";
import ProgressPage from "./pages/Progress";
import EstimatePage from "./pages/Estimate";
import CasesPage from "./pages/Cases";
import DrawingsPage from "./pages/Drawings";
import DrawingPage from "./pages/Drawing";
import CategoriesPage from "./pages/Categories";
import SearchPage from "./pages/Search";
import ReviewPage from "./pages/Review";
import PartnersPage from "./pages/Partners";
import DocumentsPage from "./pages/Documents";
import MastersPage from "./pages/Masters";
import LogicPage from "./pages/Logic";
import TemplatesPage from "./pages/Templates";
import StatusesPage from "./pages/Statuses";
import StaffPage from "./pages/Staff";
import ImportPage from "./pages/Import";
import SettingsPage from "./pages/Settings";

const TITLES: [RegExp, string][] = [
  [/^\/$/, "ホーム"],
  [/^\/drawings\/register/, "図面登録"],
  [/^\/drawings\/\d+/, "図面詳細"],
  [/^\/drawings/, "図面"],
  [/^\/estimates\/new/, "新規見積"],
  [/^\/estimates\/\d+\/progress/, "新規見積"],
  [/^\/estimates\/\d+/, "見積結果"],
  [/^\/cases/, "見積・案件"],
  [/^\/search/, "検索"],
  [/^\/review/, "実績分析"],
  [/^\/documents/, "帳票発行"],
  [/^\/settings\/masters/, "マスタ"],
  [/^\/settings\/logic/, "見積ロジック"],
  [/^\/settings\/templates/, "見積書テンプレート"],
  [/^\/settings\/statuses/, "案件ステータス"],
  [/^\/settings\/categories/, "分類・属性項目"],
  [/^\/settings\/staff/, "担当者"],
  [/^\/settings\/import/, "過去見積の取り込み"],
  [/^\/settings\/partners/, "取引先管理"],
  [/^\/settings/, "設定"],
];

/** The menu: each item once (ホーム／見積・案件／図面／帳票発行／実績分析, and 設定 at the bottom). */
function Sidebar() {
  const location = useLocation();
  const [counts, setCounts] = useState<{ active: number; drawings: number } | null>(null);
  useEffect(() => {
    get("/api/home").then((h) => setCounts(h.counts)).catch(() => setCounts(null));
  }, [location.pathname]);
  const inEstimate = /^\/estimates\/\d+/.test(location.pathname);
  return (
    <aside className="side">
      <div className="brand">
        <div className="brand-mark">
          <i />
        </div>
        <div>
          <b>板金見積</b>
          <span>図面管理プラットフォーム</span>
        </div>
      </div>
      <nav className="nav" aria-label="メニュー">
        <NavLink to="/estimates/new" className={({ isActive }) => `primary ${isActive ? "active" : ""}`}>
          ＋ 新規見積
        </NavLink>
        <NavLink to="/" end>
          <Icon.home />
          ホーム
        </NavLink>
        <NavLink to="/cases" className={({ isActive }) => (isActive || inEstimate ? "active" : "")}>
          <Icon.board />
          見積・案件
          {counts && <span className="count">{counts.active}</span>}
        </NavLink>
        <NavLink to="/drawings">
          <Icon.layers />
          図面
          {counts && <span className="count">{counts.drawings}</span>}
        </NavLink>
        <NavLink to="/documents">
          <Icon.doc />
          帳票発行
        </NavLink>
        <NavLink to="/review">
          <Icon.calc />
          実績分析
        </NavLink>
        <span className="grow" />
        <div className="settings">
          <NavLink to="/settings">
            <Icon.gear />
            設定
          </NavLink>
        </div>
      </nav>
      <div className="who">
        <Link to="/settings/staff">担当者：{session.actor() || "未設定"}</Link>
      </div>
    </aside>
  );
}

const GLOSSARY: [string, string][] = [
  ["CADデータ", "部品の3Dデータ（STEP）か展開図（DXF）。板厚・展開面積・切断長・穴数・曲げ数を求めます。DXFには板厚がないので入力します。"],
  ["展開面積", "板を平らに伸ばしたときの面積。材料費（重量）の元になります。"],
  ["切断長", "レーザーで切る線の長さ（外周と穴の周りの合計）。"],
  ["穴数・曲げ数", "CADデータから数えた穴（ピアス加工）と曲げの数。類似の判定にも使います。"],
  ["Kファクター", "曲げたときの伸びを決める値（0〜1）。指定がないと標準値0.33で計算し、曲げのある部品は概算になります。"],
  ["確定・概算", "CADデータから求めた寸法の確からしさ。概算でも、金額に必要な項目が入力されていれば見積書は発行できます。"],
  ["読み取り済み・要確認・未登録・記載なし", "図面から読み取った項目の状態。要確認は確認のヒントで、発行は止めません。未登録はマスタにない項目で、単価の入力が必要です。"],
  ["未入力の項目", "金額に必要なのに入っていない項目。1つでもあると見積書は発行できません。"],
  ["希望納期", "記録・見積書の表示・進捗の警告に使います。金額は変わりません（特急は担当者が指定します）。"],
  ["マスタにない材質", "入力したkg単価と密度で計算します。歩留まり係数1.15を掛けます。"],
  ["類似実績・類似形状", "材料が同じで、曲げ数の差が1以内、穴数の差が2以内のもの。差が小さい順に並びます。CADデータのない図面は対象外です。"],
  ["フェーズ", "案件のステータスを 1 見積作成中／2 見積確認中／3 回答待ち／4 製造・出荷／完了分 にまとめたもの。どのステータスがどのフェーズかは設定の「案件ステータス」で決めます。"],
  ["To Doリスト", "納期超過・未入力あり・納期まで3日以内・回答待ち3日以上の案件。急ぐ順に並びます。"],
  ["顧客提示モード", "見積結果の原価の内訳（材料費・加工費・粗利）を隠して、単価と合計だけを表示します。"],
  ["受注率", "受注 ÷（受注＋失注）。結果の出ていない見積は数えません。"],
  ["書類の検索", "文字の入ったPDF・Excelと、登録済みの図面・見積が対象です。スキャンした画像の文字は検索できません。"],
];

function Help({ onClose }: { onClose: () => void }) {
  return (
    <>
      <div className="drawer-bg" onClick={onClose} />
      <div className="drawer" role="dialog" aria-label="用語・計算式">
        <div className="row between">
          <h2>用語・計算式</h2>
          <button className="btn link" onClick={onClose}>
            ✕
          </button>
        </div>
        <table className="rule">
          <tbody>
            {GLOSSARY.map(([k, v]) => (
              <tr key={k}>
                <td style={{ width: 130, fontWeight: 700 }}>{k}</td>
                <td>{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p>
          <Link to="/settings/logic" onClick={onClose}>
            見積ロジック（費目ごとの計算式）を見る →
          </Link>
        </p>
      </div>
    </>
  );
}

function Layout({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [help, setHelp] = useState(false);
  const [q, setQ] = useState("");
  const title = TITLES.find(([re]) => re.test(location.pathname))?.[1] || "";
  useEffect(() => {
    document.title = `${title} | 板金見積`;
  }, [title]);
  return (
    <div className="app">
      <Sidebar />
      <div className="main">
        <header className="topbar">
          <h1>{title}</h1>
          <form
            className="search"
            role="search"
            onSubmit={(e) => {
              e.preventDefault();
              if (q.trim()) navigate(`/search?q=${encodeURIComponent(q.trim())}`);
            }}
          >
            <Icon.search />
            <input type="search" placeholder="図番・品名・顧客・書類（Enterで検索）" value={q} onChange={(e) => setQ(e.target.value)} aria-label="検索" />
          </form>
          <button className="btn" onClick={() => setHelp(true)}>
            <Icon.help />
            用語・計算式
          </button>
        </header>
        <main className="content">
          {/^\/settings\/./.test(location.pathname) && <Link to="/settings" className="back">← 設定</Link>}
          {children}
        </main>
      </div>
      {help && <Help onClose={() => setHelp(false)} />}
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <MetaProvider>
        <Layout>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/drawings/register" element={<RegisterPage />} />
            <Route path="/drawings/:id" element={<DrawingPage />} />
            <Route path="/drawings" element={<DrawingsPage />} />
            <Route path="/estimates/new" element={<NewEstimatePage />} />
            <Route path="/estimates/:id/progress" element={<ProgressPage />} />
            <Route path="/estimates/:id" element={<EstimatePage />} />
            <Route path="/estimates" element={<Navigate to="/cases" replace />} />
            <Route path="/cases" element={<CasesPage />} />
            <Route path="/search" element={<SearchPage />} />
            <Route path="/review" element={<ReviewPage />} />
            <Route path="/documents" element={<DocumentsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/settings/masters" element={<MastersPage />} />
            <Route path="/settings/logic" element={<LogicPage />} />
            <Route path="/settings/templates" element={<TemplatesPage />} />
            <Route path="/settings/statuses" element={<StatusesPage />} />
            <Route path="/settings/categories" element={<CategoriesPage />} />
            <Route path="/settings/staff" element={<StaffPage />} />
            <Route path="/settings/import" element={<ImportPage />} />
            <Route path="/settings/partners" element={<PartnersPage />} />
            <Route path="/partners" element={<Navigate to="/settings/partners" replace />} />
            <Route path="*" element={<div className="empty">ページが見つかりません。</div>} />
          </Routes>
        </Layout>
      </MetaProvider>
    </ToastProvider>
  );
}
