// The landing page, for visitors without an account: the Log in window and the
// language button in the top bar, the contact links, and the sections that
// slide in as they scroll into view. The mock-ups are drawn in landing-mocks.js.

import { getLang, setLang } from "../core/i18n.js";
import { registerMenu } from "../core/menus.js";
import { startMock } from "./landing-mocks.js";

registerMenu("login-btn", "login-menu", () => {
  document.getElementById("auth-error").classList.add("hidden");
  document.getElementById("auth-username").focus();
});

// --- Language: the button offers the other one ---

const langToggle = document.getElementById("lang-toggle");
const other = getLang() === "fr" ? { code: "en", name: "English" } : { code: "fr", name: "Français" };
langToggle.textContent = other.code.toUpperCase();
langToggle.lang = other.code;
langToggle.title = other.name;
langToggle.setAttribute("aria-label", other.name);
langToggle.addEventListener("click", () => setLang(other.code));

// --- Contact: the address is put together here, so it isn't in the page for spam bots ---

const CONTACT = ["julientremblay128", "gmail.com"].join("@");

for (const link of document.querySelectorAll("[data-contact]")) {
  const subject = link.dataset.subject ? `?subject=${encodeURIComponent(link.dataset.subject)}` : "";
  link.href = `mailto:${CONTACT}${subject}`;
  if (!link.textContent.trim()) link.textContent = CONTACT;
}

// --- Sections and mock-ups come in when they are scrolled to ---

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const revealed = new IntersectionObserver(
  (entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      entry.target.classList.add("is-visible");
      const mock = entry.target.querySelector("[data-mock]");
      if (mock) startMock(mock, { animate: !reduceMotion });
      revealed.unobserve(entry.target);
    }
  },
  { threshold: 0.2 },
);

// Hidden until scrolled to only when this script runs: without it, everything shows.
document.documentElement.classList.add("can-reveal");
for (const el of document.querySelectorAll("#auth-view .reveal")) revealed.observe(el);

// The top bar gets a shadow once the page is scrolled.
const topbar = document.querySelector(".topbar");
window.addEventListener("scroll", () => topbar.classList.toggle("scrolled", window.scrollY > 8), { passive: true });
