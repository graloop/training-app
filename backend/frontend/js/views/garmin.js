// Garmin: the "Connect Garmin" panel, the Reconnect/Disconnect buttons of the
// Settings menu, and the Sync button. The sync runs on the server; the page
// starts it and asks how it is going (see backend/app/autosync.py).

import { api } from "../core/api.js";
import { plural } from "../core/format.js";
import { getUser, setUser } from "../core/session.js";
import { getCurrentTab, loadTab, showTab } from "../core/tabs.js";
import { showStatus } from "../core/ui.js";
import { loadCalendar } from "./calendar.js";
import { setMenuOpen } from "./settings.js";

const garminPanel = document.getElementById("garmin-panel");
const garminForm = document.getElementById("garmin-form");
const garminError = document.getElementById("garmin-error");
const syncBtn = document.getElementById("sync-btn");
const logSyncBtn = document.getElementById("log-sync"); // on the History tab
const syncStatus = document.getElementById("sync-status");

function isConnected() {
  const me = getUser();
  return Boolean(me && me.garmin_connected);
}

// --- Connection ---

export function renderGarminState(reason = "") {
  const me = getUser();
  const connected = isConnected() && !reason;
  garminPanel.classList.toggle("hidden", connected);
  document.getElementById("garmin-reason").textContent =
    reason || "Connect Garmin so your activities and sleep show up on the calendar.";
  document.getElementById("garmin-state").textContent = isConnected()
    ? `Connected as ${me.garmin_email}.`
    : "Not connected.";
  document.getElementById("garmin-disconnect").classList.toggle("hidden", !isConnected());
  syncBtn.disabled = !isConnected();
  syncBtn.title = isConnected() ? "" : "Connect Garmin first";
  if (!connected && me && me.garmin_email) document.getElementById("garmin-email").value = me.garmin_email;
}

function showGarminConnect(reason) {
  renderGarminState(reason);
  garminPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  document.getElementById("garmin-password").focus({ preventScroll: true });
}

garminForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const submit = document.getElementById("garmin-submit");
  garminError.classList.add("hidden");
  submit.disabled = true;
  submit.textContent = "Logging in to Garmin…";
  try {
    setUser(
      await api("/api/garmin/connect", {
        method: "POST",
        body: JSON.stringify({
          garmin_email: document.getElementById("garmin-email").value.trim(),
          garmin_password: document.getElementById("garmin-password").value,
        }),
      }),
    );
    document.getElementById("garmin-password").value = "";
    renderGarminState();
    runSync();
  } catch (err) {
    showStatus(garminError, err.message, true);
  } finally {
    submit.disabled = false;
    submit.textContent = "Connect Garmin";
  }
});

document.getElementById("garmin-reconnect").addEventListener("click", () => {
  setMenuOpen(false);
  showGarminConnect("Log in to Garmin again to refresh the connection.");
});

document.getElementById("garmin-disconnect").addEventListener("click", async () => {
  if (!confirm("Disconnect Garmin? Data already synced stays on the calendar.")) return;
  try {
    setUser(await api("/api/garmin/connect", { method: "DELETE" }));
  } catch (err) {
    if (err.status !== 401) alert(`Couldn't disconnect Garmin: ${err.message}`);
    return;
  }
  setMenuOpen(false);
  renderGarminState();
});

// --- Sync ---

// The sync runs on the server (a first one has weeks of sleep, recovery data
// and session details to fetch, in batches): start it, then ask how it goes.
const SYNC_POLL_MS = 1500;
const SYNC_GIVE_UP_MS = 30 * 60 * 1000; // stop asking; the server keeps going
let syncing = false;

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function syncProgressText(job) {
  if (job.state === "waiting") return "Waiting for the background sync of your account to finish…";
  if (job.rounds === 0) return "Syncing with Garmin…";
  return `Fetching older days and session details (step ${job.rounds + 1})…`;
}

async function refreshAfterSync() {
  await loadCalendar();
  const tab = getCurrentTab();
  if (tab === "history" || tab === "athlete") await loadTab(tab);
}

// `statusEl` is where progress is shown: under the calendar, or on the History tab.
export async function runSync(statusEl = syncStatus) {
  if (syncing) return;
  syncing = true;
  for (const btn of [syncBtn, logSyncBtn]) btn.disabled = true;
  try {
    let job = await api("/api/garmin/sync/start", { method: "POST" });
    let roundsShown = job.rounds;
    const giveUpAt = Date.now() + SYNC_GIVE_UP_MS;
    while ((job.state === "waiting" || job.state === "running") && Date.now() < giveUpAt) {
      showStatus(statusEl, syncProgressText(job));
      await wait(SYNC_POLL_MS);
      job = await api("/api/garmin/sync/status");
      // The calendar fills in batch by batch; the History tab is rebuilt once, at the end.
      if (job.rounds > roundsShown && job.state === "running") {
        roundsShown = job.rounds;
        await loadCalendar();
      }
    }
    if (job.state === "failed") throw new Error(job.error || "The sync failed.");
    await refreshAfterSync();
    if (job.state !== "done") {
      showStatus(statusEl, "The sync is still running on the server. Refresh the page in a few minutes.");
      return;
    }
    showStatus(
      statusEl,
      `Synced ${job.activities_synced} ${job.activities_synced === 1 ? "activity" : "activities"}, ` +
        `${plural(job.days_synced, "day")} of sleep and recovery` +
        (job.details_synced ? `, effort, feel and laps for ${plural(job.details_synced, "session")}` : "") +
        (job.more_to_sync ? ". Older data is still waiting: sync again to continue." : "."),
    );
    if (!job.more_to_sync) statusEl.classList.add("ok");
  } catch (err) {
    if (err.status === 409) {
      statusEl.classList.add("hidden");
      showTab("calendar", { load: false });
      showGarminConnect(err.message);
    } else if (err.status !== 401) {
      showStatus(statusEl, err.message, true);
    }
  } finally {
    syncing = false;
    logSyncBtn.disabled = false;
    syncBtn.disabled = !isConnected();
  }
}

syncBtn.addEventListener("click", () => runSync(syncStatus));
