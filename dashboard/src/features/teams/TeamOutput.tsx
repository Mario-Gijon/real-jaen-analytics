"use client";

import type { SeasonStat } from "@/contract";
import { formatNumber, MISSING } from "@/lib/format";
import { difference, type Indicator } from "./performance";
import css from "./TeamOutput.module.css";

type Measure = { indicator: Indicator; label: string; decimals: number };

/** Attack and defence mirror each other: goals and xG lead, shots follow. */
const UNITS: readonly { title: string; main: Measure[]; secondary: Measure[] }[] = [
  {
    title: "Ataque",
    main: [
      { indicator: "goals", label: "Goles / partido", decimals: 2 },
      { indicator: "xg", label: "xG / partido", decimals: 2 },
    ],
    secondary: [
      { indicator: "shots", label: "Tiros / partido", decimals: 1 },
      { indicator: "shotsOnTarget", label: "Tiros a puerta / partido", decimals: 1 },
    ],
  },
  {
    title: "Defensa",
    main: [
      { indicator: "goalsAgainst", label: "Goles encajados / partido", decimals: 2 },
      { indicator: "xgAgainst", label: "xG concedidos / partido", decimals: 2 },
    ],
    secondary: [
      { indicator: "shotsAgainst", label: "Tiros concedidos / partido", decimals: 1 },
      { indicator: "shotsOnTargetAgainst", label: "Tiros a puerta concedidos / partido", decimals: 1 },
    ],
  },
];

const CONTEXT: readonly Measure[] = [
  { indicator: "possession", label: "Posesión media", decimals: 1 },
  { indicator: "corners", label: "Córners / partido", decimals: 1 },
];

/** «+0,24», «−1,50» or «±0,00»: a plain difference, never good or bad. */
function signed(value: number, decimals: number): string {
  const text = formatNumber(Math.abs(value), decimals);
  if (Number(Math.abs(value).toFixed(decimals)) === 0) return `±${text}`;
  return `${value > 0 ? "+" : "−"}${text}`;
}

function Value({
  stat,
  decimals,
  suffix = "",
}: {
  stat: SeasonStat;
  decimals: number;
  suffix?: string;
}) {
  if (stat.value === null) {
    return (
      <span className={css.missing} title="Sin datos">
        {MISSING}
      </span>
    );
  }
  return (
    <>
      {formatNumber(stat.value, decimals)}
      {suffix}
      {stat.coverage === "partial" ? (
        <sup className={css.partial} title={`Media de ${stat.matches} de ${stat.of} partidos`}>
          *
        </sup>
      ) : null}
    </>
  );
}

/** «+0,24 vs temporada»: secondary, neutral, and flagged when either side is partial. */
function Delta({
  period,
  season,
  decimals,
}: {
  period: SeasonStat;
  season: SeasonStat;
  decimals: number;
}) {
  const diff = difference(period, season);
  if (diff === null) return null;
  const partial = period.coverage === "partial" || season.coverage === "partial";
  return (
    <span className={css.delta} title={partial ? "Comparación con datos parciales" : undefined}>
      {signed(diff, decimals)} vs temporada
      {partial ? <sup className={css.partial}>*</sup> : null}
    </span>
  );
}

/**
 * «¿Cuánto produce y cuánto concede?»: per-match averages of attack and
 * defence, and the game context, for the season or for the last three
 * finished matches (`compare`, then compared with the season). The averages
 * come computed, with their coverage, from whichever source.
 */
export function TeamOutput({
  compare,
  values,
  seasonValue,
}: {
  compare: boolean;
  values: (indicator: Indicator) => SeasonStat;
  seasonValue: (indicator: Indicator) => SeasonStat;
}) {
  const partial = [...UNITS.flatMap((u) => [...u.main, ...u.secondary]), ...CONTEXT].some(
    ({ indicator }) =>
      values(indicator).coverage === "partial" ||
      (compare && seasonValue(indicator).coverage === "partial"),
  );

  return (
    <div className={css.output}>
      <div className={css.units}>
        {UNITS.map((unit) => (
          <section aria-label={unit.title} className={css.unit} key={unit.title}>
            <h4 className={css.unitTitle}>{unit.title}</h4>
            <dl className={css.main}>
              {unit.main.map((m) => (
                <div key={m.indicator}>
                  <dt>{m.label}</dt>
                  <dd data-indicator={m.indicator}>
                    <span className={css.mainValue}>
                      <Value decimals={m.decimals} stat={values(m.indicator)} />
                    </span>
                    {compare ? (
                      <Delta
                        decimals={m.decimals}
                        period={values(m.indicator)}
                        season={seasonValue(m.indicator)}
                      />
                    ) : null}
                  </dd>
                </div>
              ))}
            </dl>
            <dl className={css.secondary}>
              {unit.secondary.map((m) => (
                <div key={m.indicator}>
                  {/* Label first in the markup; the value reads first on screen (CSS order). */}
                  <dt>{m.label}</dt>
                  <dd data-indicator={m.indicator}>
                    <strong>
                      <Value decimals={m.decimals} stat={values(m.indicator)} />
                    </strong>
                  </dd>
                  {compare ? (
                    <dd className={css.secondaryDelta}>
                      <Delta
                        decimals={m.decimals}
                        period={values(m.indicator)}
                        season={seasonValue(m.indicator)}
                      />
                    </dd>
                  ) : null}
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>

      <section aria-label="Contexto de juego" className={css.context}>
        <h4 className={css.contextTitle}>Contexto de juego</h4>
        <dl>
          {CONTEXT.map((m) => (
            <div key={m.indicator}>
              <dt>{m.label}</dt>
              <dd data-indicator={m.indicator}>
                <strong>
                  <Value
                    decimals={m.decimals}
                    stat={values(m.indicator)}
                    suffix={m.indicator === "possession" ? " %" : ""}
                  />
                </strong>
                {compare ? (
                  <Delta
                    decimals={m.decimals}
                    period={values(m.indicator)}
                    season={seasonValue(m.indicator)}
                  />
                ) : null}
              </dd>
            </div>
          ))}
        </dl>
      </section>

      {partial ? (
        <p className={css.note}>
          <sup className={css.partial}>*</sup> Media solo de los partidos con dato.
        </p>
      ) : null}
    </div>
  );
}
