// Dates, durations and words, formatted the same way on every tab.
// Dates from the server are "YYYY-MM-DD" strings (local days, no time).

export function isoDate(d) {
  return [d.getFullYear(), d.getMonth() + 1, d.getDate()].map((n) => String(n).padStart(2, "0")).join("-");
}

export function addDays(d, n) {
  const copy = new Date(d);
  copy.setDate(copy.getDate() + n);
  return copy;
}

export function thisMonday() {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return addDays(today, -((today.getDay() + 6) % 7));
}

// "Tue, Oct 13"
export function fmtDate(iso) {
  return new Date(iso + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

// "Tue, Oct 13, 2026"
export function fmtLongDate(iso) {
  return new Date(iso + "T00:00:00").toLocaleDateString(undefined, {
    weekday: "short",
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

// "Mon, Oct 12 to Sun, Nov 8, 2026"
export function fmtSpan(startIso, endIso) {
  const start = new Date(startIso + "T00:00:00");
  const end = new Date(endIso + "T00:00:00");
  const opts = { weekday: "short", month: "short", day: "numeric" };
  return `${start.toLocaleDateString(undefined, opts)} to ${end.toLocaleDateString(undefined, { ...opts, year: "numeric" })}`;
}

// "45 min", "1 h", "1 h 05"
export function fmtHours(minutes) {
  const total = Math.round(minutes || 0);
  if (total < 60) return `${total} min`;
  const h = Math.floor(total / 60);
  const m = total % 60;
  return m ? `${h} h ${String(m).padStart(2, "0")}` : `${h} h`;
}

// "1:05"
export function fmtHM(minutes) {
  const total = Math.round(minutes || 0);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

// "1 session", "3 sessions"
export function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}
