import { ReactNode, useEffect, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { get } from "./api";
import { Icon, MetaProvider, ToastProvider } from "./ui";
import RegisterPage from "./pages/Register";
import NewEstimatePage from "./pages/NewEstimate";
import ProgressPage from "./pages/Progress";
import EstimatePage from "./pages/Estimate";
import CasesPage from "./pages/Cases";
import DrawingsPage from "./pages/Drawings";
import DrawingPage from "./pages/Drawing";
import EstimatesPage from "./pages/Estimates";
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

type Group = { label: string; icon: () => JSX.Element; items: { to: string; label: string }[] };

const GROUPS: Group[] = [
  { label: "図面", icon: Icon.tree, items: [{ to: "/drawings", label: "図面一覧" }, { to: "/drawings/register", label: "図面を登録" }, { to: "/settings/categories", label: "分類・属性項目" }] },
  { label: "見積作業", icon: Icon.doc, items: [{ to: "/estimates/new", label: "新規見積作成" }, { to: "/estimates", label: "見積" }, { to: "/review", label: "振り返り分析" }, { to: "/settings/import", label: "過去見積の取り込み" }] },
  { label: "書類", icon: Icon.files, items: [{ to: "/search", label: "書類・ナレッジ検索" }, { to: "/documents", label: "帳票発行" }] },
  { label: "取引先", icon: Icon.building, items: [{ to: "/partners", label: "取引先管理" }] },
  {
    label: "設定",
    icon: Icon.gear,
    items: [
      { to: "/settings/masters", label: "マスタ" },
      { to: "/settings/logic", label: "見積ロジック" },
      { to: "/settings/templates", label: "見積書テンプレート" },
      { to: "/settings/statuses", label: "案件ステータス" },
      { to: "/settings/categories", label: "分類・属性項目" },
      { to: "/settings/staff", label: "担当者" },
    ],
  },
];

const TITLES: [RegExp, string][] = [
  [/^\/drawings\/register/, "図面を登録"],
  [/^\/drawings\/\d+/, "図面詳細"],
  [/^\/drawings/, "図面一覧"],
  [/^\/estimates\/new/, "新規見積作成"],
  [/^\/estimates\/\d+\/progress/, "解析の進み具合"],
  [/^\/estimates\/\d+/, "見積結果"],
  [/^\/estimates/, "見積"],
  [/^\/cases/, "案件・進捗"],
  [/^\/search/, "書類・ナレッジ検索"],
  [/^\/review/, "振り返り分析"],
  [/^\/partners/, "取引先管理"],
  [/^\/documents/, "帳票発行"],
  [/^\/settings\/masters/, "マスタ"],
  [/^\/settings\/logic/, "見積ロジック"],
  [/^\/settings\/templates/, "見積書テンプレート"],
  [/^\/settings\/statuses/, "案件ステータス"],
  [/^\/settings\/categories/, "分類・属性項目"],
  [/^\/settings\/staff/, "担当者"],
  [/^\/settings\/import/, "過去見積の取り込み"],
];

function Sidebar() {
  const location = useLocation();
  const [open, setOpen] = useState<string | null>(() => GROUPS.find((g) => g.items.some((i) => location.pathname.startsWith(i.to)))?.label || null);
  const [recent, setRecent] = useState<any[]>([]);
  useEffect(() => {
    get("/api/recent").then(setRecent).catch(() => setRecent([]));
  }, [location.pathname]);
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
        <NavLink to="/drawings/register" className={({ isActive }) => `primary marked ${isActive ? "active" : ""}`}>
          <i className="cm tl" />
          <i className="cm tr" />
          <i className="cm bl" />
          <i className="cm br" />
          <Icon.upload />
          図面を登録
        </NavLink>
        <NavLink to="/drawings" end>
          <Icon.layers />
          図面一覧
        </NavLink>
        <NavLink to="/cases">
          <Icon.board />
          案件・進捗
        </NavLink>
        <NavLink to="/estimates" end>
          <Icon.calc />
          見積
        </NavLink>
        <hr />
        {GROUPS.map((g) => (
          <div key={g.label} className={open === g.label ? "open" : ""}>
            <button className="navbtn" onClick={() => setOpen(open === g.label ? null : g.label)} aria-expanded={open === g.label}>
              <g.icon />
              {g.label}
              <span className="chev">›</span>
            </button>
            {open === g.label && (
              <div className="sub">
                {g.items.map((i) => (
                  <NavLink key={i.to + i.label} to={i.to} end>
                    {i.label}
                  </NavLink>
                ))}
              </div>
            )}
          </div>
        ))}
      </nav>
      <div className="recent">
        <h4>最近の見積</h4>
        {recent.map((r) => (
          <Link key={r.case_id} to={r.quote_id ? `/estimates/${r.quote_id}` : "/cases"}>
            {r.drawing_no} {r.name}
            <small>
              見積 {r.status}
            </small>
          </Link>
        ))}
        {recent.length === 0 && <span className="small muted" style={{ padding: "0 6px" }}>まだありません</span>}
      </div>
    </aside>
  );
}

const GLOSSARY: [string, string][] = [
  ["展開面積", "板を平らに伸ばしたときの面積。材料費（重量）の元になります。"],
  ["切断長", "レーザーで切る線の長さ（外周と穴の周りの合計）。"],
  ["穴数・曲げ数", "形状解析で数えた穴（ピアス加工）と曲げの数。類似の判定にも使います。"],
  ["Kファクター", "曲げたときの中立面の位置（0〜1）。指定がないと既定値0.33で、曲げ部品は概算になります。"],
  ["確定・概算", "形状解析の確からしさ。概算でも、金額に必要な項目が入力されていれば見積書は発行できます。"],
  ["読み取り済み・要確認・未登録・記載なし", "図面PDFから読み取った項目の状態。要確認はヒントで、発行は止めません。未登録はマスターにない項目で、単価の入力が必要です。"],
  ["未入力の項目", "金額に必要なのに入っていない項目。1つでもあると見積書は発行できません（APIが拒否します）。"],
  ["希望納期", "記録・見積書の表示・進捗の警告に使います。金額は変わりません（特急は人が指定）。"],
  ["類似実績・似た図面", "材料が同じで、曲げ数の差が1以内、穴数の差が2以内のもの。差が小さい順に並びます。"],
  ["顧客提示モード", "見積結果の原価の内訳（材料費・加工費・粗利）を隠して、単価と合計だけを表示します。"],
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
        <p className="muted">金額はマスターとルールだけで決まります（AIは金額を計算しません）。</p>
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
            <input type="search" placeholder="図番・品名・書類（Enterで検索）" value={q} onChange={(e) => setQ(e.target.value)} aria-label="検索" />
          </form>
          <button className="btn" onClick={() => setHelp(true)}>
            <Icon.help />
            用語・計算式
          </button>
        </header>
        <main className="content">{children}</main>
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
            <Route path="/" element={<Navigate to="/drawings" replace />} />
            <Route path="/drawings/register" element={<RegisterPage />} />
            <Route path="/drawings/:id" element={<DrawingPage />} />
            <Route path="/drawings" element={<DrawingsPage />} />
            <Route path="/estimates/new" element={<NewEstimatePage />} />
            <Route path="/estimates/:id/progress" element={<ProgressPage />} />
            <Route path="/estimates/:id" element={<EstimatePage />} />
            <Route path="/estimates" element={<EstimatesPage />} />
            <Route path="/cases" element={<CasesPage />} />
            <Route path="/search" element={<SearchPage />} />
            <Route path="/review" element={<ReviewPage />} />
            <Route path="/partners" element={<PartnersPage />} />
            <Route path="/documents" element={<DocumentsPage />} />
            <Route path="/settings/masters" element={<MastersPage />} />
            <Route path="/settings/logic" element={<LogicPage />} />
            <Route path="/settings/templates" element={<TemplatesPage />} />
            <Route path="/settings/statuses" element={<StatusesPage />} />
            <Route path="/settings/categories" element={<CategoriesPage />} />
            <Route path="/settings/staff" element={<StaffPage />} />
            <Route path="/settings/import" element={<ImportPage />} />
            <Route path="*" element={<div className="empty">ページが見つかりません。</div>} />
          </Routes>
        </Layout>
      </MetaProvider>
    </ToastProvider>
  );
}
