// The night mode switch in the top bar (one for the landing page, one once
// logged in). The choice is kept in this browser; until one is made, the
// device's own setting is followed. theme-early.js applies it on load.

const THEME_KEY = "at_theme";
const switches = document.querySelectorAll(".theme-switch");
const deviceDark = window.matchMedia("(prefers-color-scheme: dark)");

function savedTheme() {
  try {
    return localStorage.getItem(THEME_KEY);
  } catch (_) {
    return null;
  }
}

function apply(theme) {
  document.documentElement.dataset.theme = theme;
  for (const button of switches) button.setAttribute("aria-checked", String(theme === "dark"));
}

for (const button of switches) {
  button.addEventListener("click", () => {
    const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    apply(theme);
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch (_) {
      // not kept, but switched for this visit
    }
  });
}

deviceDark.addEventListener("change", (e) => {
  if (!savedTheme()) apply(e.matches ? "dark" : "light");
});

apply(document.documentElement.dataset.theme === "dark" ? "dark" : "light");
