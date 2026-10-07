import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { ApiError, blobUrl, get } from "./api";

// ---------------------------------------------------------------- data loading
export function useLoad<T>(path: string | null, deps: any[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string>("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) return;
    let live = true;
    get<T>(path)
      .then((d) => live && (setData(d), setError("")))
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, tick, ...deps]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, reload, setData };
}

/** Image behind the API as an object URL (so the token header goes with it). */
export function ApiImage({ src, alt, className, onError }: { src: string; alt: string; className?: string; onError?: () => void }) {
  const [url, setUrl] = useState("");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    let made = "";
    setFailed(false);
    blobUrl(src)
      .then((u) => {
        made = u;
        if (live) setUrl(u);
      })
      .catch(() => {
        if (live) {
          setFailed(true);
          onError?.();
        }
      });
    return () => {
      live = false;
      if (made) URL.revokeObjectURL(made);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src]);
  if (failed) return <div className={`hatch ${className || ""}`} />;
  if (!url) return <div className={`hatch ${className || ""}`} />;
  return <img src={url} alt={alt} className={className} />;
}

// ---------------------------------------------------------------- meta (choices shared by every screen)
export type Meta = {
  materials: { code: string; name: string }[];
  surface_treatments: { code: string; name: string }[];
  processes: { code: string; name: string; unit: string }[];
  staff: { id: number; name: string; active: boolean }[];
  statuses: { id: number; name: string; color: string; phase: string; visible: boolean; role: string; count: number }[];
  categories: { id: number; name: string; children: { id: number; name: string; count: number }[] }[];
  attributes: { id: number; key: string; label: string; input_type: string; options: any[]; unit: string; searchable: boolean; builtin: boolean }[];
  templates: { id: number; name: string; customers: string[]; options: Record<string, boolean>; is_default: boolean }[];
  drawing_reader: boolean;
  lost_reasons: string[];
  document_kinds: string[];
  phases: { key: string; number: number | null; label: string }[];
  colors: Record<string, string>;
  policy: Record<string, number>;
};

const MetaContext = createContext<{ meta: Meta | null; reload: () => void }>({ meta: null, reload: () => {} });
export const useMeta = () => useContext(MetaContext);

export function MetaProvider({ children }: { children: ReactNode }) {
  const { data, reload } = useLoad<Meta>("/api/meta");
  return <MetaContext.Provider value={{ meta: data, reload }}>{children}</MetaContext.Provider>;
}

// ---------------------------------------------------------------- toast
const ToastContext = createContext<(text: string) => void>(() => {});
export const useToast = () => useContext(ToastContext);
export function ToastProvider({ children }: { children: ReactNode }) {
  const [text, setText] = useState("");
  useEffect(() => {
    if (!text) return;
    const t = setTimeout(() => setText(""), 3200);
    return () => clearTimeout(t);
  }, [text]);
  return (
    <ToastContext.Provider value={setText}>
      {children}
      {text && <div className="toast" role="status">{text}</div>}
    </ToastContext.Provider>
  );
}

// ---------------------------------------------------------------- small parts
/** The steps of a flow with the current one (新規見積の4ステップ、見積結果の6ステップ). `now` is 1-based;
 * steps before it are done; `now` past the last step marks every step done. */
export function Flow({ steps, now }: { steps: { label: string; note?: string }[]; now: number }) {
  return (
    <ol className="flow" aria-label="手順">
      {steps.map((st, i) => {
        const n = i + 1;
        const state = n < now ? "done" : n === now ? "now" : "";
        return (
          <li key={st.label} className={state} aria-current={state === "now" ? "step" : undefined}>
            <small>{state === "done" ? "完了" : state === "now" ? "現在" : st.note || (n === now + 1 ? "次" : "")}</small>
            <b>{n} {st.label}</b>
          </li>
        );
      })}
    </ol>
  );
}

/** Where a value comes from (読取・要確認・図面情報・マスタ未登録・記載なし), from the API's `mark`. */
const MARK_LABEL: Record<string, string> = { read: "読取", review: "要確認", drawing: "図面情報", unregistered: "マスタ未登録", none: "記載なし" };
export function Mark({ mark }: { mark?: string }) {
  if (!mark || !MARK_LABEL[mark]) return null;
  return <span className={`mark ${mark}`}>{MARK_LABEL[mark]}</span>;
}

/** The screen that opens the next action the API chose for a case or an estimate. */
export function actionHref(a: { kind: string; quote_id?: number | null; drawing_id?: number | null; doc_kind?: string } | null | undefined): string {
  if (!a) return "/cases";
  switch (a.kind) {
    case "new_estimate":
      return a.drawing_id ? `/estimates/new?drawing=${a.drawing_id}` : "/estimates/new";
    case "progress":
      return `/estimates/${a.quote_id}/progress`;
    case "input":
      return `/estimates/${a.quote_id}?tab=conditions`;
    case "outcome":
      return `/estimates/${a.quote_id}#outcome`;
    case "documents":
      return `/documents?quote=${a.quote_id}&kind=${a.doc_kind || "delivery"}`;
    default:
      return `/estimates/${a.quote_id}`;
  }
}

export function Reasons({ items }: { items: { kind: string; label: string }[] }) {
  return (
    <span className="row" style={{ gap: 4 }}>
      {items.map((w) => (
        <span key={w.kind} className={`reason ${w.kind}`}>{w.label}</span>
      ))}
    </span>
  );
}

export function StatusTag({ name, color }: { name: string; color?: string }) {
  const cls = color === "grey" ? "tag grey" : color === "dark" ? "tag soft" : "tag";
  return <span className={cls}>{name}</span>;
}

export function StatusDot({ name, color }: { name: string; color: string }) {
  return (
    <span className="nowrap">
      <i className={`dot ${color}`} />
      {name}
    </span>
  );
}

export function ErrorBox({ error }: { error: any }) {
  if (!error) return null;
  const message = typeof error === "string" ? error : error.message;
  const details = error instanceof ApiError && Array.isArray(error.details) ? error.details : null;
  return (
    <div className="error" role="alert">
      {message}
      {details && details.length > 0 && (
        <ul>
          {details.map((d: any, i: number) => (
            <li key={i}>{d.message}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Loading({ text = "読み込み中…" }: { text?: string }) {
  return <div className="empty">{text}</div>;
}

export function Field({ label, children, hint }: { label: ReactNode; children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="field">
      <label>{label}</label>
      {children}
      {hint && <span className="small muted">{hint}</span>}
    </div>
  );
}

export function Tabs<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: ReactNode }[]; onChange: (v: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {options.map((o) => (
        <button key={o.value} className={o.value === value ? "on" : ""} role="tab" aria-selected={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function LineTabs<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: ReactNode }[]; onChange: (v: T) => void }) {
  return (
    <div className="linetabs" role="tablist">
      {options.map((o) => (
        <button key={o.value} className={o.value === value ? "on" : ""} role="tab" aria-selected={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  return (
    <>
      <div className="drawer-bg" onClick={onClose} />
      <div className="modal" role="dialog" aria-label={title}>
        <div className="row between" style={{ marginBottom: 12 }}>
          <h2>{title}</h2>
          <button className="btn link" onClick={onClose} aria-label="閉じる">
            ✕
          </button>
        </div>
        {children}
      </div>
    </>
  );
}

export const Icon = {
  home: () => (
    <svg className="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
      <path d="M2 7.5 8 2.5l6 5" />
      <path d="M3.5 6.5v7h9v-7M6.5 13.5v-4h3v4" />
    </svg>
  ),
  search: () => (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
      <circle cx="7" cy="7" r="5" />
      <path d="M11 11l3.5 3.5" />
    </svg>
  ),
  upload: () => (
    <svg className="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
      <path d="M3 1.5h7l3 3v10H3z" />
      <path d="M8 12V7M5.8 9 8 6.8 10.2 9" />
    </svg>
  ),
  layers: () => (
    <svg className="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
      <path d="M8 2 1.5 5.2 8 8.4l6.5-3.2z" />
      <path d="M1.5 8 8 11.2 14.5 8M1.5 10.8 8 14l6.5-3.2" />
    </svg>
  ),
  board: () => (
    <svg className="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
      <rect x="2" y="2.5" width="12" height="11" />
      <path d="M5.5 5v5.5M8 5v3M10.5 5v6" />
    </svg>
  ),
  calc: () => (
    <svg className="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
      <rect x="3" y="1.5" width="10" height="13" />
      <path d="M5.5 4.5h5M5.5 7.5h1M8 7.5h1M10.5 7.5h0M5.5 10h1M8 10h1M5.5 12.5h1M8 12.5h1" />
    </svg>
  ),
  tree: () => (
    <svg className="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
      <path d="M2.5 2v10h4M2.5 6h4" />
      <rect x="7.5" y="3.5" width="6" height="4" />
      <rect x="7.5" y="10" width="6" height="4" />
    </svg>
  ),
  doc: () => (
    <svg className="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
      <rect x="3" y="1.5" width="10" height="13" />
      <path d="M5.5 5h5M5.5 8h5M5.5 11h3" />
    </svg>
  ),
  files: () => (
    <svg className="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
      <path d="M4.5 1.5h6l3 3v8h-9z" />
      <path d="M2.5 4v10.5H11" />
      <circle cx="8.5" cy="8" r="2" />
    </svg>
  ),
  building: () => (
    <svg className="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
      <rect x="3" y="2" width="7" height="12" />
      <path d="M10 6h3v8h-3M5 5h1M7.5 5h1M5 8h1M7.5 8h1M5 11h1M7.5 11h1" />
    </svg>
  ),
  gear: () => (
    <svg className="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
      <circle cx="8" cy="8" r="2.2" />
      <path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4" />
    </svg>
  ),
  help: () => (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5">
      <circle cx="8" cy="8" r="6.5" />
      <path d="M6.2 6.2a1.8 1.8 0 1 1 2.6 1.6c-.5.3-.8.6-.8 1.2v.5M8 11.3v.3" />
    </svg>
  ),
};

export function Thumb({ revisionId, hasPdf, className = "thumb" }: { revisionId?: number | null; hasPdf?: boolean; className?: string }) {
  if (!revisionId || !hasPdf) return <div className={`hatch ${className}`} />;
  return <ApiImage src={`/api/revisions/${revisionId}/preview.png?width=400`} alt="図面" className={className} />;
}
