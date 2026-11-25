// A coach's Athletes tab: the list (photo, next objective, this week's hours,
// last Garmin sync with a warning when stale or not connected, whether they
// share recovery, and where the coach's draft for them stands, which opens the
// Plan tab on them), adding one by username, and one athlete's profile as a
// coach sees it (objectives, zones, rule set; health notes only if shared),
// with Calendar and Plan, which open those tabs on that athlete (core/coached.js).
// Only shown to coaches. An athlete's own coaches are in Settings (views/settings.js).
// API: GET/POST /api/coach/athletes, GET/DELETE /api/coach/athletes/{id}.
// Markup: #athletes-panel. Styles: css/athlete.css.

import { api } from "../core/api.js";
import { loadCoachedAthletes, setCoachedAthlete } from "../core/coached.js";
import { showAvatar } from "../core/avatars.js";
import { fmtHours, fmtMoment, isoDate, plural } from "../core/format.js";
import { locale, t } from "../core/i18n.js";
import { showTab } from "../core/tabs.js";
import { showStatus } from "../core/ui.js";
import { planFor } from "./plan.js";

const list = document.getElementById("athletes-list");
const status = document.getElementById("athletes-status");
const view = document.getElementById("athlete-view");
const removeBtn = document.getElementById("athlete-view-remove");
let shown = null; // the athlete whose profile is open

function syncedText(when) {
  if (!when) return t("never synced with Garmin");
  const date = new Date(when).toLocaleDateString(locale(), { month: "short", day: "numeric" });
  return t("synced {date}", { date });
}

// The Garmin part of an athlete's line, with a warning when a plan would be built on old data.
function garminNote(a) {
  const note = document.createElement("span");
  if (a.garmin_state === "not connected") {
    note.className = "warn";
    note.textContent = t("Garmin not connected");
  } else if (a.garmin_state === "stale") {
    note.className = "warn";
    note.textContent = t("{synced}: stale data", { synced: syncedText(a.garmin_synced_at) });
    note.title = t("No sync for 3 days: their Garmin may need reconnecting, in their Settings.");
  } else {
    note.textContent = syncedText(a.garmin_synced_at);
  }
  return note;
}

// Where the coach's draft for this athlete stands: who changed it last, and whether it was sent.
export function draftStateText(draft) {
  if (!draft) return t("No draft");
  const sessions = plural(draft.sessions, "session");
  if (draft.sent_at) return t("Sent {when} · {sessions}", { when: fmtMoment(draft.sent_at), sessions });
  if (draft.written_by === "claude")
    return t("Proposed by Claude · {when} · {sessions}", { when: fmtMoment(draft.updated_at), sessions });
  return t("Edited by you · {when} · {sessions}", { when: fmtMoment(draft.updated_at), sessions });
}

function renderList(athletes) {
  list.innerHTML = "";
  if (!athletes.length) {
    const empty = document.createElement("li");
    empty.className = "hint";
    empty.textContent = t("No athletes yet. Add one with their username.");
    list.appendChild(empty);
  }
  for (const a of athletes) {
    const item = document.createElement("li");
    const open = document.createElement("button");
    open.type = "button";
    open.className = "person";
    open.setAttribute("aria-label", t("Open {name}", { name: a.username }));
    const face = document.createElement("span");
    face.className = "avatar";
    showAvatar(face, a.avatar_url);
    const text = document.createElement("span");
    text.className = "person-text";
    const name = document.createElement("strong");
    name.textContent = a.username;
    const detail = document.createElement("span");
    detail.className = "subtle";
    detail.textContent = [
      a.main_objective || t("no objective"),
      t("this week {done} of {planned}", { done: fmtHours(a.done_minutes), planned: fmtHours(a.planned_minutes) }),
      "",
    ].join(" · ");
    detail.appendChild(garminNote(a));
    if (a.share_health) {
      const shared = document.createElement("span");
      shared.className = "shared-badge";
      shared.textContent = t("recovery shared");
      shared.title = t("{name} shares their sleep and recovery with you.", { name: a.username });
      detail.append(" · ", shared);
    }
    text.append(name, detail);
    open.append(face, text);
    open.addEventListener("click", () => openAthlete(a.id));
    const draft = document.createElement("button");
    draft.type = "button";
    draft.className = `btn-link draft-state${a.draft && a.draft.written_by === "claude" && !a.draft.sent_at ? " new" : ""}`;
    draft.textContent = draftStateText(a.draft);
    draft.setAttribute("aria-label", t("Open {name}'s draft on the Plan tab", { name: a.username }));
    draft.addEventListener("click", () => planFor(a.id));
    item.className = "athlete-row";
    item.append(open, draft);
    list.appendChild(item);
  }
}

function fill(listEl, lines, empty) {
  listEl.innerHTML = "";
  for (const line of lines.length ? lines : [empty]) {
    const item = document.createElement("li");
    item.textContent = line;
    listEl.appendChild(item);
  }
}

const PROFILE_LABELS = {
  weight_kg: "Weight (kg)",
  height_cm: "Height (cm)",
  max_hr: "Max HR (bpm)",
  threshold_hr: "Threshold HR (bpm)",
  resting_hr: "Resting HR (bpm)",
  ftp_watts: "FTP (W)",
  ftp_date: "FTP tested on",
  bike_zones: "Power zones",
  threshold_pace: "Threshold pace (min/km)",
  run_zones: "Run zones",
  css_pace: "CSS (min/100 m)",
  swim_zones: "Swim zones",
  equipment: "Equipment",
  other: "Other",
};

async function openAthlete(id) {
  try {
    const a = await api(`/api/coach/athletes/${id}`);
    shown = a;
    document.getElementById("athlete-view-name").textContent = a.username;
    fill(
      document.getElementById("athlete-view-objectives"),
      a.objectives.map((o) =>
        [o.title, o.event_date || t("no date"), o.target_time ? t("target {time}", { time: o.target_time }) : ""]
          .filter(Boolean)
          .join(" · "),
      ),
      t("No objectives."),
    );
    fill(
      document.getElementById("athlete-view-profile"),
      Object.entries(a.profile).map(([key, value]) => `${t(PROFILE_LABELS[key] || key)}: ${value}`),
      t("Nothing entered yet."),
    );
    document.getElementById("athlete-view-garmin").textContent = a.garmin.join("\n");
    document.getElementById("athlete-view-rules").textContent = a.rules || t("No rule set yet.");
    resetRemove();
    view.classList.remove("hidden");
    view.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    if (err.status !== 401) showStatus(status, err.message, true);
  }
}

export async function loadMyAthletes() {
  status.classList.add("hidden");
  try {
    renderList(await loadCoachedAthletes());
  } catch (err) {
    if (err.status !== 401) showStatus(status, err.message, true);
  }
}

document.getElementById("athlete-add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("athlete-add-name");
  const username = input.value.trim();
  if (!username) return;
  try {
    const added = await api(`/api/coach/athletes?today=${isoDate(new Date())}`, {
      method: "POST",
      body: JSON.stringify({ username }),
    });
    input.value = "";
    await loadMyAthletes();
    showStatus(status, t("{name} is now one of your athletes.", { name: added.username }));
    status.classList.add("ok");
  } catch (err) {
    if (err.status !== 401) showStatus(status, err.message, true);
  }
});

document.getElementById("athlete-view-close").addEventListener("click", () => view.classList.add("hidden"));
document.getElementById("athlete-view-plan").addEventListener("click", () => planFor(shown.id));
document.getElementById("athlete-view-calendar").addEventListener("click", () => {
  setCoachedAthlete(shown.id);
  showTab("calendar");
});

// Two clicks: removing an athlete also drops your draft for them.
function resetRemove() {
  removeBtn.classList.remove("confirming");
  removeBtn.textContent = t("Remove");
}
removeBtn.addEventListener("click", async () => {
  if (!removeBtn.classList.contains("confirming")) {
    removeBtn.classList.add("confirming");
    removeBtn.textContent = t("Click again to remove {name}", { name: shown.username });
    setTimeout(resetRemove, 4000);
    return;
  }
  try {
    await api(`/api/coach/athletes/${shown.id}`, { method: "DELETE" });
    view.classList.add("hidden");
    await loadMyAthletes();
    showStatus(status, t("{name} is no longer one of your athletes.", { name: shown.username }));
    status.classList.add("ok");
  } catch (err) {
    if (err.status !== 401) showStatus(status, err.message, true);
  }
});
