import type { PlayerPosition, PreferredFoot, SeasonStat, Stat } from "@/contract";

/**
 * How contract values read on screen. A missing value is always «—», never 0;
 * a real 0 is «0».
 */
export const MISSING = "—";

const numberFormats = new Map<number, Intl.NumberFormat>();

export function formatNumber(value: number, decimals = 0): string {
  let format = numberFormats.get(decimals);
  if (!format) {
    format = new Intl.NumberFormat("es-ES", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
    numberFormats.set(decimals, format);
  }
  return format.format(value);
}

/** A per-match metric: its number, or «—» whatever the reason. */
export function statText(stat: Stat, decimals = 0): string {
  return stat.value === null ? MISSING : formatNumber(stat.value, decimals);
}

/** A season metric: its number, or «—» when no match had it. */
export function seasonText(stat: SeasonStat, decimals = 0): string {
  return stat.value === null ? MISSING : formatNumber(stat.value, decimals);
}

/** Whether a season figure covers only some of its matches. */
export function isPartial(stat: SeasonStat): boolean {
  return stat.coverage === "partial";
}

export const POSITION_LABEL: Record<PlayerPosition, string> = {
  goalkeeper: "Portero",
  defender: "Defensa",
  midfielder: "Centrocampista",
  forward: "Delantero",
};

export const POSITION_PLURAL: Record<PlayerPosition, string> = {
  goalkeeper: "Porteros",
  defender: "Defensas",
  midfielder: "Centrocampistas",
  forward: "Delanteros",
};

export const POSITION_SHORT: Record<PlayerPosition, string> = {
  goalkeeper: "POR",
  defender: "DEF",
  midfielder: "MED",
  forward: "DEL",
};

/** Football order of the lines, for sorting and filters. */
export const POSITION_ORDER: readonly PlayerPosition[] = [
  "goalkeeper",
  "defender",
  "midfielder",
  "forward",
];

export function positionLabel(position: PlayerPosition | null): string {
  return position ? POSITION_LABEL[position] : MISSING;
}

const FOOT_LABEL: Record<PreferredFoot, string> = {
  right: "Derecha",
  left: "Izquierda",
  both: "Ambas",
};

export function footLabel(foot: PreferredFoot | null): string {
  return foot ? FOOT_LABEL[foot] : MISSING;
}

export function heightLabel(heightCm: number | null): string {
  return heightCm === null ? MISSING : `${formatNumber(heightCm / 100, 2)} m`;
}

/** Whole years between an ISO date (`YYYY-MM-DD`) and `today`. */
export function ageOn(birthDate: string, today: Date): number {
  const [year, month, day] = birthDate.split("-").map(Number);
  let age = today.getFullYear() - year;
  const beforeBirthday =
    today.getMonth() + 1 < month || (today.getMonth() + 1 === month && today.getDate() < day);
  if (beforeBirthday) age--;
  return age;
}

/** «14/03/2008 (18 años)», or «—». */
export function birthLabel(birthDate: string | null, today = new Date()): string {
  if (!birthDate) return MISSING;
  const [year, month, day] = birthDate.split("-");
  return `${day}/${month}/${year} (${ageOn(birthDate, today)} años)`;
}

const dateFormat = new Intl.DateTimeFormat("es-ES", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "Europe/Madrid",
});

/** Kickoff date in Spain, e.g. «22 ago 2026»; «—» when not set. */
export function dateLabel(kickoff: string | null): string {
  return kickoff ? dateFormat.format(new Date(kickoff)).replace(".", "") : MISSING;
}

/** Up to two initials of a name, for crest and avatar fallbacks. */
export function initials(name: string): string {
  return (
    name
      .trim()
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0])
      .join("")
      .toLocaleUpperCase("es") || "?"
  );
}
