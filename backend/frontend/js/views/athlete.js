// Athlete tab: the athlete's own numbers (weight, zones, FTP...) and what
// Garmin knows about them. The objectives on the same tab are in objectives.js.

import { api } from "../core/api.js";
import { isoDate } from "../core/format.js";
import { t } from "../core/i18n.js";
import { showStatus } from "../core/ui.js";

const profileForm = document.getElementById("profile-form");
const profileStatus = document.getElementById("profile-status");
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
  try {
    fillProfile(await api(`/api/athlete?today=${isoDate(new Date())}`));
  } catch (err) {
    if (err.status !== 401) showStatus(profileStatus, err.message, true);
  }
}

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
    showStatus(profileStatus, t("Saved. It is now part of the History tab."));
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
