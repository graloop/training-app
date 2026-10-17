// Every call to the server goes through api(): it adds the session token and
// turns an error answer into a thrown Error with a readable message and the
// HTTP status (err.status).

import { clearSession, getToken } from "./session.js";
import { showStatus, showView } from "./ui.js";

export async function api(path, options = {}) {
  const headers = options.headers || {};
  headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(path, { ...options, headers });
  // A 401 without a token is a failed login, not an expired session.
  if (res.status === 401 && token) {
    const message = "Your session expired, please log in again.";
    clearSession();
    showView("auth-view");
    showStatus(document.getElementById("auth-error"), message, true);
    throw new Error(message);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_) {
      // Not the app's answer: Cloudflare's own page when the server is off or took too long (502, 504, 524...).
      if (res.status >= 500) detail = `The server didn't answer (error ${res.status}). Try again in a minute.`;
    }
    if (!detail) detail = `Error ${res.status}`;
    if (typeof detail !== "string") detail = detail.map((d) => d.msg).join("; "); // validation errors
    const error = new Error(detail);
    error.status = res.status;
    throw error;
  }
  if (res.status === 204) return null;
  return res.json();
}
