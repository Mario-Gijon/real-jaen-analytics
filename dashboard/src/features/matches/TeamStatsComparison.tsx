import Link from "next/link";
import type { Stat, TeamMatchStats, TeamStatKey } from "@/contract";
import { statText } from "@/lib/format";
import css from "./MatchDetailView.module.css";

const METRICS: readonly { key: TeamStatKey; label: string; decimals?: number }[] = [
  { key: "xg", label: "xG", decimals: 2 },
  { key: "shots", label: "Tiros" },
  { key: "shotsOnTarget", label: "Tiros a puerta" },
  { key: "possession", label: "Posesión" },
  { key: "corners", label: "Córners" },
  { key: "fouls", label: "Faltas" },
  { key: "yellowCards", label: "Amarillas" },
  { key: "redCards", label: "Rojas" },
];

export type StatsSide = { label: string; name: string; stats: TeamMatchStats; href?: string };

/** Home and away team stats face to face; a missing value reads «—», a real 0 reads «0». */
export function TeamStatsComparison({ home: homeSide, away: awaySide }: { home: StatsSide; away: StatsSide }) {
  return <>
    <div className={css.legend}>
      <span>{homeSide.href ? <Link href={homeSide.href}><strong>{homeSide.label}</strong></Link> : <strong>{homeSide.label}</strong>}<span> · Local</span></span>
      <span>{awaySide.href ? <Link href={awaySide.href}><strong>{awaySide.label}</strong></Link> : <strong>{awaySide.label}</strong>}<span> · Visitante</span></span>
    </div>
    <dl className={css.metrics}>
      {METRICS.map(({ key, label, decimals = 0 }) => {
        const home = homeSide.stats[key];
        const away = awaySide.stats[key];
        const maximum = home.value !== null && away.value !== null ? Math.max(home.value, away.value) : 0;
        const comparable = home.value !== null && away.value !== null && maximum > 0;
        return <div className={css.metric} key={key}>
          <dt>{label}</dt>
          <dd className={css.homeValue}><Value stat={home} decimals={decimals}
            team={homeSide.name} percent={key === "possession"} /></dd>
          <dd className={css.awayValue}><Value stat={away} decimals={decimals}
            team={awaySide.name} percent={key === "possession"} /></dd>
          <dd className={css.comparison} aria-hidden="true" data-comparison={comparable || undefined}>
            <span className={css.homeHalf}>
              {comparable && home.value! > 0 ? <span data-bar="home" style={{ width: `${home.value! / maximum * 100}%` }} /> : null}
            </span>
            <span className={css.awayHalf}>
              {comparable && away.value! > 0 ? <span data-bar="away" style={{ width: `${away.value! / maximum * 100}%` }} /> : null}
            </span>
          </dd>
        </div>;
      })}
    </dl>
  </>;
}

function Value({ stat, decimals, team, percent }: {
  stat: Stat; decimals: number; team: string; percent: boolean;
}) {
  const missing = stat.value === null;
  const text = missing ? "—" : stat.value === 0 ? "0" : statText(stat, decimals);
  const reason = missing ? stat.reason === "not_collected" ? "Dato no recogido" : "Dato no disponible" : null;
  return <span aria-label={`${team}: ${reason ?? `${text}${percent ? " %" : ""}`}`} title={reason ?? undefined}>
    {text}{!missing && percent ? " %" : ""}
  </span>;
}
