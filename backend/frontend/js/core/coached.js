// A coach's athletes, and the one they are looking at: shared by the
// Calendar and Plan tabs (views/calendar.js, views/plan.js) and the Athletes
// tab (views/coaching.js), so picking an athlete in one shows them in the others.
// API: GET /api/coach/athletes.

import { api } from "./api.js";
import { isoDate } from "./format.js";

let athletes = []; // [{ id, username, ... }] as /api/coach/athletes lists them
let current = null; // the athlete being looked at, or null when there is none

export async function loadCoachedAthletes() {
  athletes = (await api(`/api/coach/athletes?today=${isoDate(new Date())}`)).athletes;
  current = athletes.find((a) => current && a.id === current.id) || athletes[0] || null;
  return athletes;
}

export function coachedAthletes() {
  return athletes;
}

export function coachedAthlete() {
  return current;
}

export function setCoachedAthlete(id) {
  current = athletes.find((a) => a.id === id) || current;
}

// Fills an athlete <select> (Calendar, Plan) and keeps it on the current athlete.
export function fillAthleteSelect(select) {
  select.innerHTML = "";
  for (const a of athletes) select.appendChild(new Option(a.username, String(a.id)));
  select.value = current ? String(current.id) : "";
  select.disabled = athletes.length === 0;
}
