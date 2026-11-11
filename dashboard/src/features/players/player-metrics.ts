import type { PlayerMatchStats } from "@/contract";

type Metric = { key: keyof PlayerMatchStats; label: string; percent?: boolean };
export const FIELD_GROUPS: readonly { title: string; metrics: readonly Metric[] }[] = [
  { title: "Finalización", metrics: [
    { key: "shots", label: "Tiros" }, { key: "shotsOnTarget", label: "Tiros a puerta" },
  ] },
  { title: "Juego", metrics: [
    { key: "passes", label: "Pases" }, { key: "passAccuracy", label: "Precisión de pase", percent: true },
    { key: "dribbles", label: "Regates" }, { key: "dribblesWon", label: "Regates ganados" },
  ] },
  { title: "Recuperación", metrics: [
    { key: "recoveries", label: "Recuperaciones" }, { key: "possessionLost", label: "Pérdidas" },
  ] },
  { title: "Disciplina", metrics: [
    { key: "fouls", label: "Faltas" }, { key: "yellowCards", label: "Amarillas" }, { key: "redCards", label: "Rojas" },
  ] },
];
export const KEEPER_GROUPS: typeof FIELD_GROUPS = [
  { title: "Portería", metrics: [{ key: "saves", label: "Paradas" }, { key: "goalsConceded", label: "Goles encajados" }] },
];
