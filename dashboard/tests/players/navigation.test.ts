import { describe, expect, it } from "vitest";
import {
  homeHref, homeReturnHref, matchHref, matchesHref, matchesInitialState, observedMatchHref, observedMatchListReturnHref, observedMatchReturnHref, observedMatchTeamsReturnHref, playerHref, playerMatchHref, relatedObservedMatchHref,
  playerInitialView, playerProfileHref, playerReturnHref, profileInitialMatch, profileOrigin, teamsHref, teamsInitialView,
} from "@/features/players/navigation";

describe("«Partidos» as the origin of a real match", () => {
  it("keeps both list filters through a profile and a match opened from that profile", () => {
    const matches = { kind: "matches", teamId: "all", state: "finished" } as const;
    const origin = { kind: "observedMatch", id: "m1", tab: "lineups", matches } as const;
    const profile = playerProfileHref("p1", null, origin);
    const query = queryOf(profile);
    expect(profileOrigin(query)).toEqual(origin);
    const back = playerReturnHref(null, query);
    expect(back).toBe(`${observedMatchHref("m1", matches)}&tab=lineups`);
    expect(observedMatchListReturnHref(queryOf(back))).toBe("/matches?team=all&state=finished");
    const match = observedMatchHref("m2", { playerId: "p1", seasonId: null, origin: profileOrigin(query) });
    expect(observedMatchReturnHref("m2", queryOf(match))).toBe(`${profile}&match=m2`);
  });

  it("keeps the team and status the list had, leaving out the club and «Todos»", () => {
    expect(matchesHref({})).toBe("/matches");
    expect(matchesHref({ teamId: "team-real-jaen", state: "all" })).toBe("/matches");
    expect(matchesHref({ teamId: "all", state: "pending" })).toBe("/matches?team=all&state=pending");
    const href = observedMatchHref("sofascore-match-1", { kind: "matches", teamId: "sofascore-team-24326", state: "finished" });
    expect(href).toBe("/matches/sofascore-match-1?from=matches&team=sofascore-team-24326&state=finished");
    expect(observedMatchListReturnHref(queryOf(href))).toBe("/matches?team=sofascore-team-24326&state=finished");
    expect(observedMatchListReturnHref(queryOf(observedMatchHref("sofascore-match-1", { kind: "matches", teamId: "team-real-jaen", state: "all" }))))
      .toBe("/matches");
    // A related match keeps it; other origins are not one.
    expect(relatedObservedMatchHref("sofascore-match-2", queryOf(href)))
      .toBe("/matches/sofascore-match-2?from=matches&team=sofascore-team-24326&state=finished");
    expect(observedMatchTeamsReturnHref(queryOf(href))).toBeNull();
    expect(homeReturnHref(queryOf(href))).toBeNull();
  });

  it("returns to plain «Partidos» without a context, and drops what it did not build", () => {
    expect(observedMatchListReturnHref(queryOf(observedMatchHref("sofascore-match-1")))).toBeNull();
    expect(observedMatchListReturnHref({ from: "matches", team: "//example.com", state: "otro" })).toBe("/matches");
    expect(matchesInitialState({ state: "pending" })).toBe("pending");
    expect(matchesInitialState({ state: ["finished"] })).toBe("all");
  });
});

describe("«Inicio» as the origin of a match", () => {
  it("returns from a synthetic match to the season and target it was opened from", () => {
    const href = matchHref("played-10", { kind: "home", seasonId: "s1", matchId: "target" });
    expect(href).toBe("/matches/played-10?from=home&season=s1&match=target");
    expect(homeReturnHref(queryOf(href))).toBe("/?season=s1&match=target");
    expect(matchHref("played-10")).toBe("/matches/played-10");
    expect(homeReturnHref(queryOf(matchHref("played-10")))).toBeNull();
  });

  it("returns from a real match to its target, with no season (a real Inicio has none), and keeps it in a related match", () => {
    const href = observedMatchHref("sofascore-match-16599934", { kind: "home", seasonId: null, matchId: "sofascore-match-16599973" });
    expect(href).toBe("/matches/sofascore-match-16599934?from=home&match=sofascore-match-16599973");
    expect(homeReturnHref(queryOf(href))).toBe("/?match=sofascore-match-16599973");
    expect(observedMatchTeamsReturnHref(queryOf(href))).toBeNull();
    expect(observedMatchReturnHref("sofascore-match-16599934", queryOf(href))).toBeNull();
    expect(relatedObservedMatchHref("sofascore-match-1", queryOf(href)))
      .toBe("/matches/sofascore-match-1?from=home&match=sofascore-match-16599973");
  });

  it("leaves the origins of Equipos and of a player as they were, and drops what it did not build", () => {
    const teams = queryOf(observedMatchHref("sofascore-match-1", { kind: "teams", seasonId: null, teamId: "sofascore-team-24329", tab: "performance" }));
    expect(homeReturnHref(teams)).toBeNull();
    expect(observedMatchTeamsReturnHref(teams)).toBe("/teams?team=sofascore-team-24329");
    expect(homeReturnHref({ from: "home", season: "//example.com", match: "../x" })).toBe("/");
    expect(homeHref({})).toBe("/");
  });

  it("keeps the origin from a legacy link that still contains view=observed", () => {
    const legacy = queryOf("/matches/sofascore-match-16599970?view=observed&from=home&match=sofascore-match-16600011");
    expect(homeReturnHref(legacy)).toBe("/?match=sofascore-match-16600011");
    expect(relatedObservedMatchHref("sofascore-match-16600012", legacy))
      .toBe("/matches/sofascore-match-16600012?from=home&match=sofascore-match-16600011");
  });
});

describe("teamsHref", () => {
  it("keeps season, team and the players tab; the default tab and missing values are left out", () => {
    expect(teamsHref({ seasonId: "season-2", teamId: "team-3", tab: "players" })).toBe("/teams?season=season-2&team=team-3&tab=players");
    expect(teamsHref({ seasonId: "season-2", teamId: "sofascore-team-24368", tab: "performance" }))
      .toBe("/teams?season=season-2&team=sofascore-team-24368");
    expect(teamsHref({})).toBe("/teams");
  });
  it("reads back what it writes; anything else stays unset or the default tab", () => {
    const query = queryOf(teamsHref({ seasonId: "season-2", teamId: "sofascore-team-24368", tab: "players" }));
    expect(teamsInitialView(query)).toEqual({ initialTeamId: "sofascore-team-24368", initialSeasonId: "season-2", initialTab: "players" });
    expect(teamsInitialView({ team: ["a", "b"], season: "", tab: "otra" }))
      .toEqual({ initialTeamId: undefined, initialSeasonId: undefined, initialTab: "performance" });
  });
});

function queryOf(href: string) {
  return Object.fromEntries(new URL(href, "http://localhost").searchParams);
}

describe("Player navigation context", () => {
  it("returns to the same team, season and players tab without browser history", () => {
    const href = playerHref("player-1", "season-2", { kind: "team", id: "team-3" });
    const query = queryOf(href);
    expect(href).toBe("/players/player-1?season=season-2&from=team&origin=team-3");
    expect(playerInitialView(query)).toEqual({ tab: "season", matchId: null });
    expect(playerReturnHref(query.season, query)).toBe("/teams?season=season-2&team=team-3&tab=players");
  });
  it.each(["lineups", "timeline"] as const)("opens Partidos on the origin match from %s and returns to that tab", tab => {
    const href = playerHref("player-1", "season-2", { kind: "match", id: "match-3", tab });
    const query = queryOf(href);
    expect(href).toBe(`/players/player-1?season=season-2&tab=matches&match=match-3&from=match&origin=match-3&originTab=${tab}`);
    expect(playerInitialView(query)).toEqual({ tab: "matches", matchId: "match-3" });
    expect(playerReturnHref(query.season, query)).toBe(`/matches/match-3?tab=${tab}`);
  });
  it("opens a season player match with a shareable return to the selected profile context", () => {
    const href = playerMatchHref("match-9", { playerId: "player-1", seasonId: "season-2", origin: { kind: "team", id: "team-3" } });
    expect(href).toBe("/matches/match-9?from=player&player=player-1&season=season-2&playerFrom=team&playerOrigin=team-3");
    const back = observedMatchReturnHref("match-9", queryOf(href))!;
    expect(back).toBe("/players/player-1?view=profile&season=season-2&from=team&origin=team-3&match=match-9&tab=matches");
    expect(playerInitialView(queryOf(back))).toEqual({ tab: "matches", matchId: "match-9" });
    expect(playerReturnHref("season-2", queryOf(back))).toBe("/teams?season=season-2&team=team-3&tab=players");
  });
  it("opens Temporada for direct access without parameters", () => {
    expect(playerHref("player-1", "season-2")).toBe("/players/player-1?season=season-2");
    expect(playerInitialView({})).toEqual({ tab: "season", matchId: null });
  });
  it.each([
    [{ tab: "unknown", match: "match-3" }, { tab: "season", matchId: null }],
    [{ tab: "lineups" }, { tab: "season", matchId: null }],
    [{ match: "match-3" }, { tab: "season", matchId: null }],
    [{ tab: ["matches", "season"] }, { tab: "season", matchId: null }],
    [{ tab: "matches" }, { tab: "matches", matchId: null }],
    [{ tab: "matches", match: "" }, { tab: "matches", matchId: null }],
    [{ tab: "matches", match: ["a", "b"] }, { tab: "matches", matchId: null }],
    [{ tab: "matches", match: "unknown-match" }, { tab: "matches", matchId: "unknown-match" }],
  ])("resolves the initial view deterministically for %j", (query, expected) => {
    expect(playerInitialView(query)).toEqual(expected);
  });
  it("returns to the origin match without a section when the origin tab is unknown", () => {
    expect(playerReturnHref("season-2", { from: "match", origin: "match-3", originTab: "summary" })).toBe("/matches/match-3");
  });
  it.each([{}, { from: "unknown", origin: "match-1" }, { from: "match", origin: "https://example.com" },
    { from: "team", origin: "//example.com" }, { from: "match", origin: "../teams" },
    { from: "match", origin: "https://example.com", originTab: "lineups" }])("uses a deterministic internal fallback for %j", query => {
    expect(playerReturnHref("season-2", query)).toBe("/teams?season=season-2&tab=players");
  });

  it("opens a real profile without tying it to the season, which only restores the return", () => {
    const href = playerProfileHref("sofascore-player-7", "season-2", { kind: "team", id: "team-3" });
    expect(href).toBe("/players/sofascore-player-7?view=profile&season=season-2&from=team&origin=team-3");
    const query = queryOf(href);
    expect(playerReturnHref(query.season, query)).toBe("/teams?season=season-2&team=team-3&tab=players");
    expect(playerProfileHref("p", null, { kind: "team", id: "t" })).toBe("/players/p?view=profile&from=team&origin=t");
  });

  it("returns from a profile opened in a real match without adding a data-mode parameter", () => {
    const href = playerProfileHref("sofascore-player-7", null, { kind: "observedMatch", id: "sofascore-match-3" });
    expect(href).toBe("/players/sofascore-player-7?view=profile&from=observedMatch&origin=sofascore-match-3");
    expect(playerReturnHref(null, queryOf(href))).toBe("/matches/sofascore-match-3");
  });

  it("returns to the line-ups tab of a real match when the profile was opened there", () => {
    const href = playerProfileHref("sofascore-player-7", null, { kind: "observedMatch", id: "sofascore-match-3", tab: "lineups" });
    expect(href).toBe("/players/sofascore-player-7?view=profile&from=observedMatch&origin=sofascore-match-3&originTab=lineups");
    expect(playerReturnHref(null, queryOf(href))).toBe("/matches/sofascore-match-3?tab=lineups");
  });

  it("opens a real match from the list without any return context", () => {
    expect(observedMatchHref("sofascore-match-3")).toBe("/matches/sofascore-match-3");
    expect(observedMatchReturnHref("sofascore-match-3", queryOf(observedMatchHref("sofascore-match-3")))).toBeNull();
    expect(observedMatchTeamsReturnHref(queryOf(observedMatchHref("sofascore-match-3")))).toBeNull();
  });

  it.each([
    [{ seasonId: "sofascore-scope-94191-97382", teamId: "sofascore-team-24265", tab: "players" as const },
      "/teams?season=sofascore-scope-94191-97382&team=sofascore-team-24265&tab=players"],
    [{ seasonId: null, teamId: "sofascore-team-24368", tab: "performance" as const }, "/teams?team=sofascore-team-24368"],
    [{ seasonId: null, teamId: null, tab: "performance" as const }, "/teams"],
  ])("returns from a real match to the «Equipos» view it was opened from (%o)", (view, teams) => {
    const href = observedMatchHref("sofascore-match-16599788", { kind: "teams", ...view });
    expect(observedMatchTeamsReturnHref(queryOf(href))).toBe(teams);
    expect(observedMatchReturnHref("sofascore-match-16599788", queryOf(href))).toBeNull();
  });

  it("opens a related real match with the same origin, so «Volver» still leads there", () => {
    const fromTeams = queryOf(observedMatchHref("sofascore-match-16599956", { kind: "teams", seasonId: null, teamId: "sofascore-team-24329", tab: "performance" }));
    expect(relatedObservedMatchHref("sofascore-match-17118209", fromTeams)).toBe("/matches/sofascore-match-17118209?from=teams&team=sofascore-team-24329");
    const fromPlayer = queryOf(observedMatchHref("sofascore-match-1", { playerId: "sofascore-player-7", seasonId: "s", origin: { kind: "team", id: "team-3" } }));
    expect(relatedObservedMatchHref("sofascore-match-2", fromPlayer))
      .toBe(observedMatchHref("sofascore-match-2", { playerId: "sofascore-player-7", seasonId: "s", origin: { kind: "team", id: "team-3" } }));
    expect(relatedObservedMatchHref("sofascore-match-2", {})).toBe("/matches/sofascore-match-2");
    expect(relatedObservedMatchHref("sofascore-match-2", { from: "teams", team: "//example.com" })).toBe("/matches/sofascore-match-2?from=teams");
  });

  it("drops an «Equipos» origin it did not build", () => {
    expect(observedMatchTeamsReturnHref({ from: "teams", team: "//example.com", season: "../x", tab: "other" })).toBe("/teams");
    expect(observedMatchTeamsReturnHref({ from: "player", team: "sofascore-team-24265" })).toBeNull();
  });

  it.each([
    ["a team", "/players/sofascore-player-7?view=profile&season=season-2&from=team&origin=team-3"],
    ["a real match's line-ups", "/players/sofascore-player-7?view=profile&from=observedMatch&origin=sofascore-match-1&originTab=lineups"],
    ["a real match", "/players/sofascore-player-7?view=profile&from=observedMatch&origin=sofascore-match-1"],
    ["nowhere", "/players/sofascore-player-7?view=profile"],
  ])("returns from a real match to the profile opened from %s, with that match selected and its own return", (_, profile) => {
    const query = queryOf(profile);
    const seasonId = query.season ?? null;
    const href = observedMatchHref("sofascore-match-9", { playerId: "sofascore-player-7", seasonId, origin: profileOrigin(query) });
    expect(href.startsWith("/matches/sofascore-match-9?from=player&player=sofascore-player-7")).toBe(true);
    const back = observedMatchReturnHref("sofascore-match-9", queryOf(href));
    expect(back).toBe(`${profile}&match=sofascore-match-9`);
    expect(profileInitialMatch(queryOf(back!))).toBe("sofascore-match-9");
    expect(playerReturnHref(seasonId, queryOf(back!))).toBe(playerReturnHref(seasonId, query));
  });

  it("does not grow when going back and forth between a profile and its matches", () => {
    let profile = playerProfileHref("sofascore-player-7", "season-2", { kind: "observedMatch", id: "sofascore-match-1", tab: "lineups" }, "sofascore-match-2");
    const first = profile;
    for (let i = 0; i < 5; i++) {
      const query = queryOf(profile);
      const match = observedMatchHref("sofascore-match-2", { playerId: "sofascore-player-7", seasonId: query.season, origin: profileOrigin(query) });
      // A profile opened from that match's line-ups returns to the match only.
      const lineupProfile = playerProfileHref("sofascore-player-8", null, { kind: "observedMatch", id: "sofascore-match-1", tab: "lineups" });
      expect(playerReturnHref(null, queryOf(lineupProfile))).toBe("/matches/sofascore-match-1?tab=lineups");
      profile = observedMatchReturnHref("sofascore-match-2", queryOf(match))!;
    }
    expect(profile).toBe(first);
  });

  it.each([
    {},
    { from: "team", player: "sofascore-player-7" },
    { from: "player" },
    { from: "player", player: "" },
    { from: "player", player: "https://example.com" },
    { from: "player", player: "//example.com" },
    { from: "player", player: "../teams" },
    { from: "player", player: ["sofascore-player-7", "sofascore-player-8"] },
    { from: ["player", "player"], player: "sofascore-player-7" },
  ])("returns from a real match to the match list for %j", query => {
    expect(observedMatchReturnHref("sofascore-match-1", query)).toBeNull();
  });

  it.each([
    [{ playerFrom: "team", playerOrigin: "//example.com" }, "/players/sofascore-player-7?view=profile"],
    [{ playerFrom: "match", playerOrigin: "match-3" }, "/players/sofascore-player-7?view=profile"],
    [{ playerFrom: "observedMatch", playerOrigin: "sofascore-match-1", playerOriginTab: "timeline" },
      "/players/sofascore-player-7?view=profile&from=observedMatch&origin=sofascore-match-1"],
    [{ season: "../x", playerFrom: "team", playerOrigin: "team-3" }, "/players/sofascore-player-7?view=profile&from=team&origin=team-3"],
  ])("drops a tampered profile context, keeping the player, for %j", (extra, expected) => {
    expect(observedMatchReturnHref("sofascore-match-1", { from: "player", player: "sofascore-player-7", ...extra }))
      .toBe(`${expected}&match=sofascore-match-1`);
  });

  it.each([
    [{}, null],
    [{ match: "sofascore-match-4" }, "sofascore-match-4"],
    [{ from: "observedMatch", origin: "sofascore-match-1" }, "sofascore-match-1"],
    [{ match: "sofascore-match-4", from: "observedMatch", origin: "sofascore-match-1" }, "sofascore-match-4"],
    [{ from: "team", origin: "team-real-jaen" }, null],
    [{ match: "//example.com", from: "observedMatch", origin: "sofascore-match-1" }, "sofascore-match-1"],
    [{ match: ["sofascore-match-4", "sofascore-match-5"] }, null],
    [{ match: "" }, null],
  ])("selects the real match from the URL or the line-ups that opened the profile for %j", (query, expected) => {
    expect(profileInitialMatch(query)).toBe(expected);
  });

  it("keeps the selected match in a real profile link, after its origin", () => {
    expect(playerProfileHref("sofascore-player-7", null, null, "sofascore-match-4")).toBe("/players/sofascore-player-7?view=profile&match=sofascore-match-4");
  });
});
