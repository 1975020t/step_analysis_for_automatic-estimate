// Signing in with Amazon Cognito: Cognito's own sign-in pages, authorization code with PKCE (no secret in the
// screens, passwords never reach our server). The server says whether sign-in is used (/api/auth/config); the ID
// token goes with every call (api.ts) and is renewed with the refresh token before it expires.

export type AuthConfig = {
  mode: "login" | "token" | "open";
  client_id?: string;
  scopes?: string;
  authorize_url?: string;
  token_url?: string;
  logout_url?: string;
};

let config: AuthConfig = { mode: "open" };

const keep = {
  get(store: Storage, key: string): string {
    try {
      return store.getItem(key) || "";
    } catch {
      return "";
    }
  },
  set(store: Storage, key: string, value: string) {
    try {
      if (value) store.setItem(key, value);
      else store.removeItem(key);
    } catch {
      /* private mode: kept for this page only */
    }
  },
};
const ID = "login.id", REFRESH = "login.refresh", EXPIRES = "login.expires";
const VERIFIER = "login.verifier", STATE = "login.state", BACK = "login.back";

export const authMode = () => config.mode;
const redirectUri = () => `${window.location.origin}/auth/callback`;

export async function loadAuthConfig(): Promise<AuthConfig> {
  try {
    const res = await fetch("/api/auth/config");
    if (res.ok) config = await res.json();
  } catch {
    /* the server answers the other calls with their own errors */
  }
  return config;
}

function base64url(bytes: Uint8Array): string {
  let s = "";
  bytes.forEach((b) => (s += String.fromCharCode(b)));
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function random(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return base64url(bytes);
}

/** Go to the sign-in page; after signing in the screens come back to `back`. */
export async function startLogin(back = window.location.pathname + window.location.search): Promise<void> {
  const verifier = random();
  const state = random();
  const challenge = base64url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
  keep.set(sessionStorage, VERIFIER, verifier);
  keep.set(sessionStorage, STATE, state);
  keep.set(sessionStorage, BACK, back.startsWith("/auth/") ? "/" : back);
  const q = new URLSearchParams({
    response_type: "code",
    client_id: config.client_id || "",
    redirect_uri: redirectUri(),
    scope: config.scopes || "openid email profile",
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
  });
  window.location.assign(`${config.authorize_url}?${q}`);
}

async function tokenCall(body: Record<string, string>): Promise<void> {
  const res = await fetch(config.token_url || "", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: config.client_id || "", ...body }),
  });
  if (!res.ok) throw new Error("ログインできませんでした。もう一度お試しください。");
  const t = await res.json();
  keep.set(localStorage, ID, t.id_token);
  if (t.refresh_token) keep.set(localStorage, REFRESH, t.refresh_token);
  keep.set(localStorage, EXPIRES, String(Date.now() + Number(t.expires_in || 3600) * 1000));
}

/** The return from the sign-in page (/auth/callback?code=...&state=...): the path to go back to. */
export async function finishLogin(search: string): Promise<string> {
  const q = new URLSearchParams(search);
  const state = keep.get(sessionStorage, STATE);
  const verifier = keep.get(sessionStorage, VERIFIER);
  keep.set(sessionStorage, STATE, "");
  keep.set(sessionStorage, VERIFIER, "");
  if (q.get("error")) throw new Error("ログインが取り消されました。");
  if (!q.get("code") || !state || q.get("state") !== state) throw new Error("ログインをやり直してください。");
  await tokenCall({ grant_type: "authorization_code", code: q.get("code")!, redirect_uri: redirectUri(), code_verifier: verifier });
  return keep.get(sessionStorage, BACK) || "/";
}

let renewing: Promise<void> | null = null;

/** The ID token for the next call ("" when not signed in), renewed a minute before it expires. */
export async function idToken(): Promise<string> {
  if (config.mode !== "login") return "";
  const fresh = () => keep.get(localStorage, ID) && Number(keep.get(localStorage, EXPIRES)) - Date.now() > 60_000;
  if (fresh()) return keep.get(localStorage, ID);
  const refresh = keep.get(localStorage, REFRESH);
  if (!refresh) return "";
  renewing ||= tokenCall({ grant_type: "refresh_token", refresh_token: refresh })
    .catch(() => forget())
    .finally(() => (renewing = null));
  await renewing;
  return fresh() ? keep.get(localStorage, ID) : "";
}

function forget() {
  [ID, REFRESH, EXPIRES].forEach((k) => keep.set(localStorage, k, ""));
}

export function logout() {
  forget();
  const q = new URLSearchParams({ client_id: config.client_id || "", logout_uri: `${window.location.origin}/` });
  window.location.assign(`${config.logout_url}?${q}`);
}
