import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import {
  PLAYER_STAT_KEYS,
  seasonStat,
  TEAM_STAT_KEYS,
  type Accumulation,
  type Competition,
  type Lineup,
  type MatchDetail,
  type MatchEvent,
  type MatchFilter,
  type MatchSummary,
  type ObservedPlayers,
  type PlayerMatch,
  type PlayerMatchStats,
  type PlayerRef,
  type PlayerSeason,
  type PlayerSeasonStats,
  type Season,
  type Side,
  type Stat,
  type TeamMatch,
  type TeamMatchStats,
  type TeamRecord,
  type TeamRef,
  type TeamSeason,
  type TeamStanding,
} from "@/contract";
import type { DataSource } from "../data-source";
import type {
  RawDataset,
  RawEvent,
  RawLineup,
  RawMatch,
  RawPlayerStats,
  RawStat,
  RawTeamStats,
} from "./raw";

/** How the demonstration labels itself on every screen. */
export const DEMO_LABEL = "Demostración · datos sintéticos";

/** Only this adapter knows where the synthetic season lives and how it is laid out. */
const DATA_DIR = join(process.cwd(), "data", "synthetic");

export function loadSyntheticDataset(dir = DATA_DIR): RawDataset {
  const read = (path: string) => JSON.parse(readFileSync(join(dir, path), "utf8"));
  const roundFiles = readdirSync(join(dir, "rounds"))
    .filter((name) => name.endsWith(".json"))
    .sort();
  return {
    season: read("season.json"),
    players: read("players.json"),
    rounds: roundFiles.map((name) => read(join("rounds", name))),
  };
}

/** A `DataSource` over an already loaded synthetic dataset. */
export function createSyntheticSource(data: RawDataset): DataSource {
  const index = buildIndex(data);
  return {
    info: () => ({ synthetic: true, label: DEMO_LABEL, realSections: [] }),
    competitions: async () => index.competitions(),
    seasonMatches: async (seasonId, filter) =>
      seasonId === data.season.season.id ? index.matchList(filter) : null,
    match: async (matchId) => {
      const match = index.matchById.get(matchId);
      return match ? index.detail(match) : null;
    },
    matchComments: async (matchId) => index.matchById.has(matchId) ? {
      matchId, availability: "not_collected", capturedAt: null, responseSha256: null,
      lastAttempt: { availability: "not_collected", occurredAt: null, httpStatus: null, errorCode: null }, comments: [],
    } : null,
    enrichedComments: async () => null,
    seasonTeams: async (seasonId) =>
      seasonId === data.season.season.id ? index.standings() : null,
    teamSeason: async (seasonId, teamId) =>
      seasonId === data.season.season.id && index.teams.has(teamId)
        ? index.teamSeason(teamId)
        : null,
    player: async (playerId, seasonId) =>
      seasonId === data.season.season.id && index.squadOf.has(playerId)
        ? index.playerSeason(playerId)
        : null,
    observedPlayers: async (teamId) =>
      index.teams.has(teamId) ? index.observedPlayers(teamId) : null,
    teamIdentity: async () => null,
    // Synthetic players are reached through their season (`player`), never as a real profile.
    playerProfile: async () => null,
    playerObservedMatches: async () => null,
    teamObservedMatches: async () => null,
    teamObservedPerformance: async () => null,
    matchObservedDetail: async () => null,
    observedSquad: async () => null,
  };
}

let loaded: DataSource | null = null;

/** The demonstration source (`DATA_MODE=demo`): invented season read lazily from `data/synthetic`. */
export const syntheticSource: DataSource = {
  info: () => ({ synthetic: true, label: DEMO_LABEL, realSections: [] }),
  competitions: () => lazy().competitions(),
  seasonMatches: (seasonId, filter) => lazy().seasonMatches(seasonId, filter),
  match: (matchId) => lazy().match(matchId),
  matchComments: (matchId) => lazy().matchComments(matchId),
  enrichedComments: async () => null,
  seasonTeams: (seasonId) => lazy().seasonTeams(seasonId),
  teamSeason: (seasonId, teamId) => lazy().teamSeason(seasonId, teamId),
  player: (playerId, seasonId) => lazy().player(playerId, seasonId),
  observedPlayers: (teamId) => lazy().observedPlayers(teamId),
  teamIdentity: async () => null,
  observedSquad: async () => null,
  playerProfile: async () => null,
  playerObservedMatches: async () => null,
  teamObservedMatches: async () => null,
  teamObservedPerformance: async () => null,
  matchObservedDetail: async () => null,
};

function lazy(): DataSource {
  loaded ??= createSyntheticSource(loadSyntheticDataset());
  return loaded;
}

// ─── Raw → contract ─────────────────────────────────────────────────────────

export function toStat(raw: RawStat): Stat {
  return typeof raw === "number" ? { value: raw } : { value: null, reason: raw };
}

function passAccuracy(raw: RawPlayerStats): Stat {
  const passes = toStat(raw.passes);
  const completed = toStat(raw.passesCompleted);
  if (passes.value === null) return passes;
  if (completed.value === null) return completed;
  if (passes.value === 0) return { value: null, reason: "not_available" };
  return { value: Math.round((completed.value / passes.value) * 1000) / 10 };
}

function toPlayerStats(raw: RawPlayerStats): PlayerMatchStats {
  const stats = {} as PlayerMatchStats;
  for (const key of PLAYER_STAT_KEYS) {
    stats[key] = key === "passAccuracy" ? passAccuracy(raw) : toStat(raw[key]);
  }
  return stats;
}

function toTeamStats(raw: RawTeamStats): TeamMatchStats {
  const stats = {} as TeamMatchStats;
  for (const key of TEAM_STAT_KEYS) stats[key] = toStat(raw[key]);
  return stats;
}

function buildIndex(data: RawDataset) {
  const { competition, season: rawSeason } = data.season;
  const teams = new Map(data.season.teams.map((team) => [team.id, team]));
  const players = new Map(data.players.map((player) => [player.id, player]));
  const squadOf = new Map<string, { teamId: string; shirtNumber: number | null }>();
  for (const squad of data.season.squads) {
    for (const member of squad.players) {
      squadOf.set(member.playerId, { teamId: squad.teamId, shirtNumber: member.shirtNumber });
    }
  }
  const matches = data.rounds
    .flatMap((round) => round.matches)
    .sort(
      (a, b) =>
        a.round - b.round ||
        (a.kickoff ?? "~").localeCompare(b.kickoff ?? "~") ||
        a.id.localeCompare(b.id),
    );
  const matchById = new Map(matches.map((match) => [match.id, match]));
  const finished = matches.filter((match) => match.status === "finished");

  const teamRef = (id: string): TeamRef => {
    const { name, shortName, crestUrl, colors } = teams.get(id)!;
    return { id, name, shortName, crestUrl, colors };
  };
  const playerRef = (id: string): PlayerRef => {
    const { name, position } = players.get(id)!;
    return { id, name, position };
  };

  const season = (): Season => ({
    ...rawSeason,
    currentRound: finished.length > 0 ? Math.max(...finished.map((match) => match.round)) : null,
  });

  const summary = (match: RawMatch): MatchSummary => ({
    id: match.id,
    seasonId: rawSeason.id,
    round: match.round,
    kickoff: match.kickoff,
    status: match.status,
    home: teamRef(match.homeTeamId),
    away: teamRef(match.awayTeamId),
    score: match.score,
  });

  const lineup = (raw: RawLineup): Lineup => {
    const entry = ({ playerId, shirtNumber, slot }: RawLineup["starters"][number]) => ({
      player: playerRef(playerId),
      shirtNumber,
      slot: slot ? { line: slot[0], order: slot[1] } : null,
    });
    return {
      formation: raw.formation,
      starters: raw.starters.map(entry),
      bench: raw.bench.map(entry),
    };
  };

  const event = (match: RawMatch, raw: RawEvent): MatchEvent => {
    const base = {
      id: raw.id,
      matchId: match.id,
      teamId: raw.teamId,
      minute: raw.minute,
      addedTime: raw.addedTime,
      clip: raw.clip,
    };
    switch (raw.type) {
      case "goal":
        return {
          ...base,
          type: "goal",
          player: playerRef(raw.playerId),
          assist: raw.assistId ? playerRef(raw.assistId) : null,
          penalty: raw.penalty,
          ownGoal: raw.ownGoal,
        };
      case "card":
        return { ...base, type: "card", player: playerRef(raw.playerId), card: raw.card };
      case "substitution":
        return {
          ...base,
          type: "substitution",
          playerIn: playerRef(raw.playerInId),
          playerOut: playerRef(raw.playerOutId),
        };
    }
  };

  const detail = (match: RawMatch): MatchDetail => ({
    ...summary(match),
    lineups: match.lineups && {
      home: lineup(match.lineups.home),
      away: lineup(match.lineups.away),
    },
    events: match.events.map((raw) => event(match, raw)),
    teamStats: match.teamStats && {
      home: toTeamStats(match.teamStats.home),
      away: toTeamStats(match.teamStats.away),
    },
    playerStats: Object.fromEntries(
      Object.entries(match.playerStats).map(([id, raw]) => [id, toPlayerStats(raw)]),
    ),
  });

  const sideOf = (match: RawMatch, teamId: string): Side =>
    match.homeTeamId === teamId ? "home" : "away";
  const involves = (match: RawMatch, teamId: string) =>
    match.homeTeamId === teamId || match.awayTeamId === teamId;

  const record = (teamId: string): TeamRecord => {
    const r: TeamRecord = {
      played: 0,
      wins: 0,
      draws: 0,
      losses: 0,
      goalsFor: 0,
      goalsAgainst: 0,
      points: 0,
    };
    for (const match of finished) {
      if (!involves(match, teamId) || !match.score) continue;
      const home = sideOf(match, teamId) === "home";
      const scored = home ? match.score.home : match.score.away;
      const conceded = home ? match.score.away : match.score.home;
      r.played++;
      r.goalsFor += scored;
      r.goalsAgainst += conceded;
      if (scored > conceded) r.wins++;
      else if (scored === conceded) r.draws++;
      else r.losses++;
    }
    r.points = r.wins * 3 + r.draws;
    return r;
  };

  const standings = (): TeamStanding[] =>
    [...teams.keys()]
      .map((id) => ({ team: teamRef(id), record: record(id) }))
      .sort(
        (a, b) =>
          b.record.points - a.record.points ||
          b.record.goalsFor - b.record.goalsAgainst - (a.record.goalsFor - a.record.goalsAgainst) ||
          b.record.goalsFor - a.record.goalsFor ||
          a.team.name.localeCompare(b.team.name, "es"),
      )
      .map((row, i) => ({ rank: i + 1, ...row }));

  const teamMatch = (match: RawMatch, teamId: string): TeamMatch => {
    const side = sideOf(match, teamId);
    let result: TeamMatch["result"] = null;
    if (match.status === "finished" && match.score) {
      const diff =
        side === "home" ? match.score.home - match.score.away : match.score.away - match.score.home;
      result = diff > 0 ? "win" : diff === 0 ? "draw" : "loss";
    }
    const other = side === "home" ? "away" : "home";
    return {
      match: summary(match),
      side,
      opponent: teamRef(side === "home" ? match.awayTeamId : match.homeTeamId),
      result,
      teamStats:
        match.status === "finished" && match.teamStats
          ? {
              own: toTeamStats(match.teamStats[side]),
              opponent: toTeamStats(match.teamStats[other]),
            }
          : null,
    };
  };

  /** Season figures of a player from the matches they took part in. */
  const playerSeasonStats = (playerId: string): PlayerSeasonStats => {
    const played = finished.filter((match) => match.playerStats[playerId]);
    const rows = played.map((match) => toPlayerStats(match.playerStats[playerId]));
    const stats = {
      appearances: seasonStat(rows.map(() => ({ value: 1 }))),
      starts: seasonStat(
        played.map((match) => ({
          value: Object.values(match.lineups ?? {}).some((l) =>
            l.starters.some((s) => s.playerId === playerId),
          )
            ? 1
            : 0,
        })),
      ),
    } as PlayerSeasonStats;
    for (const key of PLAYER_STAT_KEYS) {
      const accumulation: Accumulation =
        key === "passAccuracy"
          ? { kind: "weighted", weights: rows.map((row) => row.passes) }
          : { kind: "sum" };
      stats[key] = seasonStat(
        rows.map((row) => row[key]),
        accumulation,
      );
    }
    return stats;
  };

  const teamSeason = (teamId: string): TeamSeason => {
    const played = finished.filter((match) => involves(match, teamId) && match.teamStats);
    const rows = played.map((match) => toTeamStats(match.teamStats![sideOf(match, teamId)]));
    const stats = {} as TeamSeason["stats"];
    for (const key of TEAM_STAT_KEYS) {
      stats[key] = seasonStat(
        rows.map((row) => row[key]),
        key === "possession" ? { kind: "mean" } : { kind: "sum" },
      );
    }
    const squad = data.season.squads.find((s) => s.teamId === teamId)?.players ?? [];
    return {
      season: season(),
      team: teamRef(teamId),
      standing: standings().find((row) => row.team.id === teamId)!,
      matches: matches
        .filter((match) => involves(match, teamId))
        .map((match) => teamMatch(match, teamId)),
      stats,
      squad: squad.map(({ playerId, shirtNumber }) => ({
        player: playerRef(playerId),
        shirtNumber,
        stats: playerSeasonStats(playerId),
      })),
    };
  };

  const playerSeason = (playerId: string): PlayerSeason => {
    const { teamId, shirtNumber } = squadOf.get(playerId)!;
    const names = (e: RawEvent) =>
      e.type === "substitution"
        ? e.playerInId === playerId || e.playerOutId === playerId
        : e.playerId === playerId || (e.type === "goal" && e.assistId === playerId);
    const { name, position, birthDate, heightCm, preferredFoot } = players.get(playerId)!;
    return {
      season: season(),
      player: { id: playerId, name, position, birthDate, heightCm, preferredFoot },
      team: teamRef(teamId),
      shirtNumber,
      stats: playerSeasonStats(playerId),
      matches: finished
        .filter((match) => match.playerStats[playerId])
        .map((match): PlayerMatch => {
          const side = sideOf(match, teamId);
          return {
            match: summary(match),
            side,
            started: match.lineups![side].starters.some((s) => s.playerId === playerId),
            stats: toPlayerStats(match.playerStats[playerId]),
            events: match.events.filter(names).map((raw) => event(match, raw)),
          };
        }),
    };
  };

  const matchList = (filter: MatchFilter): MatchSummary[] =>
    matches
      .filter((match) => filter.round === undefined || match.round === filter.round)
      .filter((match) => filter.teamId === undefined || involves(match, filter.teamId))
      .map(summary);

  const competitions = (): Competition[] => [{ ...competition, seasons: [season()] }];

  /** The synthetic squad, the only players the invented season knows about a team. */
  const observedPlayers = (teamId: string): ObservedPlayers => ({
    teamId,
    players: (data.season.squads.find((s) => s.teamId === teamId)?.players ?? []).map(
      ({ playerId }) => playerRef(playerId),
    ),
    scope: "Plantilla de la temporada sintética.",
    source: { synthetic: true, label: DEMO_LABEL },
  });

  return {
    teams,
    squadOf,
    matchById,
    competitions,
    matchList,
    detail,
    standings,
    teamSeason,
    playerSeason,
    observedPlayers,
  };
}
