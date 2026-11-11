/**
 * Invented, internally coherent season for development and visual testing.
 * Pure: the same seed always yields the same dataset. Real Jaén CF is the
 * only real name; its first 14 rounds follow a script (`RJ_PLAN`) so every
 * state the interface must show exists on purpose, not by chance.
 */
import type {
  RawDataset,
  RawEvent,
  RawLineup,
  RawMatch,
  RawPlayer,
  RawPlayerStats,
  RawPosition,
  RawRoundFile,
  RawSeasonFile,
  RawTeamStats,
} from "../../src/server/synthetic/raw";
import { CLUBS, COLORS, FIRST_NAMES, SURNAMES } from "./names.mts";
import { createRng, type Rng } from "./random.mts";

export const SEED = 1922;

export const COMPETITION_ID = "competition-demo";
export const SEASON_ID = "season-demo-2026-27";
export const REAL_JAEN_ID = "team-real-jaen";
const ROUNDS_PLAYED = 14;
/** First round on Saturday 22 August 2026. */
const FIRST_ROUND = Date.UTC(2026, 7, 22);

type Line = { position: RawPosition; count: number };
const FORMATIONS: Record<string, Line[]> = {
  "4-4-2": [line("defender", 4), line("midfielder", 4), line("forward", 2)],
  "4-3-3": [line("defender", 4), line("midfielder", 3), line("forward", 3)],
  "4-2-3-1": [
    line("defender", 4),
    line("midfielder", 2),
    line("midfielder", 3),
    line("forward", 1),
  ],
  "3-5-2": [line("defender", 3), line("midfielder", 5), line("forward", 2)],
  "5-3-2": [line("defender", 5), line("midfielder", 3), line("forward", 2)],
  "4-1-4-1": [
    line("defender", 4),
    line("midfielder", 1),
    line("midfielder", 4),
    line("forward", 1),
  ],
};
const FORMATION_NAMES = Object.keys(FORMATIONS);

function line(position: RawPosition, count: number): Line {
  return { position, count };
}

/** Squad shape: 3 goalkeepers and 21 outfield players. */
const SQUAD: Line[] = [
  line("goalkeeper", 3),
  line("defender", 8),
  line("midfielder", 8),
  line("forward", 5),
];
const OUTFIELD_NUMBERS = [
  2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23,
];
const GOALKEEPER_NUMBERS = [1, 13, 25];
const BENCH_SIZE = 9;
const SUB_MINUTES = [46, 46, 55, 60, 63, 66, 68, 70, 72, 75, 78, 80, 83, 86];

/**
 * Script of Real Jaén's first 14 rounds: results and the special cases the
 * interface must handle. Round 9 is postponed.
 */
type Plan = {
  score: [rj: number, opponent: number];
  formation: string;
  subs: number;
  /** The academy player with limited stats comes on as the first change. */
  academyOn?: boolean;
  /** One of Real Jaén's goals is an own goal by an opponent. */
  opponentOwnGoal?: boolean;
  firstGoalPenalty?: boolean;
  lastGoalAddedTime?: number;
  rjRed?: Red;
  opponentRed?: Red;
  /** The opponent's line-up comes without formation nor slots. */
  opponentShapeUnknown?: boolean;
  missing?: "not_collected" | "xg_not_available";
};
type Red = { minute: number; card: "second_yellow" | "red" };

const RJ_PLAN: Record<number, Plan> = {
  1: { score: [2, 1], formation: "4-2-3-1", subs: 3, firstGoalPenalty: true },
  2: { score: [0, 0], formation: "4-3-3", subs: 5, academyOn: true },
  3: { score: [1, 2], formation: "4-4-2", subs: 2 },
  4: { score: [4, 3], formation: "4-2-3-1", subs: 4, lastGoalAddedTime: 3, academyOn: true },
  5: { score: [1, 1], formation: "4-3-3", subs: 5, missing: "not_collected" },
  6: { score: [0, 1], formation: "3-5-2", subs: 3, opponentShapeUnknown: true, academyOn: true },
  7: { score: [3, 0], formation: "4-2-3-1", subs: 4, opponentRed: { minute: 58, card: "red" } },
  8: { score: [2, 2], formation: "4-4-2", subs: 1, rjRed: { minute: 71, card: "second_yellow" } },
  10: { score: [1, 0], formation: "5-3-2", subs: 5, academyOn: true },
  11: { score: [0, 2], formation: "4-2-3-1", subs: 3, missing: "xg_not_available" },
  12: { score: [2, 0], formation: "4-3-3", subs: 4, opponentOwnGoal: true },
  13: { score: [1, 3], formation: "4-1-4-1", subs: 2 },
  14: { score: [2, 1], formation: "4-2-3-1", subs: 5, academyOn: true },
};
const POSTPONED_ROUND = 9;

type GenPlayer = RawPlayer & {
  position: RawPosition;
  shirtNumber: number;
  quality: number;
  /** Only minutes, goals, assists and cards are known for this player. */
  limited: boolean;
};
type GenTeam = RawSeasonFile["teams"][number] & {
  strength: number;
  formation: string;
  players: GenPlayer[];
};

// ─── Teams and players ──────────────────────────────────────────────────────

function slug(text: string): string {
  return text
    .normalize("NFD")
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-");
}

function createTeams(rng: Rng): GenTeam[] {
  const realJaen: GenTeam = {
    id: REAL_JAEN_ID,
    name: "Real Jaén CF",
    shortName: "Real Jaén",
    crestUrl: "/crests/real-jaen.webp",
    colors: { primary: "#592888", secondary: "#ffffff" },
    strength: 1.1,
    formation: "4-2-3-1",
    players: [],
  };
  const rivals = CLUBS.map(([name, shortName], index): GenTeam => ({
    id: `team-${slug(shortName)}`,
    name,
    shortName,
    crestUrl: null,
    colors: { primary: COLORS[index][0], secondary: COLORS[index][1] },
    strength: 0.75 + rng.next() * 0.55,
    formation: rng.pick(FORMATION_NAMES),
    players: [],
  }));
  const teams = [realJaen, ...rivals];
  const usedNames = new Set<string>();
  let next = 1;
  for (const team of teams) {
    const outfield = [...OUTFIELD_NUMBERS];
    const goalkeepers = [...GOALKEEPER_NUMBERS];
    for (const { position, count } of SQUAD) {
      for (let i = 0; i < count; i++) {
        let name: string;
        do name = `${rng.pick(FIRST_NAMES)} ${rng.pick(SURNAMES)}`;
        while (usedNames.has(name));
        usedNames.add(name);
        team.players.push({
          id: `player-${String(next++).padStart(4, "0")}`,
          name,
          position,
          birthDate: rng.chance(0.03) ? null : birthDate(rng),
          heightCm: rng.chance(0.04) ? null : height(rng, position),
          preferredFoot: rng.chance(0.04) ? null : rng.weighted(FEET, ([, weight]) => weight)[0],
          shirtNumber: position === "goalkeeper" ? goalkeepers.shift()! : outfield.shift()!,
          quality: rng.next(),
          limited: false,
        });
      }
    }
  }
  // Real Jaén cases: no height, no preferred foot, an academy player with limited stats.
  const rj = realJaen.players;
  const best = (position: RawPosition) =>
    rj.filter((p) => p.position === position).sort((a, b) => b.quality - a.quality)[0];
  best("defender").heightCm = null;
  best("forward").preferredFoot = null;
  const academy = rj
    .filter((p) => p.position === "midfielder")
    .sort((a, b) => a.quality - b.quality)[0];
  academy.limited = true;
  academy.quality = -1;
  academy.birthDate = "2008-03-14";
  academy.heightCm = rng.int(170, 178);
  academy.preferredFoot = "right";
  return teams;
}

const FEET = [
  ["right", 70],
  ["left", 25],
  ["both", 5],
] as const;

function birthDate(rng: Rng): string {
  const day = new Date(Date.UTC(rng.int(1992, 2007), 0, 1) + rng.int(0, 364) * 86_400_000);
  return day.toISOString().slice(0, 10);
}

function height(rng: Rng, position: RawPosition): number {
  const [min, max] = {
    goalkeeper: [183, 197],
    defender: [175, 192],
    midfielder: [167, 186],
    forward: [169, 191],
  }[position];
  return rng.int(min, max);
}

// ─── Calendar ───────────────────────────────────────────────────────────────

/** Double round robin (circle method), Real Jaén fixed and alternating home/away. */
function calendar(rng: Rng, teams: GenTeam[]): [home: GenTeam, away: GenTeam][][] {
  const [fixed, ...rest] = [teams[0], ...rng.shuffle(teams.slice(1))];
  const firstLeg: [GenTeam, GenTeam][][] = [];
  for (let r = 0; r < teams.length - 1; r++) {
    const ring = [fixed, ...rest.slice(r), ...rest.slice(0, r)];
    const pairs: [GenTeam, GenTeam][] = [];
    for (let i = 0; i < teams.length / 2; i++) {
      const a = ring[i];
      const b = ring[teams.length - 1 - i];
      pairs.push((r + i) % 2 === 0 ? [a, b] : [b, a]);
    }
    firstLeg.push(pairs);
  }
  const secondLeg = firstLeg.map((pairs) => pairs.map(([h, a]): [GenTeam, GenTeam] => [a, h]));
  return [...firstLeg, ...secondLeg];
}

function kickoff(round: number, index: number): string {
  const day = FIRST_ROUND + (round - 1) * 7 * 86_400_000 + (index < 5 ? 0 : 86_400_000);
  const [hour, minute] = [
    [10, 0],
    [14, 0],
    [16, 15],
    [18, 30],
    [19, 0],
  ][index % 5];
  return new Date(day + (hour * 60 + minute) * 60_000).toISOString().replace(".000Z", "Z");
}

// ─── One side of a match ────────────────────────────────────────────────────

type SideOptions = {
  goals: number;
  /** Own goals by this side's players (they count for the opponent). */
  ownGoals: number;
  subs: number;
  formation: string;
  shapeUnknown: boolean;
  academyOn: boolean;
  firstGoalPenalty: boolean;
  lastGoalAddedTime: number | null;
  red: Red | null;
};

/** Minutes on the pitch: `on`/`off` are match times (minute + addedTime / 100). */
type Interval = { on: number; off: number };
/** An event before it gets its id (once both sides are merged and sorted). */
type Draft<E> = E extends RawEvent ? Omit<E, "id"> : never;
type EventDraft = Draft<RawEvent>;

type Side = {
  team: GenTeam;
  lineup: RawLineup;
  starters: GenPlayer[];
  intervals: Map<string, Interval>;
  events: EventDraft[];
};

const time = (minute: number, addedTime: number | null) => minute + (addedTime ?? 0) / 100;
const onPitch = (interval: Interval | undefined, t: number) =>
  interval !== undefined && interval.on < t && t < interval.off;

function playSide(rng: Rng, team: GenTeam, options: SideOptions): Side {
  // Line-up: the best available by position, with some rotation.
  const score = new Map(team.players.map((p) => [p.id, p.quality + rng.next() * 0.6]));
  const ranked = (position: RawPosition) =>
    team.players
      .filter((p) => p.position === position)
      .sort((a, b) => score.get(b.id)! - score.get(a.id)! || a.id.localeCompare(b.id));
  const keepers = ranked("goalkeeper").sort((a, b) => b.quality - a.quality);
  const [goalkeeper, backupKeeper] = rng.chance(0.85)
    ? [keepers[0], keepers[1]]
    : [keepers[1], keepers[0]];

  const lines = FORMATIONS[options.formation];
  const pool = new Map<RawPosition, GenPlayer[]>(
    (["defender", "midfielder", "forward"] as const).map((position) => [
      position,
      ranked(position).filter((p) => !p.limited),
    ]),
  );
  const starters: GenPlayer[] = [goalkeeper];
  const slots: [number, number][] = [[0, 0]];
  lines.forEach(({ position, count }, index) => {
    const chosen = pool.get(position)!.splice(0, count);
    chosen.forEach((player, order) => {
      starters.push(player);
      slots.push([index + 1, order]);
    });
  });
  const academy = team.players.filter((p) => p.limited);
  const rest = [...pool.values()].flat().sort((a, b) => score.get(b.id)! - score.get(a.id)!);
  const bench = [backupKeeper, ...academy, ...rest].slice(0, BENCH_SIZE);

  const lineup: RawLineup = {
    formation: options.shapeUnknown ? null : options.formation,
    starters: starters.map((p, i) => ({
      playerId: p.id,
      shirtNumber: p.shirtNumber,
      slot: options.shapeUnknown ? null : slots[i],
    })),
    bench: bench.map((p) => ({ playerId: p.id, shirtNumber: p.shirtNumber, slot: null })),
  };

  const intervals = new Map<string, Interval>(
    starters.map((p) => [p.id, { on: 0, off: Infinity }]),
  );
  const events: EventDraft[] = [];
  const event = (draft: EventDraft) => events.push(draft);
  const base = (minute: number, addedTime: number | null = null) => ({
    teamId: team.id,
    minute,
    addedTime,
    clip: null,
  });

  // Sending-off: an outfield starter, never the goalkeeper.
  const sentOff = options.red ? rng.pick(starters.slice(1)) : null;
  if (options.red && sentOff) {
    if (options.red.card === "second_yellow") {
      event({
        ...base(rng.int(20, options.red.minute - 12)),
        type: "card",
        playerId: sentOff.id,
        card: "yellow",
      });
    }
    event({
      ...base(options.red.minute),
      type: "card",
      playerId: sentOff.id,
      card: options.red.card,
    });
    intervals.get(sentOff.id)!.off = options.red.minute;
  }

  // Substitutions: an outfield starter leaves, a substitute of the same line comes on when possible.
  const minutes = rng
    .shuffle(SUB_MINUTES)
    .slice(0, options.subs)
    .sort((a, b) => a - b);
  const entered = new Set<string>();
  minutes.forEach((minute, index) => {
    const leaving = starters.filter(
      (p) => p !== goalkeeper && p !== sentOff && intervals.get(p.id)!.off === Infinity,
    );
    const available = bench.filter((p) => p !== backupKeeper && !entered.has(p.id));
    if (leaving.length === 0 || available.length === 0) return;
    const playerIn =
      index === 0 && options.academyOn && academy[0] && available.includes(academy[0])
        ? academy[0]
        : rng.pick(available.filter((p) => !p.limited));
    const sameLine = leaving.filter((p) => p.position === playerIn.position);
    const playerOut = rng.pick(sameLine.length > 0 ? sameLine : leaving);
    entered.add(playerIn.id);
    intervals.get(playerOut.id)!.off = minute;
    intervals.set(playerIn.id, { on: minute, off: Infinity });
    event({
      ...base(minute),
      type: "substitution",
      playerInId: playerIn.id,
      playerOutId: playerOut.id,
    });
  });

  // Goals: scorer and assistant on the pitch at that moment.
  const busy = new Set([...minutes, options.red?.minute ?? -1]);
  const goalTimes: [number, number | null][] = [];
  for (let i = 0; i < options.goals; i++) {
    let minute: number;
    do minute = rng.int(1, 90);
    while (busy.has(minute));
    goalTimes.push([minute, minute === 45 || minute === 90 ? rng.int(1, 4) : null]);
  }
  goalTimes.sort((a, b) => time(...a) - time(...b));
  if (options.lastGoalAddedTime !== null && goalTimes.length > 0) {
    goalTimes[goalTimes.length - 1] = [90, options.lastGoalAddedTime];
  }
  goalTimes.forEach(([minute, addedTime], index) => {
    const t = time(minute, addedTime);
    const present = [...intervals.keys()]
      .filter((id) => onPitch(intervals.get(id), t))
      .map((id) => team.players.find((p) => p.id === id)!)
      .filter((p) => p.position !== "goalkeeper");
    const scorer = rng.weighted(present, (p) => GOAL_WEIGHT[p.position]);
    const penalty = index === 0 && options.firstGoalPenalty ? true : rng.chance(0.08);
    const mates = present.filter((p) => p !== scorer);
    const assist =
      !penalty && mates.length > 0 && rng.chance(0.72)
        ? rng.weighted(mates, (p) => ASSIST_WEIGHT[p.position])
        : null;
    event({
      ...base(minute, addedTime),
      type: "goal",
      playerId: scorer.id,
      assistId: assist?.id ?? null,
      penalty,
      ownGoal: false,
    });
  });

  // Own goals: any outfield player on the pitch; never assisted nor a penalty.
  for (let i = 0; i < options.ownGoals; i++) {
    let minute: number;
    do minute = rng.int(1, 89);
    while (busy.has(minute));
    const present = [...intervals.keys()]
      .filter((id) => onPitch(intervals.get(id), minute))
      .map((id) => team.players.find((p) => p.id === id)!)
      .filter((p) => p.position !== "goalkeeper");
    event({
      ...base(minute),
      type: "goal",
      playerId: rng.pick(present).id,
      assistId: null,
      penalty: false,
      ownGoal: true,
    });
  }

  // Yellow cards, at most one per player (the sent-off player already has theirs).
  const booked = new Set(sentOff ? [sentOff.id] : []);
  const yellows = Math.min(rng.poisson(1.8), 5);
  for (let i = 0; i < yellows; i++) {
    const minute = rng.int(8, 90);
    const addedTime = minute === 45 || minute === 90 ? rng.int(1, 3) : null;
    const t = time(minute, addedTime);
    const candidates = [...intervals.keys()].filter(
      (id) => !booked.has(id) && onPitch(intervals.get(id), t),
    );
    if (candidates.length === 0) continue;
    const playerId = rng.pick(candidates);
    booked.add(playerId);
    event({ ...base(minute, addedTime), type: "card", playerId, card: "yellow" });
  }

  return { team, lineup, starters, intervals, events };
}

const GOAL_WEIGHT: Record<RawPosition, number> = {
  goalkeeper: 0,
  defender: 1,
  midfielder: 3,
  forward: 7,
};
const ASSIST_WEIGHT: Record<RawPosition, number> = {
  goalkeeper: 0,
  defender: 2,
  midfielder: 5,
  forward: 3,
};

// ─── Statistics ─────────────────────────────────────────────────────────────

type NumericStats = { [K in keyof RawPlayerStats]: number };

const RATES: Record<
  RawPosition,
  {
    shots: number;
    passes: number;
    accuracy: number;
    dribbles: number;
    recoveries: number;
    lost: number;
    fouls: number;
  }
> = {
  goalkeeper: {
    shots: 0,
    passes: 28,
    accuracy: 0.7,
    dribbles: 0,
    recoveries: 3,
    lost: 4,
    fouls: 0.1,
  },
  defender: {
    shots: 0.5,
    passes: 52,
    accuracy: 0.86,
    dribbles: 0.6,
    recoveries: 6.5,
    lost: 7,
    fouls: 1.3,
  },
  midfielder: {
    shots: 1.3,
    passes: 55,
    accuracy: 0.84,
    dribbles: 1.8,
    recoveries: 6,
    lost: 10,
    fouls: 1.2,
  },
  forward: {
    shots: 2.8,
    passes: 24,
    accuracy: 0.74,
    dribbles: 3,
    recoveries: 2.5,
    lost: 12,
    fouls: 1,
  },
};

/** Fields hidden for a player with limited stats. */
const LIMITED_FIELDS = [
  "shots",
  "shotsOnTarget",
  "passes",
  "passesCompleted",
  "dribbles",
  "dribblesWon",
  "recoveries",
  "possessionLost",
  "fouls",
] as const;

function playerNumbers(
  rng: Rng,
  side: Side,
  player: GenPlayer,
  events: EventDraft[],
): NumericStats {
  const { on, off } = side.intervals.get(player.id)!;
  const minutes = Math.min(off, 90) - on;
  const f = minutes / 90;
  const rate = RATES[player.position];
  const count = (test: (e: EventDraft) => boolean) => events.filter(test).length;
  const goals = count((e) => e.type === "goal" && !e.ownGoal && e.playerId === player.id);
  const shots = goals + rng.poisson(rate.shots * f);
  const passes = rng.poisson(rate.passes * f);
  const accuracy = Math.min(1, Math.max(0.4, rate.accuracy + (rng.next() - 0.5) * 0.14));
  const dribbles = rng.poisson(rate.dribbles * f);
  return {
    minutes,
    goals,
    assists: count((e) => e.type === "goal" && e.assistId === player.id),
    shots,
    shotsOnTarget: goals + rng.binomial(shots - goals, 0.33),
    passes,
    passesCompleted: Math.round(passes * accuracy),
    dribbles,
    dribblesWon: rng.binomial(dribbles, 0.5),
    recoveries: rng.poisson(rate.recoveries * f),
    possessionLost: rng.poisson(rate.lost * f),
    fouls: rng.poisson(rate.fouls * f),
    yellowCards: count((e) => e.type === "card" && e.playerId === player.id && e.card === "yellow"),
    redCards: count((e) => e.type === "card" && e.playerId === player.id && e.card !== "yellow"),
    saves: 0,
    goalsConceded: 0,
  };
}

const round2 = (value: number) => Math.round(value * 100) / 100;

// ─── Match ──────────────────────────────────────────────────────────────────

function playMatch(
  rng: Rng,
  id: string,
  round: number,
  kickoffAt: string,
  home: GenTeam,
  away: GenTeam,
): RawMatch {
  const plan = home.id === REAL_JAEN_ID || away.id === REAL_JAEN_ID ? RJ_PLAN[round] : undefined;
  const sideOptions = (team: GenTeam, opponent: GenTeam, isHome: boolean): SideOptions => {
    const isRj = team.id === REAL_JAEN_ID;
    const ownGoals = !isRj && plan?.opponentOwnGoal ? 1 : 0;
    const goals = plan
      ? plan.score[isRj ? 0 : 1] - (isRj && plan.opponentOwnGoal ? 1 : 0)
      : Math.min(rng.poisson((isHome ? 1.45 : 1.1) * (team.strength / opponent.strength)), 6);
    return {
      goals,
      ownGoals,
      subs: plan && isRj ? plan.subs : rng.int(2, 5),
      formation:
        plan && isRj
          ? plan.formation
          : rng.chance(0.8)
            ? team.formation
            : rng.pick(FORMATION_NAMES),
      shapeUnknown: !isRj && plan?.opponentShapeUnknown === true,
      academyOn: isRj && plan?.academyOn === true,
      firstGoalPenalty: isRj && plan?.firstGoalPenalty === true,
      lastGoalAddedTime: (isRj && plan?.lastGoalAddedTime) || null,
      red: plan
        ? ((isRj ? plan.rjRed : plan.opponentRed) ?? null)
        : rng.chance(0.05)
          ? { minute: rng.int(50, 85), card: rng.pick(["red", "second_yellow"] as const) }
          : null,
    };
  };
  const sides = {
    home: playSide(rng, home, sideOptions(home, away, true)),
    away: playSide(rng, away, sideOptions(away, home, false)),
  };

  const order = { goal: 0, card: 1, substitution: 2 };
  const drafts = [...sides.home.events, ...sides.away.events].sort(
    (a, b) =>
      time(a.minute, a.addedTime) - time(b.minute, b.addedTime) ||
      Number(a.teamId === away.id) - Number(b.teamId === away.id) ||
      order[a.type] - order[b.type],
  );
  const events = drafts.map(
    (draft, i) => ({ id: `${id}-e${String(i + 1).padStart(2, "0")}`, ...draft }) as RawEvent,
  );
  /** Goals in the score: the team's own goals plus the opponent's own goals. */
  const goals = (team: GenTeam) =>
    drafts.filter((e) => e.type === "goal" && (e.teamId === team.id) !== e.ownGoal).length;
  const score = { home: goals(home), away: goals(away) };

  // Player numbers (all known internally), then goalkeepers, then what the source hides.
  const numbers = new Map<
    string,
    { side: "home" | "away"; player: GenPlayer; stats: NumericStats }
  >();
  for (const key of ["home", "away"] as const) {
    const side = sides[key];
    for (const id of side.intervals.keys()) {
      const player = side.team.players.find((p) => p.id === id)!;
      numbers.set(id, { side: key, player, stats: playerNumbers(rng, side, player, drafts) });
    }
  }
  const total = (key: "home" | "away", field: keyof NumericStats) =>
    [...numbers.values()].filter((n) => n.side === key).reduce((sum, n) => sum + n.stats[field], 0);
  for (const n of numbers.values()) {
    if (n.player.position !== "goalkeeper") continue;
    const other = n.side === "home" ? "away" : "home";
    // Saves: shots on target faced minus goals from those shots (own goals are not shots).
    const shotGoals = drafts.filter(
      (e) => e.type === "goal" && e.teamId === sides[other].team.id && !e.ownGoal,
    ).length;
    n.stats.goalsConceded = score[other];
    n.stats.saves = total(other, "shotsOnTarget") - shotGoals;
  }

  const possessionHome = Math.min(
    68,
    Math.max(32, Math.round(50 + (home.strength - away.strength) * 30 + (rng.next() - 0.5) * 16)),
  );
  const teamStats = (key: "home" | "away"): RawTeamStats => {
    const possession = key === "home" ? possessionHome : 100 - possessionHome;
    const teamId = key === "home" ? home.id : away.id;
    const shots = total(key, "shots");
    const onTarget = total(key, "shotsOnTarget");
    const penalties = drafts.filter(
      (e) => e.type === "goal" && e.teamId === teamId && e.penalty,
    ).length;
    const cards = drafts.filter((e) => e.type === "card" && e.teamId === teamId);
    return {
      possession,
      shots,
      shotsOnTarget: onTarget,
      xg: round2(
        (shots - onTarget) * 0.05 +
          (onTarget - penalties) * 0.19 +
          penalties * 0.76 +
          rng.next() * 0.25,
      ),
      corners: rng.poisson(2 + possession / 20),
      fouls: total(key, "fouls"),
      yellowCards: cards.filter((e) => e.type === "card" && e.card === "yellow").length,
      redCards: cards.filter((e) => e.type === "card" && e.card !== "yellow").length,
    };
  };
  const stats = { home: teamStats("home"), away: teamStats("away") };
  if (plan?.missing === "not_collected") {
    stats.home.xg = stats.away.xg = "not_collected";
  } else if (plan?.missing === "xg_not_available") {
    stats.home.xg = stats.away.xg = "not_available";
  }

  const playerStats: Record<string, RawPlayerStats> = {};
  for (const [id, n] of numbers) {
    const row: RawPlayerStats = { ...n.stats };
    if (n.player.position !== "goalkeeper") row.saves = row.goalsConceded = "not_available";
    if (n.player.limited) for (const field of LIMITED_FIELDS) row[field] = "not_available";
    if (plan?.missing === "not_collected") row.passes = row.passesCompleted = "not_collected";
    playerStats[id] = row;
  }

  return {
    id,
    round,
    kickoff: kickoffAt,
    status: "finished",
    homeTeamId: home.id,
    awayTeamId: away.id,
    score,
    lineups: { home: sides.home.lineup, away: sides.away.lineup },
    events,
    teamStats: stats,
    playerStats,
  };
}

// ─── Season ─────────────────────────────────────────────────────────────────

export function generateDataset(seed = SEED): RawDataset {
  const rng = createRng(seed);
  const teams = createTeams(rng);
  const rounds: RawRoundFile[] = calendar(rng, teams).map((pairs, r) => {
    const round = r + 1;
    const matches = pairs.map(([home, away], index): RawMatch => {
      const id = `match-r${String(round).padStart(2, "0")}-${String(index + 1).padStart(2, "0")}`;
      const involvesRj = home.id === REAL_JAEN_ID || away.id === REAL_JAEN_ID;
      const at = kickoff(round, index);
      if (round <= ROUNDS_PLAYED && !(involvesRj && round === POSTPONED_ROUND)) {
        return playMatch(rng, id, round, at, home, away);
      }
      const postponed = round <= ROUNDS_PLAYED;
      return {
        id,
        round,
        kickoff: postponed ? null : at,
        status: postponed ? "postponed" : "scheduled",
        homeTeamId: home.id,
        awayTeamId: away.id,
        score: null,
        lineups: null,
        events: [],
        teamStats: null,
        playerStats: {},
      };
    });
    return { round, matches };
  });

  return {
    season: {
      competition: { id: COMPETITION_ID, name: "Liga de Demostración", shortName: "Liga Demo" },
      season: {
        id: SEASON_ID,
        competitionId: COMPETITION_ID,
        name: "2026/27",
        rounds: rounds.length,
      },
      teams: teams.map(({ id, name, shortName, crestUrl, colors }) => ({
        id,
        name,
        shortName,
        crestUrl,
        colors,
      })),
      squads: teams.map((team) => ({
        teamId: team.id,
        players: team.players.map((p) => ({ playerId: p.id, shirtNumber: p.shirtNumber })),
      })),
    },
    players: teams.flatMap((team) =>
      team.players.map(({ id, name, position, birthDate, heightCm, preferredFoot }) => ({
        id,
        name,
        position,
        birthDate,
        heightCm,
        preferredFoot,
      })),
    ),
    rounds,
  };
}

// ─── Files ──────────────────────────────────────────────────────────────────

/** Files under `data/synthetic`, by relative path. */
export function datasetFiles(dataset: RawDataset): Map<string, string> {
  const files = new Map<string, string>();
  files.set("season.json", formatJson(dataset.season));
  files.set("players.json", formatJson(dataset.players));
  for (const round of dataset.rounds) {
    files.set(`rounds/round-${String(round.round).padStart(2, "0")}.json`, formatJson(round));
  }
  return files;
}

/**
 * JSON with one record per line (a line-up player, an event, a stats row), so
 * the files stay compact, readable and diffable.
 */
export function formatJson(value: unknown): string {
  return `${format(value, "")}\n`;
}

const isPrimitive = (value: unknown) => value === null || typeof value !== "object";
const isFlat = (value: unknown) =>
  isPrimitive(value) || (Array.isArray(value) && value.every(isPrimitive));

function format(value: unknown, indent: string): string {
  const flat = JSON.stringify(value);
  if (isFlat(value) || flat.length <= 100 || Object.values(value as object).every(isFlat))
    return flat;
  const inner = `${indent}  `;
  if (Array.isArray(value)) {
    return `[\n${value.map((item) => inner + format(item, inner)).join(",\n")}\n${indent}]`;
  }
  const entries = Object.entries(value as object).map(
    ([key, item]) => `${inner}${JSON.stringify(key)}: ${format(item, inner)}`,
  );
  return `{\n${entries.join(",\n")}\n${indent}}`;
}
