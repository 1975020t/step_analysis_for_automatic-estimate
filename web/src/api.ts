// The only way the screens talk to the server. No amount or "can it be issued" is computed here.

import { authMode, idToken, startLogin } from "./auth";

export class ApiError extends Error {
  status: number;
  code: string;
  details: any;
  constructor(status: number, code: string, message: string, details?: any) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

const store = {
  get(key: string): string {
    try {
      return localStorage.getItem(key) || "";
    } catch {
      return "";
    }
  },
  set(key: string, value: string) {
    try {
      localStorage.setItem(key, value);
    } catch {
      /* private mode: kept for this page only */
    }
  },
};

export const session = {
  actor: () => store.get("actor"),
  setActor: (name: string) => store.set("actor", name),
  token: () => store.get("apiToken"),
  setToken: (t: string) => store.set("apiToken", t),
};

async function headers(extra: Record<string, string> = {}): Promise<Record<string, string>> {
  const h: Record<string, string> = { ...extra };
  if (authMode() === "login") {
    // signed in: the ID token says who acts and for which company (no X-Actor)
    const token = await idToken();
    if (token) h["Authorization"] = `Bearer ${token}`;
    return h;
  }
  const token = session.token();
  if (token) h["Authorization"] = `Bearer ${token}`;
  const actor = session.actor();
  if (actor) h["X-Actor"] = encodeURIComponent(actor);
  return h;
}

async function handle(res: Response) {
  if (res.ok) {
    const type = res.headers.get("content-type") || "";
    return type.includes("application/json") ? res.json() : res;
  }
  let body: any = null;
  try {
    body = await res.json();
  } catch {
    /* not JSON */
  }
  const e = body?.error || {};
  if (res.status === 401 && authMode() === "login") void startLogin(); // the sign-in ran out: sign in again
  throw new ApiError(res.status, e.code || "ERROR", e.message || `エラー（${res.status}）`, e.details);
}

export async function get<T = any>(path: string): Promise<T> {
  return handle(await fetch(path, { headers: await headers() }));
}

export async function send<T = any>(method: string, path: string, body?: any): Promise<T> {
  return handle(
    await fetch(path, {
      method,
      headers: await headers({ "Content-Type": "application/json" }),
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
}

export const post = <T = any>(path: string, body?: any) => send<T>("POST", path, body);
export const put = <T = any>(path: string, body?: any) => send<T>("PUT", path, body);
export const del = <T = any>(path: string) => send<T>("DELETE", path);

export async function upload<T = any>(path: string, file: File, fields: Record<string, string> = {}): Promise<T> {
  const form = new FormData();
  form.append("file", file, file.name);
  Object.entries(fields).forEach(([k, v]) => form.append(k, v));
  return handle(await fetch(path, { method: "POST", headers: await headers(), body: form }));
}

/** A drawing PDF, STEP or DXF: straight to the file store (S3) when the API offers it (on AWS a request through the
 * API holds about 6MB), else through the API. Either way the answer is the file's id, name, kind and size. */
export async function uploadFile<T = any>(file: File): Promise<T> {
  const form = await post("/api/uploads", { filename: file.name, size: file.size });
  if (!form.direct) return upload<T>("/api/files", file);
  const body = new FormData();
  Object.entries(form.fields as Record<string, string>).forEach(([k, v]) => body.append(k, v));
  body.append("file", file, file.name);
  const sent = await fetch(form.url, { method: "POST", body });
  if (!sent.ok) throw new ApiError(sent.status, "UPLOAD_FAILED", `ファイルを送れませんでした（${sent.status}）。もう一度お試しください。`);
  return post<T>(`/api/uploads/${form.file_id}/complete`, { filename: file.name });
}

/** An image or PDF behind the API (with the token header) as an object URL. */
export async function blobUrl(path: string): Promise<string> {
  const res = await fetch(path, { headers: await headers() });
  if (!res.ok) await handle(res);
  return URL.createObjectURL(await res.blob());
}

export async function waitJob(jobId: string, onProgress?: (job: any) => void, intervalMs = 400): Promise<any> {
  for (;;) {
    const job = await get(`/api/jobs/${jobId}`);
    onProgress?.(job);
    if (job.status === "done" || job.status === "failed") return job;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

export const yen = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `¥${Math.round(v).toLocaleString("ja-JP")}`);
export const num = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined ? "—" : v.toLocaleString("ja-JP", { maximumFractionDigits: digits });
export const day = (iso?: string | null) => (iso ? iso.slice(0, 10).replace(/-/g, "/") : "—");
export const stamp = (iso?: string | null) => (iso ? `${iso.slice(5, 10).replace("-", "/")} ${iso.slice(11, 16)}` : "—");
