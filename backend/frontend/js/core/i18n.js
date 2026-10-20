// The page's language: English (the words written in index.html and in the
// scripts) or French. A French word is looked up by its English text in fr.js;
// anything missing stays in English. Words from the server (error messages,
// the training log, Garmin's data) stay in English.

import { FR } from "./fr.js";

const LANG_KEY = "at_lang";

function savedLang() {
  try {
    return localStorage.getItem(LANG_KEY);
  } catch (_) {
    return null;
  }
}

const lang = savedLang() === "fr" ? "fr" : "en";

export function getLang() {
  return lang;
}

// Saved in this browser, then the page reloads in the new language (the
// session is kept). Returns false when the browser won't save it.
export function setLang(next) {
  try {
    localStorage.setItem(LANG_KEY, next);
  } catch (_) {
    return false;
  }
  location.reload();
  return true;
}

// For toLocaleDateString(): in English, the browser's own formats as before.
export function locale() {
  return lang === "fr" ? "fr-CA" : undefined;
}

// t("Connected as {email}.", { email }) -> "Connecté en tant que a@b.c."
export function t(text, params = {}) {
  let out = (lang === "fr" && FR[text]) || text;
  for (const [key, value] of Object.entries(params)) out = out.replaceAll(`{${key}}`, value);
  return out;
}

// French counts 0 and 1 as singular ("0 séance"), English only 1.
export function isSingular(n) {
  return lang === "fr" ? Math.abs(n) < 2 : n === 1;
}

const ATTRIBUTES = ["placeholder", "aria-label", "title", "alt"];
const normalize = (s) => s.replace(/\s+/g, " ").trim();

// Translates index.html once, before anything is shown. Plain text and the
// attributes above are matched by their English words; an element with inline
// markup (<strong>, <code>) carries data-i18n="<key>" and is replaced whole.
export function translatePage() {
  document.documentElement.lang = lang;
  if (lang === "en") return;
  for (const el of document.querySelectorAll("[data-i18n]")) {
    if (FR[el.dataset.i18n]) el.innerHTML = FR[el.dataset.i18n];
  }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) =>
      node.parentElement.closest("[data-i18n], script, pre, textarea")
        ? NodeFilter.FILTER_REJECT
        : NodeFilter.FILTER_ACCEPT,
  });
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const key = normalize(node.nodeValue);
    if (FR[key]) node.nodeValue = node.nodeValue.replace(node.nodeValue.trim(), FR[key]);
  }
  for (const attr of ATTRIBUTES) {
    for (const el of document.querySelectorAll(`[${attr}]`)) {
      const french = FR[normalize(el.getAttribute(attr))];
      if (french) el.setAttribute(attr, french);
    }
  }
}
