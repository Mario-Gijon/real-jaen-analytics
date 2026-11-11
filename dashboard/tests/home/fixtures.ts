import type { MatchSummary, Season, TeamMatch, TeamMatchStats, TeamRef, TeamSeason } from "@/contract";
import { TEAM_STAT_KEYS, seasonStat } from "@/contract";

export const NOW = Date.parse("2026-10-01T12:00:00Z");
export const season: Season = { id: "s1", competitionId: "c1", name: "2026/27", rounds: 38, currentRound: 10 };
export const club: TeamRef = { id: "team-real-jaen", name: "Real Jaén CF", shortName: "Real Jaén", crestUrl: null,
  colors: { primary: "#4b2677", secondary: "#ffffff" } };
export const rival: TeamRef = { ...club, id: "rival", name: "CD Rival", shortName: "Rival" };
export const other: TeamRef = { ...club, id: "other", name: "UD Otro Rival", shortName: "Otro Rival" };
export const target: MatchSummary = { id: "target", seasonId: season.id, round: 11,
  home: club, away: rival, kickoff: "2026-11-01T11:00:00Z", status: "scheduled", score: null };
export const second: MatchSummary = { ...target, id: "second", round: 12, home: other, away: club,
  kickoff: "2026-11-08T11:00:00Z" };
export const stats: TeamMatchStats = Object.fromEntries(TEAM_STAT_KEYS.map(key => [key, { value: 0 }])) as TeamMatchStats;
export function played(round: number): TeamMatch {
  return { match: { ...target, id: `played-${round}`, round, kickoff: `2026-09-${String(round).padStart(2, "0")}T11:00:00Z`,
    home: rival, away: other, status: "finished", score: { home: 2, away: 1 } },
    side: "home", opponent: other, result: "win", teamStats: { own: { ...stats, xg: { value: 1.5 } }, opponent: { ...stats, xg: { value: 0.5 } } } };
}
export const history = Array.from({ length: 10 }, (_, i) => played(i + 1));
export const teamSeason: TeamSeason = { team: rival, season,
  standing: { rank: 1, team: rival, record: { played: 10, wins: 10, draws: 0, losses: 0, goalsFor: 20, goalsAgainst: 10, points: 30 } },
  matches: history, squad: [], stats: Object.fromEntries(TEAM_STAT_KEYS.map(key => [key, seasonStat([{ value: 0 }])])) as TeamSeason["stats"] };
