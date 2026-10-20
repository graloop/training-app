// The ⚙️ Settings menu in the top bar: Garmin connection buttons (wired in
// garmin.js) and the language. The account menu is in account.js.

import { getLang, setLang, t } from "../core/i18n.js";
import { registerMenu } from "../core/menus.js";
import { showStatus } from "../core/ui.js";

registerMenu("settings-btn", "settings-menu");

// --- Language (kept in this browser) ---

const langSelect = document.getElementById("lang-select");
langSelect.value = getLang();
langSelect.addEventListener("change", () => {
  if (setLang(langSelect.value)) return; // the page reloads
  langSelect.value = getLang();
  showStatus(document.getElementById("lang-status"), t("This browser won't save the language."), true);
});
