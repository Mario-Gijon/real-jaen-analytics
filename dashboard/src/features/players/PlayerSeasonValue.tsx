import type { SeasonStat } from "@/contract";
import { SeasonValue } from "../SeasonValue";
import css from "./PlayerDetailView.module.css";

/** Preserve partial coverage and expose the specific reason for missing values. */
export function PlayerSeasonValue({ stat, percent = false }: { stat: SeasonStat; percent?: boolean }) {
  if (stat.value === null) {
    const note = `${stat.reason === "not_collected" ? "Dato no recogido" : "Dato no disponible"} · ${stat.matches} de ${stat.of} partidos`;
    return <span title={note} aria-label={note} className={css.missing}>—</span>;
  }
  return <><SeasonValue stat={stat} decimals={percent ? 1 : 0} />{percent ? " %" : null}</>;
}
