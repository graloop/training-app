// Every call to the server goes through api(): it adds the session token and
// turns an error answer into a thrown Error with a readable message and the
// HTTP status (err.status).
//
// The contract callers rely on:
// - resolves with the parsed JSON (null for 204 No Content);
// - a 401 with a token = the session is over: the token is dropped, the Log in
//   window opens, and the Error is still thrown. That is why callers write
//   `if (err.status !== 401) showStatus(...)`: the login window already said it;
// - any other error: err.message is the server's `detail` (FastAPI's
//   validation errors joined into one line), err.status the HTTP status.

import { t } from "./i18n.js";
import { clearSession, getToken } from "./session.js";
import { showLogin } from "./ui.js";

export async function api(path, options = {}) {
  const headers = options.headers || {};
  headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(path, { ...options, headers });
  // A 401 without a token is a failed login, not an expired session.
  if (res.status === 401 && token) {
    const message = t("Your session expired, please log in again.");
    clearSession();
    showLogin(message);
    throw new Error(message);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_) {
      // Not the app's answer: Cloudflare's own page when the server is off or took too long (502, 504, 524...).
      if (res.status >= 500)
        detail = t("The server didn't answer (error {status}). Try again in a minute.", { status: res.status });
    }
    if (!detail) detail = t("Error {status}", { status: res.status });
    if (typeof detail !== "string") detail = detail.map((d) => d.msg).join("; "); // validation errors
    const error = new Error(detail);
    error.status = res.status;
    throw error;
  }
  if (res.status === 204) return null;
  return res.json();
}
