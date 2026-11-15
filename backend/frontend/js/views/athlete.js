// Athlete tab: the athlete's own numbers (weight, zones, FTP...), what Garmin
// knows about them, and the rule set every plan follows. The objectives on the
// same tab are in objectives.js.
// API: GET/PUT /api/athlete (backend/app/routers/athlete.py), GET/PUT
// /api/plan/rules. Markup: the #profile-form, #athlete-garmin and #rules-text
// of #tab-athlete. Styles: css/athlete.css.

import { api } from "../core/api.js";
import { isoDate } from "../core/format.js";
import { t } from "../core/i18n.js";
import { showStatus } from "../core/ui.js";

const profileForm = document.getElementById("profile-form");
const profileStatus = document.getElementById("profile-status");
const rulesText = document.getElementById("rules-text");
const rulesStatus = document.getElementById("rules-status");
// Each key has an input with id "pf-<key>" in index.html.
const PROFILE_NUMBERS = ["weight_kg", "height_cm", "max_hr", "threshold_hr", "resting_hr", "ftp_watts"];
const PROFILE_TEXTS = [
  "ftp_date",
  "bike_zones",
  "threshold_pace",
  "run_zones",
  "css_pace",
  "swim_zones",
  "equipment",
  "health",
  "other",
];

function fillProfile(data) {
  for (const key of [...PROFILE_NUMBERS, ...PROFILE_TEXTS]) {
    const value = data.profile[key];
    document.getElementById(`pf-${key}`).value = value == null ? "" : String(value);
  }
  document.getElementById("athlete-garmin").textContent = data.garmin.join("\n");
}

export async function loadAthletePage() {
  profileStatus.classList.add("hidden");
  rulesStatus.classList.add("hidden");
  try {
    const [athlete, rules] = await Promise.all([
      api(`/api/athlete?today=${isoDate(new Date())}`),
      api("/api/plan/rules"),
    ]);
    fillProfile(athlete);
    rulesText.value = rules.rules;
  } catch (err) {
    if (err.status !== 401) showStatus(profileStatus, err.message, true);
  }
}

// --- The rule set (saved on its own; Claude reads it through the connector) ---

async function saveRules() {
  try {
    rulesText.value = (
      await api("/api/plan/rules", { method: "PUT", body: JSON.stringify({ rules: rulesText.value }) })
    ).rules;
    showStatus(rulesStatus, t("Saved."));
    rulesStatus.classList.add("ok");
  } catch (err) {
    if (err.status !== 401) showStatus(rulesStatus, err.message, true);
  }
}
document.getElementById("rules-save").addEventListener("click", saveRules);
rulesText.addEventListener("change", saveRules);

profileForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {};
  for (const key of PROFILE_NUMBERS) {
    const value = document.getElementById(`pf-${key}`).value.trim();
    if (value) body[key] = Number(value);
  }
  for (const key of PROFILE_TEXTS) {
    const value = document.getElementById(`pf-${key}`).value.trim();
    if (value) body[key] = value;
  }
  for (const key of ["threshold_pace", "css_pace"]) {
    if (body[key] && !/^\d{1,2}:\d{2}$/.test(body[key])) {
      showStatus(
        profileStatus,
        key === "css_pace"
          ? t("Write the CSS as min:sec, like 1:45.")
          : t("Write the threshold pace as min:sec, like 4:30."),
        true,
      );
      return document.getElementById(`pf-${key}`).focus();
    }
  }
  try {
    fillProfile(await api(`/api/athlete?today=${isoDate(new Date())}`, { method: "PUT", body: JSON.stringify(body) }));
    showStatus(profileStatus, t("Saved."));
    profileStatus.classList.add("ok");
  } catch (err) {
    if (err.status !== 401)
      showStatus(
        profileStatus,
        err.status === 422 ? t("One of the numbers is out of range: check them and save again.") : err.message,
        true,
      );
  }
});
