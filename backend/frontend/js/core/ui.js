// Small page helpers every view uses: which screen is shown, status lines,
// selecting text for a manual copy.

import { closeMenus, openMenu } from "./menus.js";

const VIEWS = ["auth-view", "app-view", "admin-view"];

export function showView(id) {
  closeMenus();
  for (const view of VIEWS) document.getElementById(view).classList.toggle("hidden", view !== id);
  // The top bar: the tabs for a member, the account and settings menus once
  // logged in, Log in on the landing page.
  document.getElementById("tabs").classList.toggle("hidden", id !== "app-view");
  document.getElementById("user-actions").classList.toggle("hidden", id === "auth-view");
  document.getElementById("guest-actions").classList.toggle("hidden", id !== "auth-view");
  document.body.dataset.view = id;
}

// The landing page with the Log in window open, and why (session expired...).
export function showLogin(message = "") {
  showView("auth-view");
  openMenu("login-menu");
  if (message) showStatus(document.getElementById("auth-error"), message, true);
}

export function showStatus(el, text, isError = false) {
  el.classList.remove("ok");
  el.textContent = text;
  el.classList.remove("hidden");
  el.classList.toggle("error", isError);
  el.classList.toggle("status", !isError);
}

// When the clipboard is not allowed: select the text so it can be copied by hand.
export function selectContents(el) {
  const range = document.createRange();
  range.selectNodeContents(el);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}
