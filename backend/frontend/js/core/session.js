// Who is logged in: the session token (kept in localStorage so a reload stays
// logged in) and the account it belongs to.

const TOKEN_KEY = "at_token";

let memoryToken = null; // fallback when localStorage is unavailable
let user = null; // { id, username, is_admin, garmin_connected, garmin_email }

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || memoryToken;
  } catch (_) {
    return memoryToken;
  }
}

export function setSession(token) {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch (_) {
    /* private mode: the session lasts until the tab closes */
  }
  memoryToken = token;
}

export function clearSession() {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch (_) {
    /* ignore */
  }
  memoryToken = null;
  user = null;
}

export function getUser() {
  return user;
}

export function setUser(account) {
  user = account;
}
