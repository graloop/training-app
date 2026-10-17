// Sports: the ones a session can be planned for, their names and icons.
// Garmin's activity types ("trail_running", "virtual_ride"...) are matched by
// the words they contain.

export const SPORTS = [
  { type: "swimming", label: "Swimming" },
  { type: "cycling", label: "Cycling" },
  { type: "running", label: "Running" },
  { type: "strength_training", label: "Strength" },
];

const ACTIVITY_ICONS = [
  ["swim", "🏊"],
  ["run", "🏃"],
  ["walk", "🚶"],
  ["hik", "🥾"],
  ["bik", "🚴"],
  ["cycl", "🚴"],
  ["ride", "🚴"], // Garmin's typeKey for Zwift is "virtual_ride"
  ["zwift", "🚴"],
  ["strength", "🏋️"],
  ["yoga", "🧘"],
  ["row", "🚣"],
  ["ski", "⛷️"],
  ["snowboard", "🏂"],
  ["padd", "🛶"],
  ["elliptical", "🌀"],
  ["rest", "😴"],
];

export function activityIcon(type) {
  const key = (type || "").toLowerCase();
  for (const [needle, icon] of ACTIVITY_ICONS) {
    if (key.includes(needle)) return icon;
  }
  return "🏅";
}

export function sportLabel(type) {
  const sport = SPORTS.find((s) => s.type === type);
  return sport ? sport.label : (type || "").replace(/_/g, " ");
}
