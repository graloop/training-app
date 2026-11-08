// Day or night colours, set before the page is drawn so night mode never
// flashes white. A plain script, not a module: modules only run after the
// first paint. The switch itself is in views/night-mode.js.
(() => {
  let saved = null;
  try {
    saved = localStorage.getItem("at_theme");
  } catch (_) {
    // private browsing: follow the device
  }
  const night = saved ? saved === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = night ? "dark" : "light";
})();
