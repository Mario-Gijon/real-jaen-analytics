// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createLocalApiObservedPlayers,
  createLocalApiObservedSquad,
  clearTeamNameCache,
  createLocalApiObservedMatches,
  createLocalApiPlayerProfile,
  createLocalApiMatchDetail,
  createLocalApiTeamMatches,
  createLocalApiTeamIdentity,
  createLocalApiTeamPerformance,
  sourceMatchId,
  sourcePlayerId,
  UpstreamUnavailableError,
} from "@/server/local-api/local-api-source";

/**
 * `GET /api/v1/scope` of the local API: this version serves Primera Federación,
 * Group 2 (tournament 94191) in season 26/27 (97382), as declared on 2026-10-22.
 */
const SCOPE_BODY = { data: { competitions: [
  { name: "Primera Federación, Grupo 2", season: "26/27", unique_tournament_id: 17073, tournament_id: 94191, season_id: 97382 },
], teams_with_own_export: [{ team_id: 2841, name: "Real Jaén", exports: [] }] } };
const SCOPE_URL = "http://127.0.0.1:8001/api/v1/scope";
/** The scope's answer for its URL, `null` for any other. */
const scoped = (input: RequestInfo | URL) => String(input).endsWith("/api/v1/scope") ? new Response(JSON.stringify(SCOPE_BODY)) : null;

/** Simulated local API: these tests never reach the real service. */
function answer(body: unknown, status = 200) {
  return vi.fn(async (input: RequestInfo | URL) =>
    scoped(input) ?? new Response(typeof body === "string" ? body : JSON.stringify(body), { status }),
  );
}

const SCOPE = "Jugadores observados en convocatorias; no es una plantilla completa ni necesariamente actual.";
const payload = (data: unknown[], count: unknown = data.length) => ({
  data,
  meta: { count, scope: SCOPE },
});
const source = (fetch: typeof globalThis.fetch, timeoutMs?: number) =>
  createLocalApiObservedPlayers({ baseUrl: "http://127.0.0.1:8001/", fetch, timeoutMs });

beforeEach(() => clearTeamNameCache());
afterEach(() => vi.restoreAllMocks());

describe("local API adapter: public team identity", () => {
  it("resolves a team with an explicit correspondence but no assumption about its statistics export", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ data: { team_id: 135672, name: "CE Europa" } })));
    const identity = createLocalApiTeamIdentity({ baseUrl: "http://127.0.0.1:8001", fetch });
    expect(await identity("sofascore-team-135672")).toEqual({ id: "sofascore-team-135672", name: "CE Europa" });
    expect(fetch).toHaveBeenCalledWith("http://127.0.0.1:8001/api/v1/teams/135672", expect.anything());
  });

  it("does not infer or request identities for unknown id formats or unnamed records", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ data: { team_id: 2841, name: null } })));
    const identity = createLocalApiTeamIdentity({ baseUrl: "http://127.0.0.1:8001", fetch });
    expect(await identity("team-guess-by-name")).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
    expect(await identity("team-real-jaen")).toBeNull();
  });
});

describe("local API adapter: observed players", () => {
  it("asks for the source id of our team and returns contract players with derived ids", async () => {
    const fetch = answer(
      payload([
        { player_id: 966303, name: "Moha Keita", usual_position: "M" },
        { player_id: 1, name: " Portero Uno ", usual_position: "G" },
        { player_id: 2, name: "Defensa", usual_position: "D" },
        { player_id: 3, name: "Delantero", usual_position: "F" },
      ]),
    );
    expect(await source(fetch)("team-real-jaen")).toEqual({
      teamId: "team-real-jaen",
      players: [
        { id: "sofascore-player-966303", name: "Moha Keita", position: "midfielder" },
        { id: "sofascore-player-1", name: "Portero Uno", position: "goalkeeper" },
        { id: "sofascore-player-2", name: "Defensa", position: "defender" },
        { id: "sofascore-player-3", name: "Delantero", position: "forward" },
      ],
      scope: SCOPE,
      source: { synthetic: false, label: "Datos reales" },
    });
    expect(fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:8001/api/v1/teams/2841/players",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("keeps an unknown or missing position as unknown, never guessed", async () => {
    const fetch = answer(
      payload([
        { player_id: 1, name: "A", usual_position: "X" },
        { player_id: 2, name: "B", usual_position: null },
        { player_id: 3, name: "C" },
      ]),
    );
    const result = await source(fetch)("team-real-jaen");
    expect(result!.players.map((p) => p.position)).toEqual([null, null, null]);
  });

  it("has nothing for a team without explicit correspondence, and does not call the API", async () => {
    const fetch = answer(payload([]));
    expect(await source(fetch)("team-alto")).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("has nothing when the source does not know the team (404)", async () => {
    expect(await source(answer({ detail: "Not found" }, 404))("team-real-jaen")).toBeNull();
  });

  it.each([
    ["no data array", { meta: { scope: SCOPE } }],
    ["no scope", { data: [], meta: { count: 0 } }],
    ["a row without id", payload([{ name: "A", usual_position: "M" }])],
    ["a non-integer id", payload([{ player_id: "966303", name: "A" }])],
    ["a row without name", payload([{ player_id: 1, name: " " }])],
    ["a repeated id", payload([{ player_id: 1, name: "A" }, { player_id: 1, name: "B" }])],
    ["a non-text position", payload([{ player_id: 1, name: "A", usual_position: 3 }])],
  ])("rejects a response with %s", async (_case, body) => {
    await expect(source(answer(body))("team-real-jaen")).rejects.toBeInstanceOf(
      UpstreamUnavailableError,
    );
  });

  it("rejects HTTP errors and non-JSON bodies", async () => {
    await expect(source(answer({}, 500))("team-real-jaen")).rejects.toThrow("HTTP 500");
    await expect(source(answer("<html>"))("team-real-jaen")).rejects.toThrow("no JSON");
  });

  it("turns a refused connection into an unavailable source", async () => {
    const fetch = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });
    await expect(source(fetch)("team-real-jaen")).rejects.toThrow("Sin conexión");
  });

  it("gives up after the timeout", async () => {
    const fetch = vi.fn(
      (_url: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init!.signal!.addEventListener("abort", () => reject(init!.signal!.reason));
        }),
    );
    await expect(
      source(fetch as unknown as typeof globalThis.fetch, 20)("team-real-jaen"),
    ).rejects.toThrow("Sin respuesta en 20 ms");
  });

  it("keeps the players but warns when meta.count does not match", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const result = await source(answer(payload([{ player_id: 1, name: "A" }], 25)))(
      "team-real-jaen",
    );
    expect(result!.players).toHaveLength(1);
    expect(warn).toHaveBeenCalledOnce();
  });
});

/** Shape of `GET /api/v1/players/966303` as the real local API answered it on 2026-10-21. */
const moha = {
  source: "Sofascore",
  player_id: 966303,
  name: "Moha Keita",
  usual_position: "M",
  country: "Senegal",
  birth_date: "1999-09-18",
  height_cm: 165,
  preferred_foot: null,
  fetched_at: "2026-10-21T12:07:39+00:00",
};
const profileSource = (fetch: typeof globalThis.fetch) =>
  createLocalApiPlayerProfile({ baseUrl: "http://127.0.0.1:8001", fetch });

describe("local API adapter: player profile", () => {
  it("resolves our derived id to the provider id only here", async () => {
    expect(sourcePlayerId("sofascore-player-966303")).toBe(966303);
    for (const id of ["player-0001", "sofascore-player-", "sofascore-player-01", "sofascore-player-1x", "sofascore-player-99999999999999999999"]) {
      expect(sourcePlayerId(id), id).toBeNull();
    }
  });

  it("returns personal data only, ignoring the provider name and the download time", async () => {
    const fetch = answer({ data: moha });
    expect(await profileSource(fetch)("sofascore-player-966303")).toEqual({
      player: {
        id: "sofascore-player-966303",
        name: "Moha Keita",
        position: "midfielder",
        birthDate: "1999-09-18",
        heightCm: 165,
        preferredFoot: null,
        country: "Senegal",
      },
      scope: "Datos personales según la fuente. No indican equipo actual.",
      source: { synthetic: false, label: "Datos reales" },
    });
    expect(fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:8001/api/v1/players/966303",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("keeps absent, null, empty or unknown values as unknown", async () => {
    const fetch = answer({
      data: { player_id: 1, name: "Sin Datos", usual_position: "X", country: "", height_cm: null, birth_date: null, preferred_foot: "Ambidiestro" },
    });
    const result = await profileSource(fetch)("sofascore-player-1");
    expect(result!.player).toEqual({
      id: "sofascore-player-1", name: "Sin Datos", position: null,
      birthDate: null, heightCm: null, preferredFoot: null, country: null,
    });
  });

  it("maps the foot whatever its case", async () => {
    const result = await profileSource(answer({ data: { ...moha, preferred_foot: "Left" } }))("sofascore-player-966303");
    expect(result!.player.preferredFoot).toBe("left");
  });

  it("does not call the API for ids that are not derived from it", async () => {
    const fetch = answer({ data: moha });
    expect(await profileSource(fetch)("player-0001")).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("has nothing for a player the source does not know (404)", async () => {
    expect(await profileSource(answer({ error: "Entidad no encontrada" }, 404))("sofascore-player-1")).toBeNull();
  });

  it.each([
    ["no data object", { player_id: 966303 }],
    ["another player", { data: { ...moha, player_id: 1 } }],
    ["no name", { data: { ...moha, name: "" } }],
    ["a malformed birth date", { data: { ...moha, birth_date: "18/09/1999" } }],
    ["a non-integer height", { data: { ...moha, height_cm: "165" } }],
    ["a non-text country", { data: { ...moha, country: 7 } }],
  ])("rejects a response with %s", async (_case, body) => {
    await expect(profileSource(answer(body))("sofascore-player-966303")).rejects.toBeInstanceOf(
      UpstreamUnavailableError,
    );
  });

  it("turns HTTP errors and refused connections into an unavailable source", async () => {
    await expect(profileSource(answer({}, 500))("sofascore-player-966303")).rejects.toThrow("HTTP 500");
    const refused = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });
    await expect(profileSource(refused)("sofascore-player-966303")).rejects.toThrow("Sin conexión");
  });
});

/**
 * Rows shaped like `GET /api/v1/players/{id}/matches` of the real local API
 * on 2026-10-22 (trimmed): a confirmed appearance and an unused substitute,
 * whose `statistics` only carries `totalShots: 0`, each with the service's
 * normalization (`normalized`).
 */
const normalizedFigure = (value: number | null, source: string | null = value === null ? null : "reported") => ({ value, source });
/** One stat of the service's `normalized.stats`. */
const served = (value: number | null, source: string | null = value === null ? null : "reported",
  rule: string | null = null, reason: string | null = value === null ? "not_reported" : null) => ({ value, source, rule, reason });
const SERVED_STATS = ["shots", "shots_on_target", "xg", "passes", "passes_accurate", "pass_accuracy", "dribbles", "dribbles_won",
  "ball_recoveries", "possession_lost", "duels_won", "duels_lost", "fouls", "saves", "rating"];
/** `normalized.stats` as the service gives it for a participation: `given` for an appearance, the rest not reported. */
const servedStats = (participation: string, given: Record<string, unknown> = {}) => Object.fromEntries(SERVED_STATS.map((name) => [name,
  participation === "unknown" ? served(null, null, null, "participation_unknown")
    : participation === "unused" ? ["xg", "rating", "pass_accuracy"].includes(name) ? served(null, null, null, "not_applicable") : served(0, "unused")
      : given[name] ?? served(null)]));
const normalized = (participation: string, minutes: number | null, goals: number | null, assists: number | null,
  sources: { goals?: string } = {}, stats: Record<string, unknown> = {}) => ({
  participation, basis: "minutes", minutes: normalizedFigure(minutes, participation === "unused" && minutes !== null ? "unused" : undefined),
  goals: normalizedFigure(goals, participation === "unused" && goals !== null ? "unused" : sources.goals),
  assists: normalizedFigure(assists, participation === "unused" && assists !== null ? "unused" : undefined),
  stats: servedStats(participation, stats), issues: [],
});
/** What the service serves for the default row's statistics. */
const DEFAULT_STATS = { shots: served(4), xg: served(0.454), passes: served(24), passes_accurate: served(19),
  pass_accuracy: served((19 / 24) * 100, "calculated", "ratio"), rating: served(7.4) };
const callUp = (overrides: Record<string, unknown> = {}, match: Record<string, unknown> = {}) => ({
  source: "Sofascore", match_id: 16599363, team_id: 2841, player_id: 966303, side: "away",
  source_team_id: 2841, substitute: false, match_position: "F", shirt_number: 19, minutes_played: 83,
  played: true, fetched_at: "2026-10-21T12:07:31+00:00", normalized: normalized("played", 83, 1, 0, {}, DEFAULT_STATS),
  statistics: { totalPass: 24, accuratePass: 19, goalAssist: 0, goals: 1, minutesPlayed: 83, rating: 7.4,
    expectedGoals: 0.454, totalShots: 4, statisticsType: { sportSlug: "football", statisticsType: "player" } },
  match: { source: "Sofascore", match_id: 16599363, kickoff_utc: "2026-08-30T17:15:00+00:00", status: "finished",
    tournament_id: 94191, competition: "Primera Federacion, Group 2", season_id: 97382, season: "26/27", round: 1,
    home_team_id: 135672, away_team_id: 2841, home_score: 0, away_score: 3, fetched_at: "2026-10-21T12:07:26+00:00",
    ...match },
  ...overrides,
});
const unused = callUp(
  { match_id: 16599388, side: "home", substitute: true, minutes_played: null, played: null, normalized: normalized("unused", 0, 0, 0),
    statistics: { totalShots: 0, statisticsType: { sportSlug: "football", statisticsType: "player" } } },
  { match_id: 16599388, kickoff_utc: "2026-09-06T09:30:00+00:00", round: 2, home_team_id: 2841, away_team_id: 24265,
    home_score: 2, away_score: 2 },
);
const TEAM_NAMES: Record<string, string> = { "2841": "Real Jaén", "135672": "CE Europa" };

/** Simulated local API for the matches and the team names they need. */
function matchesApi(rows: unknown[] | null, teams: Record<string, string> = TEAM_NAMES) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const scope = scoped(url);
    if (scope) return scope;
    const team = /\/api\/v1\/teams\/(\d+)$/.exec(url);
    if (team) {
      const name = teams[team[1]];
      return name
        ? new Response(JSON.stringify({ data: { source: "Sofascore", team_id: Number(team[1]), name } }))
        : new Response(JSON.stringify({ error: "Entidad no encontrada" }), { status: 404 });
    }
    return rows === null
      ? new Response(JSON.stringify({ error: "Entidad no encontrada" }), { status: 404 })
      : new Response(JSON.stringify({ data: rows, meta: { count: rows.length } }));
  });
}
const matchesSource = (fetch: typeof globalThis.fetch) =>
  createLocalApiObservedMatches({ baseUrl: "http://127.0.0.1:8001", fetch });
const missing = { value: null, reason: "not_available" };

describe("local API adapter: observed matches", () => {
  it("returns call-ups newest first, with the source's season, competition and team names", async () => {
    const fetch = matchesApi([callUp(), unused]);
    const result = await matchesSource(fetch)("sofascore-player-966303");
    expect(result!.matches.map((m) => m.match.id)).toEqual(["sofascore-match-16599388", "sofascore-match-16599363"]);
    expect(result!.matches[1]).toEqual({
      match: {
        id: "sofascore-match-16599363", kickoff: "2026-08-30T17:15:00+00:00", status: "finished",
        competition: "Primera Federacion, Group 2", season: "26/27", round: 1,
        home: { id: "sofascore-team-135672", name: "CE Europa" },
        away: { id: "team-real-jaen", name: "Real Jaén" },
        score: { home: 0, away: 3 },
      },
      side: "away",
      role: "starter",
      position: "forward",
      participation: "confirmed",
      stats: {
        minutes: { value: 83 }, goals: { value: 1 }, assists: { value: 0 }, shots: { value: 4 },
        xg: { value: 0.454 }, passesCompleted: { value: 19 }, passes: { value: 24 }, rating: { value: 7.4 },
        passAccuracy: { value: (19 / 24) * 100, derivation: { source: "calculated", rule: "ratio" } },
        // Stats the service does not have stay missing, never 0, although other stats are given.
        shotsOnTarget: missing, dribbles: missing, dribblesWon: missing, recoveries: missing, possessionLost: missing,
        duelsWon: missing, duelsLost: missing, fouls: missing, saves: missing,
      },
    });
    expect(result!.ratingSource).toBe("SofaScore");
    expect(result!.source).toEqual({ synthetic: false, label: "Datos reales" });
    expect(fetch).toHaveBeenCalledWith("http://127.0.0.1:8001/api/v1/players/966303/matches", expect.anything());
  });

  it("maps each metric from the service's normalization, keeping real zeros", async () => {
    const stats = { shots: served(3), shots_on_target: served(0), dribbles: served(4), dribbles_won: served(1),
      ball_recoveries: served(6), possession_lost: served(9), duels_won: served(7), duels_lost: served(0), fouls: served(2), saves: served(3) };
    const result = await matchesSource(matchesApi([callUp({ match_position: "G",
      normalized: normalized("played", 90, 0, 0, { goals: "omitted_zero" }, stats) })]))("sofascore-player-966303");
    const [row] = result!.matches;
    expect(row.position).toBe("goalkeeper");
    expect(row.stats).toMatchObject({
      shots: { value: 3 }, shotsOnTarget: { value: 0 }, dribbles: { value: 4 }, dribblesWon: { value: 1 },
      recoveries: { value: 6 }, possessionLost: { value: 9 }, duelsWon: { value: 7 }, duelsLost: { value: 0 },
      fouls: { value: 2 }, saves: { value: 3 }, passes: missing,
      // Goals the source omits in a full appearance: 0 from the service's rule, not from this adapter.
      minutes: { value: 90 }, goals: { value: 0 },
    });
    // A reported value carries no derivation.
    expect(row.stats.shotsOnTarget).toEqual({ value: 0 });
  });

  it("keeps the service's derivation and the reason of each missing value, without reading the raw row", async () => {
    // Moha Keita against Rayo Majadahonda: 2 shots, both blocked, no shots-on-target key in the raw row.
    const statistics = { minutesPlayed: 90, totalShots: 2, blockedScoringAttempt: 2, totalContest: 4, fouls: 1, wonContest: 9 };
    const stats = { shots: served(2), shots_on_target: served(0, "derived", "shots_partition"), dribbles: served(4),
      dribbles_won: served(null, null, null, "inconsistent"), fouls: served(null, null, null, "version_conflict"),
      duels_lost: served(null, null, null, "duplicate_disagree"), saves: served(null, null, null, "invalid"),
      xg: served(null, null, null, "reported_null"), pass_accuracy: served(null, null, null, "not_applicable") };
    const result = await matchesSource(matchesApi([callUp({ statistics,
      normalized: normalized("played", 90, 0, 0, {}, stats) })]))("sofascore-player-966303");
    expect(result!.matches[0].stats).toMatchObject({
      shots: { value: 2 }, shotsOnTarget: { value: 0, derivation: { source: "derived", rule: "shots_partition" } },
      dribbles: { value: 4 },
      // The raw row's wonContest is not shown: the service says its figures contradict each other.
      dribblesWon: { value: null, reason: "inconsistent" },
      fouls: { value: null, reason: "conflicting" }, duelsLost: { value: null, reason: "conflicting" },
      saves: { value: null, reason: "invalid" }, xg: missing, passAccuracy: missing,
    });
  });

  it.each([[null], ["X"], [undefined]])("leaves an unknown match position (%s) unknown", async (position) => {
    const result = await matchesSource(matchesApi([callUp({ match_position: position })]))("sofascore-player-966303");
    expect(result!.matches[0].position).toBeNull();
  });

  it("takes participation from the service: an unused substitute has 0 minutes, goals and assists and no other stat", async () => {
    const result = await matchesSource(matchesApi([unused]))("sofascore-player-966303");
    const [row] = result!.matches;
    expect(row.role).toBe("substitute");
    expect(row.participation).toBe("unused");
    expect([row.stats.minutes, row.stats.goals, row.stats.assists]).toEqual([{ value: 0 }, { value: 0 }, { value: 0 }]);
    // totalShots 0 of the bench is not a stat of the match.
    expect(row.stats.shots).toEqual(missing);
  });

  it("leaves a call-up the service cannot resolve unknown, with no stats at all", async () => {
    const result = await matchesSource(matchesApi([callUp({ normalized: normalized("unknown", null, null, null) })]))(
      "sofascore-player-966303",
    );
    const [row] = result!.matches;
    expect(row.participation).toBe("unknown");
    for (const stat of Object.values(row.stats)) expect(stat).toEqual(missing);
  });

  it("follows the service over the raw row: minutes it could not read stay missing, never re-derived here", async () => {
    // The raw row says 83, but the service found duplicates that disagree.
    const result = await matchesSource(matchesApi([callUp({ normalized: normalized("played", null, 1, 0) })]))("sofascore-player-966303");
    expect(result!.matches[0].participation).toBe("confirmed");
    expect(result!.matches[0].stats.minutes).toEqual(missing);
  });

  it("leaves a rating from line-ups read before a score correction pending, never the current one", async () => {
    const result = await matchesSource(matchesApi([callUp({ normalized: normalized("played", 83, null, null, {},
      { ...DEFAULT_STATS, rating: served(null, null, null, "before_score_correction") }) })]))("sofascore-player-966303");
    const { stats } = result!.matches[0];
    expect(stats.rating).toEqual({ value: null, reason: "not_collected" });
    expect([stats.goals, stats.assists]).toEqual([missing, missing]);
    expect(stats.shots).toEqual({ value: 4 });  // independent of the score: still served
  });

  it("keeps every other omitted stat missing, never 0, when participation is confirmed", async () => {
    const result = await matchesSource(matchesApi([callUp({ statistics: { totalPass: 3, totalShots: 0, minutesPlayed: 22 },
      normalized: normalized("played", 22, null, null, {}, { shots: served(0), passes: served(3) }) })]))("sofascore-player-966303");
    const { stats } = result!.matches[0];
    expect(stats.shots).toEqual({ value: 0 });
    expect(stats.passes).toEqual({ value: 3 });
    for (const key of ["goals", "assists", "xg", "passesCompleted", "passAccuracy", "rating"] as const) expect(stats[key], key).toEqual(missing);
  });

  const withStat = (name: string, stat: unknown) => {
    const row = normalized("played", 83, 1, 0, {}, DEFAULT_STATS);
    return { normalized: { ...row, stats: { ...row.stats, [name]: stat } } };
  };
  it.each([
    ["no normalization (an older service)", { normalized: undefined }],
    ["no normalized stats (a service before the stats normalization)", { normalized: { ...normalized("played", 83, 1, 0), stats: undefined } }],
    ["a normalized stat missing", { normalized: { ...normalized("played", 83, 1, 0), stats: { shots: served(1) } } }],
    ["a derivation the stat cannot have", withStat("fouls", served(0, "derived", "shots_partition"))],
    ["an unknown rule", withStat("shots_on_target", served(0, "derived", "team_total"))],
    ["an unknown reason", withStat("fouls", served(null, null, null, "maybe"))],
    ["a value with a reason", withStat("fouls", served(1, "reported", null, "not_reported"))],
    ["a decimal count", withStat("fouls", served(1.5))],
    ["a non-numeric stat", withStat("shots", served("4" as unknown as number))],
    ["an unknown call-up with a stat", { normalized: { ...normalized("unknown", null, null, null),
      stats: { ...servedStats("unknown"), shots: served(0) } } }],
    ["an unknown participation", { normalized: { ...normalized("played", 83, 1, 0), participation: "maybe" } }],
    ["an unused substitute with minutes", { normalized: normalized("unused", 20, 0, 0) }],
    ["an unknown call-up with goals", { normalized: { ...normalized("unknown", null, null, null), goals: normalizedFigure(1) } }],
    ["a value without its origin", { normalized: { ...normalized("played", 83, 1, 0), goals: { value: 1, source: null } } }],
    ["a boolean goal", { normalized: { ...normalized("played", 83, 1, 0), goals: { value: true, source: "reported" } } }],
  ])("rejects a call-up with %s", async (_case, overrides) => {
    await expect(matchesSource(matchesApi([callUp(overrides)]))("sofascore-player-966303")).rejects.toBeInstanceOf(UpstreamUnavailableError);
  });

  it("leaves a team the source cannot name unnamed, never a name from elsewhere", async () => {
    const result = await matchesSource(matchesApi([callUp()], { "2841": "Real Jaén" }))("sofascore-player-966303");
    expect(result!.matches[0].match.home).toEqual({ id: "sofascore-team-135672", name: null });
  });

  it("asks for each team name once", async () => {
    const fetch = matchesApi([callUp(), callUp({ match_id: 2 }, { match_id: 2 })]);
    await matchesSource(fetch)("sofascore-player-966303");
    const teamCalls = fetch.mock.calls.map((call) => String((call as unknown[])[0])).filter((url) => url.includes("/teams/"));
    expect(teamCalls.sort()).toEqual([
      "http://127.0.0.1:8001/api/v1/teams/135672",
      "http://127.0.0.1:8001/api/v1/teams/2841",
    ]);
  });

  it("asks for team names one at a time: the local service refuses concurrent connections", async () => {
    const names = matchesApi([callUp(), callUp({ match_id: 2 }, { match_id: 2, home_team_id: 7 })], { ...TEAM_NAMES, "7": "Otro" });
    let inFlight = 0;
    let most = 0;
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      inFlight++;
      most = Math.max(most, inFlight);
      await new Promise((resolve) => setTimeout(resolve, 5));
      inFlight--;
      return names(input);
    });
    await matchesSource(fetch)("sofascore-player-966303");
    expect(fetch).toHaveBeenCalledTimes(5);
    expect(most).toBe(1);
  });

  it("gives no score unless the match is finished, and unknown statuses stay unknown", async () => {
    const result = await matchesSource(matchesApi([
      callUp({}, { status: "notstarted", home_score: null, away_score: null }),
      callUp({ match_id: 2 }, { match_id: 2, status: "inprogress" }),
    ]))("sofascore-player-966303");
    expect(result!.matches.map((m) => [m.match.status, m.match.score])).toEqual([["scheduled", null], [null, null]]);
  });

  it("distinguishes an empty list from an unknown player", async () => {
    expect((await matchesSource(matchesApi([]))("sofascore-player-966303"))!.matches).toEqual([]);
    expect(await matchesSource(matchesApi(null))("sofascore-player-1")).toBeNull();
    const fetch = matchesApi([]);
    expect(await matchesSource(fetch)("player-0001")).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each([
    ["no data array", { meta: {} }],
    ["another player", { data: [callUp({ player_id: 1 })] }],
    ["a bad side", { data: [callUp({ side: "neutral" })] }],
    ["a bad kickoff", { data: [callUp({}, { kickoff_utc: "mañana" })] }],
    ["no team id", { data: [callUp({}, { home_team_id: null })] }],
  ])("rejects a response with %s", async (_case, body) => {
    await expect(matchesSource(answer(body))("sofascore-player-966303")).rejects.toBeInstanceOf(UpstreamUnavailableError);
  });

  it("fails as unavailable when a team name lookup fails", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) =>
      scoped(input) ?? (String(input).includes("/teams/") ? new Response("{}", { status: 500 }) : new Response(JSON.stringify({ data: [callUp()] }))),
    );
    await expect(matchesSource(fetch)("sofascore-player-966303")).rejects.toThrow("HTTP 500");
  });
});

/** Rows shaped like `GET /api/v1/teams/2841/matches` of the real local API on 2026-10-21. */
const fixture = (overrides: Record<string, unknown> = {}) => ({
  source: "Sofascore", match_id: 16599363, kickoff_utc: "2026-08-30T17:15:00+00:00", status: "finished",
  tournament_id: 94191, competition: "Primera Federacion, Group 2", season_id: 97382, season: "26/27", round: 1,
  home_team_id: 135672, away_team_id: 2841, home_score: 0, away_score: 3, fetched_at: "2026-10-21T12:07:26+00:00",
  available_sections: ["event", "lineups"], ...overrides,
});
const pending = fixture({ match_id: 16599386, kickoff_utc: "2026-11-07T15:30:00+00:00", status: "notstarted",
  round: 11, home_team_id: 24322, away_team_id: 2841, home_score: null, away_score: null });
const cup = fixture({ match_id: 17259821, kickoff_utc: "2026-10-28T19:00:00+00:00", status: "notstarted",
  competition: "Copa del Rey", season_id: 104108, tournament_id: 150, round: 1, home_team_id: 493345,
  home_score: null, away_score: null });
const NAMES: Record<string, string> = { "2841": "Real Jaén", "135672": "CE Europa", "24322": "Sevilla Atlético", "493345": "CD Rival Copa" };

/** Simulated local API for a team's matches and the team names they need. */
function teamApi(rows: unknown[] | null, names: Record<string, string> = NAMES) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const scope = scoped(url);
    if (scope) return scope;
    const team = /\/api\/v1\/teams\/(\d+)$/.exec(url);
    if (team) {
      return names[team[1]]
        ? new Response(JSON.stringify({ data: { source: "Sofascore", team_id: Number(team[1]), name: names[team[1]] } }))
        : new Response(JSON.stringify({ error: "Entidad no encontrada" }), { status: 404 });
    }
    return rows === null
      ? new Response(JSON.stringify({ error: "Entidad no encontrada" }), { status: 404 })
      : new Response(JSON.stringify({ data: rows, meta: { count: rows.length } }));
  });
}
const teamSource = (fetch: typeof globalThis.fetch) =>
  createLocalApiTeamMatches({ baseUrl: "http://127.0.0.1:8001", fetch });
const urls = (fetch: ReturnType<typeof teamApi>) => fetch.mock.calls.map((call) => String((call as unknown[])[0]));

describe("local API adapter: team matches", () => {
  it("asks for our team's source id and returns its matches by kickoff, with the source's names", async () => {
    const fetch = teamApi([pending, fixture()]);
    const result = await teamSource(fetch)("team-real-jaen");
    expect(urls(fetch).slice(0, 2)).toEqual([SCOPE_URL, "http://127.0.0.1:8001/api/v1/teams/2841/matches"]);
    expect(result!.matches.map((row) => row.match.id)).toEqual(["sofascore-match-16599363", "sofascore-match-16599386"]);
    expect(result!.competitions).toEqual([{ name: "Primera Federación, Grupo 2", season: "26/27" }]);
    expect(result!.matches[0]).toEqual({
      side: "away",
      match: {
        id: "sofascore-match-16599363", kickoff: "2026-08-30T17:15:00+00:00", status: "finished",
        competition: "Primera Federacion, Group 2", season: "26/27", round: 1,
        home: { id: "sofascore-team-135672", name: "CE Europa" }, away: { id: "team-real-jaen", name: "Real Jaén" },
        score: { home: 0, away: 3 },
      },
    });
    expect(result!.scope).toMatch(/No es necesariamente un calendario completo/);
    expect(result!.source).toEqual({ synthetic: false, label: "Datos reales" });
  });

  it("never gives a pending match a score, even if the source sends one, and keeps unknown statuses unknown", async () => {
    const result = await teamSource(teamApi([
      { ...pending, home_score: 0, away_score: 0 },
      fixture({ match_id: 2, status: "inprogress", kickoff_utc: "2026-09-01T10:00:00+00:00" }),
      fixture({ match_id: 3, home_score: null, away_score: null, kickoff_utc: "2026-09-02T10:00:00+00:00" }),
    ]))("team-real-jaen");
    expect(result!.matches.map(({ match }) => [match.status, match.score])).toEqual([
      [null, null], ["finished", null], ["scheduled", null],
    ]);
  });

  it("puts matches without a date last and keeps missing season or competition missing", async () => {
    const result = await teamSource(teamApi([
      fixture({ match_id: 4, kickoff_utc: null, season: null, competition: null, round: null }), fixture(),
    ]))("team-real-jaen");
    expect(result!.matches.map(({ match }) => [match.id, match.kickoff, match.season, match.competition, match.round])).toEqual([
      ["sofascore-match-16599363", "2026-08-30T17:15:00+00:00", "26/27", "Primera Federacion, Group 2", 1],
      ["sofascore-match-4", null, null, null, null],
    ]);
  });

  it("has nothing for a team without explicit correspondence, and does not call the API", async () => {
    const fetch = teamApi([fixture()]);
    expect(await teamSource(fetch)("team-montealto")).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each([
    ["Copa del Rey", cup],
    ["another group of the same league and season", fixture({ tournament_id: 94190 })],
    ["the same group in an undeclared season", fixture({ season_id: 12345 })],
  ])("never shows a match outside the declared scope (%s): the answer is invalid", async (_case, row) => {
    await expect(teamSource(teamApi([fixture(), row]))("team-real-jaen")).rejects.toThrow("fuera del ámbito declarado");
    await expect(matchesSource(matchesApi([callUp({ match_id: row.match_id }, row)]))("sofascore-player-966303"))
      .rejects.toThrow("fuera del ámbito declarado");
    await expect(detailSource(detailApi(matchBody(row), statsBody()))(`sofascore-match-${row.match_id}`))
      .rejects.toThrow("fuera del ámbito declarado");
  });

  it.each([
    ["no scope route (an older service)", () => new Response(JSON.stringify({ error: "Ruta inexistente" }), { status: 404 })],
    ["an empty scope", () => new Response(JSON.stringify({ data: { competitions: [] } }))],
    ["a scope without ids", () => new Response(JSON.stringify({ data: { competitions: [{ name: "Grupo 2" }] } }))],
  ])("fails as unavailable with %s, never serving everything", async (_case, scopeAnswer) => {
    const rows = teamApi([fixture()]);
    const fetch = vi.fn(async (input: RequestInfo | URL) => String(input) === SCOPE_URL ? scopeAnswer() : rows(input));
    await expect(teamSource(fetch)("team-real-jaen")).rejects.toBeInstanceOf(UpstreamUnavailableError);
    expect(urls(fetch as unknown as ReturnType<typeof teamApi>)).toEqual([SCOPE_URL]);
  });

  it("distinguishes an empty list from a team the source does not know", async () => {
    expect((await teamSource(teamApi([]))("team-real-jaen"))!.matches).toEqual([]);
    expect(await teamSource(teamApi(null))("team-real-jaen")).toBeNull();
  });

  it.each([
    ["no data array", { meta: {} }],
    ["a match without our team", { data: [fixture({ away_team_id: 7 })] }],
    ["a bad kickoff", { data: [fixture({ kickoff_utc: "pronto" })] }],
    ["a non-numeric score", { data: [fixture({ home_score: "0" })] }],
    ["no match id", { data: [fixture({ match_id: null })] }],
  ])("rejects a response with %s", async (_case, body) => {
    await expect(teamSource(answer(body))("team-real-jaen")).rejects.toBeInstanceOf(UpstreamUnavailableError);
  });

  it("reuses resolved team names across lists, one request at a time, without keeping failures", async () => {
    const first = teamApi([fixture(), pending]);
    await teamSource(first)("team-real-jaen");
    expect(urls(first).filter((url) => url.includes("/teams/") && !url.endsWith("/matches"))).toHaveLength(3);

    // A player's call-ups share those teams: no new name requests.
    const again = matchesApi([callUp()], {});
    await matchesSource(again)("sofascore-player-966303");
    expect(urls(again as unknown as ReturnType<typeof teamApi>).filter((url) => url.includes("/teams/"))).toEqual([]);

    clearTeamNameCache();
    const failing = vi.fn(async (input: RequestInfo | URL) =>
      scoped(input) ?? (String(input).endsWith("/matches") ? new Response(JSON.stringify({ data: [fixture()] })) : new Response("{}", { status: 500 })));
    await expect(teamSource(failing)("team-real-jaen")).rejects.toThrow("HTTP 500");
    const recovered = teamApi([fixture()]);
    await teamSource(recovered)("team-real-jaen");
    expect(urls(recovered).filter((url) => !url.endsWith("/matches")).length).toBeGreaterThan(0);
  });
});

/** Shapes of the real local API's /matches/{id} and /matches/{id}/statistics on 2026-10-21 (trimmed). */
const matchBody = (overrides: Record<string, unknown> = {}) => ({
  data: { ...fixture(), home_team_name: "CE Europa", away_team_name: "Real Jaén", ...overrides },
  meta: { sections: { statistics: "stored", lineups: "stored", incidents: "stored" } },
});
const item = (key: string, homeValue: unknown, awayValue: unknown) => ({ key, name: key, home: String(homeValue), away: String(awayValue), homeValue, awayValue });
const statsBody = (availability = "stored", groups?: unknown[]) => ({
  data: availability === "not_stored" ? null : availability === "empty" ? { statistics: [] } : {
    statistics: [
      { period: "ALL", groups: groups ?? [
        { groupName: "Match overview", statisticsItems: [item("ballPossession", 46, 54), item("expectedGoals", 1.81, 1.6),
          item("cornerKicks", 3, 3), item("fouls", 13, 5), item("totalShotsOnGoal", 15, 13), item("yellowCards", 0, 1)] },
        { groupName: "Shots", statisticsItems: [item("totalShotsOnGoal", 99, 99), item("shotsOnGoal", 4, 5)] },
      ] },
      { period: "1ST", groups: [{ groupName: "Match overview", statisticsItems: [item("ballPossession", 1, 99)] }] },
    ],
  },
  meta: { match_id: 16599363, section: "statistics", source: "Sofascore", source_key: "statistics", availability,
    fetched_at: availability === "not_stored" ? null : "2026-10-21T12:07:30+00:00", versions: availability === "not_stored" ? 0 : 1 },
});

/** Simulated local API for a match and its statistics. */
/** Shape of the real local API's /matches/{id}/lineups (trimmed): a starter with minutes, an unused substitute. */
const lineupEntry = (id: number, name: string, overrides: Record<string, unknown> = {}) => ({
  player: { name, id, position: "M", shortName: name, country: { name: "Spain" } }, teamId: 135672,
  shirtNumber: id % 30, jerseyNumber: String(id % 30), position: "M", substitute: false,
  statistics: { minutesPlayed: 90, totalShots: 1, rating: 7 }, ...overrides,
});
const benchEntry = (id: number, name: string) =>
  lineupEntry(id, name, { substitute: true, statistics: { totalShots: 0, statisticsType: { sportSlug: "football", statisticsType: "player" } } });
const eleven = (first: number) => Array.from({ length: 11 }, (_, i) => lineupEntry(first + i, `Titular ${first + i}`));
/** The service's normalized call-ups for the default line-ups: starters played 90, «Suplente Local» unused, «Entra» 22. */
const LINEUP_CALL_UPS = [
  ...[...Array.from({ length: 11 }, (_, i) => 100 + i), ...Array.from({ length: 11 }, (_, i) => 300 + i)]
    .map((id) => ({ player_id: id, ...normalized("played", 90, 0, 0) })),
  { player_id: 200, ...normalized("unused", 0, 0, 0) },
  { player_id: 400, ...normalized("played", 22, 0, 0) },
];
const lineupsBody = (availability = "stored", data?: unknown, callUps: unknown[] = data ? [] : LINEUP_CALL_UPS) => ({
  normalized: availability === "stored" ? { players: callUps } : undefined,
  data: availability === "not_stored" ? null : data ?? {
    confirmed: true, statisticalVersion: 3,
    home: { players: [...eleven(100), benchEntry(200, "Suplente Local")], formation: "4-4-2", supportStaff: [], playerColor: {} },
    away: { players: [...eleven(300), lineupEntry(400, "Entra", { substitute: true, statistics: { minutesPlayed: 22 } })], formation: "4-3-3" },
  },
  meta: { section: "lineups", availability, fetched_at: availability === "not_stored" ? null : "2026-10-21T12:07:31+00:00" },
});

/** Shape of the real local API's /matches/{id}/incidents (trimmed): kinds seen on 2026-10-21, plus cases it may send. */
const incidentsBody = (availability = "stored", incidents?: unknown[]) => ({
  data: availability === "not_stored" ? null : { incidents: incidents ?? [
    { text: "FT", homeScore: 0, awayScore: 3, time: 90, addedTime: 999, incidentType: "period" },
    { playerIn: { id: 1594459, name: "Guillem Cardelus" }, playerOut: { id: 857907, name: "Gori Gracia" }, id: 1, time: 90,
      addedTime: 2, injury: false, isHome: true, incidentClass: "regular", incidentType: "substitution" },
    { homeScore: 0, awayScore: 3, player: { id: 966303, name: "Moha Keita" }, assist1: { id: 1545211, name: "David Serrano" },
      id: 2, time: 77, isHome: false, incidentClass: "regular", incidentType: "goal" },
    { from: "penalty", player: { id: 942663, name: "Marco Siverio Toro" }, id: 3, time: 88, isHome: false, incidentClass: "penalty", incidentType: "goal" },
    { player: { id: 175409, name: "Javi Moyano" }, id: 4, time: 45, addedTime: 6, incidentClass: "yellowRed", incidentType: "card" },
    { player: { id: 964890, name: "Andy Escudero" }, id: 5, time: 64, incidentClass: "missed", incidentType: "inGamePenalty" },
    { confirmed: true, player: { id: 848589, name: "Moha Sanhaji" }, id: 6, time: 13, incidentClass: "goalAwarded", incidentType: "varDecision" },
  ], home: {}, away: {} },
  meta: { section: "incidents", availability, fetched_at: availability === "not_stored" ? null : "2026-10-21T12:07:31+00:00" },
});

function detailApi(match: unknown, stats: unknown | (() => Response), lineups: unknown | (() => Response) = lineupsBody("not_stored"),
  incidents: unknown | (() => Response) = incidentsBody("not_stored")) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const scope = scoped(url);
    if (scope) return scope;
    if (url.endsWith("/incidents")) {
      return typeof incidents === "function" ? (incidents as () => Response)() : new Response(JSON.stringify(incidents));
    }
    if (url.endsWith("/statistics")) {
      return typeof stats === "function" ? (stats as () => Response)() : new Response(JSON.stringify(stats));
    }
    if (url.endsWith("/lineups")) {
      return typeof lineups === "function" ? (lineups as () => Response)() : new Response(JSON.stringify(lineups));
    }
    return match === null ? new Response(JSON.stringify({ error: "Partido no encontrado" }), { status: 404 })
      : new Response(JSON.stringify(match));
  });
}
const detailSource = (fetch: typeof globalThis.fetch) => createLocalApiMatchDetail({ baseUrl: "http://127.0.0.1:8001", fetch });

describe("local API adapter: real match detail", () => {
  it("reads only ids derived from the source: a synthetic id is never taken as real", async () => {
    expect(sourceMatchId("sofascore-match-16599363")).toBe(16599363);
    for (const id of ["match-r01-01", "sofascore-match-", "sofascore-match-01", "sofascore-player-1"]) expect(sourceMatchId(id), id).toBeNull();
    const fetch = detailApi(matchBody(), statsBody());
    expect(await detailSource(fetch)("match-r01-01")).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("returns the match with the source's names and the ALL-period team stats, missing ones unknown", async () => {
    const fetch = detailApi(matchBody(), statsBody());
    const result = await detailSource(fetch)("sofascore-match-16599363");
    expect(fetch.mock.calls.map((call) => String((call as unknown[])[0]))).toEqual([
      SCOPE_URL, "http://127.0.0.1:8001/api/v1/matches/16599363", "http://127.0.0.1:8001/api/v1/matches/16599363/statistics",
      "http://127.0.0.1:8001/api/v1/matches/16599363/lineups", "http://127.0.0.1:8001/api/v1/matches/16599363/incidents",
    ]);
    expect(result!.match).toEqual({
      id: "sofascore-match-16599363", kickoff: "2026-08-30T17:15:00+00:00", status: "finished",
      competition: "Primera Federacion, Group 2", season: "26/27", round: 1,
      home: { id: "sofascore-team-135672", name: "CE Europa" }, away: { id: "team-real-jaen", name: "Real Jaén" },
      score: { home: 0, away: 3 },
    });
    expect(result!.statistics).toEqual({
      state: "stored", fetchedAt: "2026-10-21T12:07:30+00:00",
      home: { possession: { value: 46 }, shots: { value: 15 }, shotsOnTarget: { value: 4 }, xg: { value: 1.81 },
        corners: { value: 3 }, fouls: { value: 13 }, yellowCards: { value: 0 }, redCards: missing },
      away: { possession: { value: 54 }, shots: { value: 13 }, shotsOnTarget: { value: 5 }, xg: { value: 1.6 },
        corners: { value: 3 }, fouls: { value: 5 }, yellowCards: { value: 1 }, redCards: missing },
    });
    expect(result!.source).toEqual({ synthetic: false, label: "Datos reales" });
  });

  it("distinguishes empty and not stored statistics, and a pending match keeps no score", async () => {
    const empty = await detailSource(detailApi(matchBody(), statsBody("empty")))("sofascore-match-16599363");
    expect(empty!.statistics).toEqual({ state: "empty", fetchedAt: "2026-10-21T12:07:30+00:00" });
    const pendingDetail = await detailSource(detailApi(
      matchBody({ ...pending, home_team_name: "Sevilla Atlético", away_team_name: "Real Jaén" }), statsBody("not_stored"),
    ))("sofascore-match-16599386");
    expect(pendingDetail!.statistics).toEqual({ state: "not_stored", fetchedAt: null });
    expect([pendingDetail!.match.status, pendingDetail!.match.score]).toEqual(["scheduled", null]);
  });

  it("keeps the header when the statistics cannot be read, with an error state", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    for (const stats of [
      () => new Response("{}", { status: 500 }),
      () => { throw new TypeError("fetch failed"); },
      () => new Response(JSON.stringify({ data: null, meta: { availability: "unknown" } })),
      () => new Response(JSON.stringify(statsBody("stored", [{ statisticsItems: [item("fouls", "13", 5)] }]))),
      () => new Response(JSON.stringify({ error: "Partido no encontrado" }), { status: 404 }),
    ]) {
      const result = await detailSource(detailApi(matchBody(), stats))("sofascore-match-16599363");
      expect(result!.statistics).toEqual({ state: "error", fetchedAt: null });
      expect(result!.match.home.name).toBe("CE Europa");
    }
  });

  it("has nothing for a match the source does not know, and fails as unavailable when the match cannot be read", async () => {
    expect(await detailSource(detailApi(null, statsBody()))("sofascore-match-1")).toBeNull();
    await expect(detailSource(detailApi({ data: { match_id: 1 } }, statsBody()))("sofascore-match-1")).rejects.toBeInstanceOf(UpstreamUnavailableError);
    await expect(detailSource(answer({}, 500))("sofascore-match-1")).rejects.toThrow("HTTP 500");
  });

  it("keeps a team name the source does not give as unknown", async () => {
    const result = await detailSource(detailApi(matchBody({ home_team_name: null }), statsBody()))("sofascore-match-16599363");
    expect(result!.match.home).toEqual({ id: "sofascore-team-135672", name: null });
  });

  const local = (matchId: number) => ({ match_id: matchId, decision: "local", reason: "Aplazado y jugado con otro id",
    decided_by: "Analista", decided_on: "2026-10-22", evidence: "Mismo cruce", note: "Decisión local…" });

  it("reads a local replacement as such, in both directions, and has none when the source sends none", async () => {
    const replaced = await detailSource(detailApi(matchBody({ ...pending, replaced_by: local(17118209) }), statsBody("not_stored")))("sofascore-match-16599386");
    expect(replaced!.localReplacement).toEqual({ replacedBy: { matchId: "sofascore-match-17118209", reason: "Aplazado y jugado con otro id",
      decidedBy: "Analista", decidedOn: "2026-10-22" }, replaces: [] });
    const current = await detailSource(detailApi(matchBody({ replaces: [local(16599956)] }), statsBody()))("sofascore-match-16599363");
    expect(current!.localReplacement).toEqual({ replacedBy: null, replaces: [{ matchId: "sofascore-match-16599956",
      reason: "Aplazado y jugado con otro id", decidedBy: "Analista", decidedOn: "2026-10-22" }] });
    expect("localReplacement" in (await detailSource(detailApi(matchBody(), statsBody()))("sofascore-match-16599363"))!).toBe(false);
  });

  it.each([
    ["not marked as local", { replaced_by: { ...local(1), decision: "source" } }],
    ["without a reason", { replaced_by: { ...local(1), reason: " " } }],
    ["with a date that is not one", { replaced_by: { ...local(1), decided_on: "22/10/2026" } }],
    ["replaced and replacing at once", { replaced_by: local(1), replaces: [local(2)] }],
    ["replaces that is not a list", { replaces: local(2) }],
  ])("rejects a local replacement %s", async (_name, fields) => {
    await expect(detailSource(detailApi(matchBody(fields), statsBody()))("sofascore-match-16599363")).rejects.toBeInstanceOf(UpstreamUnavailableError);
  });
});

describe("local API adapter: real line-ups", () => {
  const detail = (lineups: unknown | (() => Response)) =>
    detailSource(detailApi(matchBody(), statsBody(), lineups))("sofascore-match-16599363");

  it("keeps each side apart, the source's order, role flag, shirt number and confirmation", async () => {
    const { lineups } = (await detail(lineupsBody()))!;
    if (lineups.state !== "stored") throw new Error("expected stored line-ups");
    expect([lineups.confirmed, lineups.fetchedAt, lineups.home.length, lineups.away.length]).toEqual([true, "2026-10-21T12:07:31+00:00", 12, 12]);
    expect(lineups.home[0]).toEqual({
      player: { id: "sofascore-player-100", name: "Titular 100", position: "midfielder" },
      shirtNumber: 10, role: "starter", participation: "confirmed", minutes: { value: 90 },
    });
    // The service's normalization: a substitute who did not come on is unused, 0 minutes.
    expect(lineups.home[11]).toEqual({
      player: { id: "sofascore-player-200", name: "Suplente Local", position: "midfielder" },
      shirtNumber: 20, role: "substitute", participation: "unused", minutes: { value: 0 },
    });
    expect(lineups.away[11]).toMatchObject({ role: "substitute", participation: "confirmed", minutes: { value: 22 } });
  });

  it("never guesses a missing role, shirt number or confirmation", async () => {
    const { lineups } = (await detail(lineupsBody("stored", {
      home: { players: [lineupEntry(1, "Sin Rol", { substitute: undefined, shirtNumber: null, statistics: undefined })] },
    })))!;
    if (lineups.state !== "stored") throw new Error("expected stored line-ups");
    expect(lineups.confirmed).toBeNull();
    expect(lineups.home).toEqual([{
      player: { id: "sofascore-player-1", name: "Sin Rol", position: "midfielder" },
      shirtNumber: null, role: null, participation: "unknown", minutes: missing,
    }]);
    expect(lineups.away).toEqual([]);
  });

  it("follows the service, never the raw minutes: a call-up it leaves unknown stays unknown", async () => {
    const { lineups } = (await detail(lineupsBody("stored", { home: { players: [lineupEntry(1, "Sin Resolver", { statistics: { minutesPlayed: 90 } })] } },
      [{ player_id: 1, ...normalized("unknown", null, null, null) }])))!;
    if (lineups.state !== "stored") throw new Error("expected stored line-ups");
    expect(lineups.home[0]).toMatchObject({ participation: "unknown", minutes: missing });
  });

  it("distinguishes empty, not stored and a failed query, keeping the rest of the detail", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect((await detail(lineupsBody("empty", { confirmed: false, home: { players: [] }, away: { players: [] } })))!.lineups)
      .toEqual({ state: "empty", fetchedAt: "2026-10-21T12:07:31+00:00" });
    expect((await detail(lineupsBody("not_stored")))!.lineups).toEqual({ state: "not_stored", fetchedAt: null });
    for (const failing of [
      () => new Response("{}", { status: 500 }),
      () => new Response(JSON.stringify(lineupsBody("stored", { home: { players: [{ player: { name: "Sin id" } }] } }))),
      () => new Response(JSON.stringify(lineupsBody("stored", { confirmed: "sí", home: { players: [] } }))),
      () => new Response(JSON.stringify(lineupsBody("stored", { home: { players: [lineupEntry(1, "X", { substitute: "no" })] } }))),
      // An older service without the normalization.
      () => new Response(JSON.stringify({ ...lineupsBody(), normalized: undefined })),
    ]) {
      const result = (await detail(failing))!;
      expect(result.lineups).toEqual({ state: "error", fetchedAt: null });
      expect(result.statistics.state).toBe("stored");
      expect(result.match.home.name).toBe("CE Europa");
    }
  });
});

describe("local API adapter: sections read before a correction of the event", () => {
  const flagged = <T extends { meta: Record<string, unknown> }>(body: T, flags: Record<string, boolean>) =>
    ({ ...body, meta: { ...body.meta, ...flags } });
  const score = { predates_score_correction: true, predates_team_change: false };
  const teams = { predates_score_correction: false, predates_team_change: true };

  it("after a score correction: kept sections marked for review, incident goals withheld (never an empty list)", async () => {
    const result = (await detailSource(detailApi(matchBody(), flagged(statsBody(), score), flagged(lineupsBody(), score),
      flagged(incidentsBody(), score)))("sofascore-match-16599363"))!;
    expect(result.review).toEqual({ corrections: ["score"], sections: ["statistics", "lineups", "incidents"] });
    // Team stats and line-ups do not depend on the score: still served.
    expect(result.statistics.state).toBe("stored");
    expect(result.lineups.state).toBe("stored");
    if (result.incidents.state !== "stored") throw new Error("expected stored incidents");
    expect(result.incidents.goals).toBeNull();
    expect(result.incidents.substitutions).toHaveLength(1);
  });

  it("after a team change: the previous teams' sections are outdated, never served as the current match's", async () => {
    const result = (await detailSource(detailApi(matchBody(), flagged(statsBody(), teams), flagged(lineupsBody(), teams),
      flagged(incidentsBody(), teams)))("sofascore-match-16599363"))!;
    expect(result.review).toEqual({ corrections: ["teams"], sections: ["statistics", "lineups", "incidents"] });
    for (const section of [result.statistics, result.lineups, result.incidents]) {
      expect(section).toEqual({ state: "outdated", fetchedAt: expect.any(String) });
    }
  });

  it("without flags (re-read, or a service that does not send them) there is nothing to review", async () => {
    const clean = { predates_score_correction: false, predates_team_change: false };
    for (const [stats, lineups, incidents] of [[statsBody(), lineupsBody(), incidentsBody()],
      [flagged(statsBody(), clean), flagged(lineupsBody(), clean), flagged(incidentsBody(), clean)]]) {
      const result = (await detailSource(detailApi(matchBody(), stats, lineups, incidents))("sofascore-match-16599363"))!;
      expect(result.review).toBeUndefined();
      if (result.incidents.state !== "stored") throw new Error("expected stored incidents");
      expect(result.incidents.goals).toHaveLength(2);
    }
  });

  it("takes a flag that is not a boolean as an unusable section, never as current data", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const result = (await detailSource(detailApi(matchBody(), flagged(statsBody(), { predates_team_change: "yes" } as never)))("sofascore-match-16599363"))!;
    expect(result.statistics).toEqual({ state: "error", fetchedAt: null });
    expect(result.review).toBeUndefined();
  });
});

describe("local API adapter: real incidents", () => {
  const detail = (incidents: unknown | (() => Response)) =>
    detailSource(detailApi(matchBody(), statsBody(), lineupsBody(), incidents))("sofascore-match-16599363");

  it("keeps only goals (with their kind and named assistant) and substitutions, as recorded", async () => {
    const { incidents } = (await detail(incidentsBody()))!;
    if (incidents.state !== "stored") throw new Error("expected stored incidents");
    expect(incidents.fetchedAt).toBe("2026-10-21T12:07:31+00:00");
    expect(incidents.goals).toEqual([
      { kind: "regular", minute: 77, addedTime: null,
        scorer: { id: "sofascore-player-966303", name: "Moha Keita", position: null },
        assist: { id: "sofascore-player-1545211", name: "David Serrano", position: null } },
      { kind: "penalty", minute: 88, addedTime: null,
        scorer: { id: "sofascore-player-942663", name: "Marco Siverio Toro", position: null }, assist: null },
    ]);
    // Cards, VAR, periods and missed penalties are not goals nor substitutions.
    expect(incidents.substitutions).toEqual([{ minute: 90, addedTime: 2,
      playerIn: { id: "sofascore-player-1594459", name: "Guillem Cardelus", position: null },
      playerOut: { id: "sofascore-player-857907", name: "Gori Gracia", position: null } }]);
  });

  it("tells own goals and unknown kinds apart, and leaves shoot-out kicks out", async () => {
    const { incidents } = (await detail(incidentsBody("stored", [
      { player: { id: 1, name: "Propia" }, assist1: { id: 2, name: "Nadie" }, time: 10, incidentClass: "ownGoal", incidentType: "goal" },
      { player: { id: 3, name: "Rara" }, time: 20, incidentClass: "otraCosa", incidentType: "goal" },
      { player: { id: 4, name: "Tanda" }, incidentClass: "scored", incidentType: "penaltyShootout" },
      { player: null, time: 30, incidentClass: "regular", incidentType: "goal" },
    ])))!;
    if (incidents.state !== "stored") throw new Error("expected stored incidents");
    expect(incidents.goals!.map((g) => [g.kind, g.scorer?.name ?? null, g.assist?.name ?? null])).toEqual([
      ["ownGoal", "Propia", "Nadie"], ["unknown", "Rara", null], ["regular", null, null],
    ]);
  });

  it("distinguishes empty, not stored and a failed query, keeping line-ups and stats", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect((await detail(incidentsBody("empty", [])))!.incidents).toEqual({ state: "empty", fetchedAt: "2026-10-21T12:07:31+00:00" });
    expect((await detail(incidentsBody("not_stored")))!.incidents).toEqual({ state: "not_stored", fetchedAt: null });
    for (const failing of [
      () => new Response("{}", { status: 500 }),
      () => new Response(JSON.stringify(incidentsBody("stored", [{ player: { id: "x" }, incidentType: "goal" }]))),
      () => new Response(JSON.stringify({ data: { incidents: "no" }, meta: { availability: "stored" } })),
    ]) {
      const result = (await detail(failing))!;
      expect(result.incidents).toEqual({ state: "error", fetchedAt: null });
      expect(result.lineups.state).toBe("stored");
      expect(result.statistics.state).toBe("stored");
    }
  });
});

/**
 * `GET /api/v1/teams/2841/performance` of the local API (trimmed): two finished
 * matches of Group 2 seen from Real Jaén, aggregated by the service.
 */
const perfMatch = (id: number, home: number, away: number, homeScore: number | boolean | null, awayScore: number | null, round: number) =>
  fixture({ match_id: id, home_team_id: home, away_team_id: away, home_score: homeScore, away_score: awayScore, round,
    kickoff_utc: `2026-09-0${round}T17:00:00+00:00` });
/** `output` of the aggregate for those two matches (3-0 and 0-2): goals from the scores, xG only in one of them. */
const average = (value: number | null, matches: number, of: number) => ({ value, matches, of });
const averages = (of: number, overrides: Record<string, unknown> = {}) => ({
  ...Object.fromEntries(["goals", "xg", "shots", "shotsOnTarget", "goalsAgainst", "xgAgainst", "shotsAgainst", "shotsOnTargetAgainst", "possession", "corners"].map((key) => [key, average(null, 0, of)])),
  ...overrides,
});
const OUTPUT = {
  season: averages(2, { goals: average(1.5, 2, 2), goalsAgainst: average(1, 2, 2), xg: average(1.25, 1, 2), possession: average(55.5, 2, 2) }),
  last3: { match_ids: [1, 2], values: averages(2, { goals: average(1.5, 2, 2), goalsAgainst: average(1, 2, 2), xg: average(1.25, 1, 2), possession: average(55.5, 2, 2) }) },
  undated: 0,
};
const performanceBody = (overrides: Record<string, unknown> = {}, results?: unknown[]) => ({
  data: { team_id: 2841, competitions: [{
    name: "Primera Federación, Grupo 2", season: "26/27", unique_tournament_id: 17073, tournament_id: 94191, season_id: 97382,
    matches: { in_scope: 4, finished: 3, pending: 1, counted: 2, finished_without_valid_score: 1, excluded_by_version_conflict: 0, unknown_status: 0 },
    record: { played: 2, wins: 1, draws: 0, losses: 1, goals_for: 3, goals_against: 2, goal_difference: 1 },
    venues: { home: { wins: 0, draws: 0, losses: 1 }, away: { wins: 1, draws: 0, losses: 0 } },
    output: OUTPUT,
    results: results ?? [
      { match: perfMatch(1, 135672, 2841, 0, 3, 1), side: "away", opponent_team_id: 135672, goals_for: 3, goals_against: 0,
        xg_for: 1.25, xg_against: null, result: "win" },
      { match: perfMatch(2, 2841, 24322, 0, 2, 2), side: "home", opponent_team_id: 24322, goals_for: 0, goals_against: 2,
        xg_for: null, xg_against: null, result: "loss" },
    ],
    ...overrides,
  }] },
  meta: { note: "Resultados de los partidos finalizados observados…" },
});
const performanceBodyWithFirstResult = (overrides: Record<string, unknown>) => {
  const results = performanceBody().data.competitions[0].results as Record<string, unknown>[];
  return performanceBody({}, [{ ...results[0], ...overrides }, results[1]]);
};
const performanceSource = (fetch: typeof globalThis.fetch) =>
  createLocalApiTeamPerformance({ baseUrl: "http://127.0.0.1:8001", fetch });

describe("local API adapter: team performance", () => {
  it("adapts the service's aggregate per scope competition, with our scope id, rival names and the analysable teams", async () => {
    const fetch = teamApi(null);
    const api = vi.fn(async (input: RequestInfo | URL) =>
      String(input).endsWith("/teams/2841/performance") ? new Response(JSON.stringify(performanceBody())) : fetch(input));
    const result = await performanceSource(api)("team-real-jaen");
    expect(urls(api as unknown as ReturnType<typeof teamApi>).slice(0, 2)).toEqual([SCOPE_URL, "http://127.0.0.1:8001/api/v1/teams/2841/performance"]);
    const [competition] = result!.competitions;
    expect(competition.id).toBe("sofascore-scope-94191-97382");
    expect([competition.name, competition.season]).toEqual(["Primera Federación, Grupo 2", "26/27"]);
    expect(competition.matches).toEqual({ inScope: 4, finished: 3, pending: 1, counted: 2, finishedWithoutValidScore: 1, excludedByVersionConflict: 0, unknownStatus: 0, replacedLocally: 0 });
    // Events replaced by a local decision are counted apart from the current matches.
    const replacedApi = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/teams/2841/performance")
      ? new Response(JSON.stringify(performanceBody({ matches: { in_scope: 4, finished: 3, pending: 1, counted: 2, finished_without_valid_score: 1,
        excluded_by_version_conflict: 0, unknown_status: 0, replaced_locally: 1 } }))) : fetch(input));
    expect((await performanceSource(replacedApi)("team-real-jaen"))!.competitions[0].matches.replacedLocally).toBe(1);
    expect(competition.record).toEqual({ played: 2, wins: 1, draws: 0, losses: 1, goalsFor: 3, goalsAgainst: 2, goalDifference: 1 });
    expect(competition.venues).toEqual({ home: { win: 0, draw: 0, loss: 1 }, away: { win: 1, draw: 0, loss: 0 } });
    expect(competition.results.map((r) => [r.match.id, r.side, r.result, r.goalsFor, r.goalsAgainst, r.xgFor, r.xgAgainst, r.match.home.name])).toEqual([
      ["sofascore-match-1", "away", "win", 3, 0, 1.25, null, "CE Europa"],
      ["sofascore-match-2", "home", "loss", 0, 2, null, null, "Real Jaén"],
    ]);
    expect(result!.teams).toEqual([{ id: "team-real-jaen", name: "Real Jaén" }]);
    // Averages with their coverage; absent when no match has the value, never 0.
    const output = competition.output!;
    expect(output.season.xg).toEqual({ value: 1.25, coverage: "partial", matches: 1, of: 2 });
    expect(output.season.possession).toEqual({ value: 55.5, coverage: "complete", matches: 2, of: 2 });
    expect(output.season.corners).toEqual({ value: null, coverage: "none", reason: "not_available", matches: 0, of: 2 });
    expect(output.last3.matchIds).toEqual(["sofascore-match-1", "sofascore-match-2"]);
    expect(output.undated).toBe(0);
    expect(result!.scope).toMatch(/No es necesariamente la temporada completa/);
    expect(result!.source).toEqual({ synthetic: false, label: "Datos reales" });
  });

  it("keeps missing individual xG as null and rejects values outside the finite non-negative domain", async () => {
    const fetch = teamApi(null);
    const body = performanceBodyWithFirstResult({ xg_for: undefined, xg_against: null });
    const response = vi.fn(async (input: RequestInfo | URL) =>
      String(input).endsWith("/performance") ? new Response(JSON.stringify(body)) : fetch(input));
    const [competition] = (await performanceSource(response)("team-real-jaen"))!.competitions;
    expect([competition.results[0].xgFor, competition.results[0].xgAgainst]).toEqual([null, null]);

    for (const invalidValue of [-0.01, true, "0.4"]) {
      const invalidBody = performanceBodyWithFirstResult({ xg_for: invalidValue });
      const invalidResponse = vi.fn(async (input: RequestInfo | URL) =>
        String(input).endsWith("/performance") ? new Response(JSON.stringify(invalidBody)) : fetch(input));
      await expect(performanceSource(invalidResponse)("team-real-jaen")).rejects.toBeInstanceOf(UpstreamUnavailableError);
    }
  });

  it("has nothing for a team without correspondence (no call) or one the service does not know", async () => {
    const fetch = answer({ error: "Entidad no encontrada" }, 404);
    expect(await performanceSource(fetch)("team-montealto")).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
    expect(await performanceSource(fetch)("team-real-jaen")).toBeNull();
  });

  it.each([
    ["a competition outside the declared scope", performanceBody({ tournament_id: 150, season_id: 104108 })],
    ["another group of the same league", performanceBody({ tournament_id: 94190 })],
    ["a boolean count", performanceBody({ matches: { in_scope: 4, finished: 3, pending: 1, counted: true, finished_without_valid_score: 1, excluded_by_version_conflict: 0, unknown_status: 0 } })],
    ["a negative goal count", performanceBody({ record: { played: 2, wins: 1, draws: 0, losses: 1, goals_for: -3, goals_against: 2, goal_difference: -5 } })],
    ["a wrong goal difference", performanceBody({ record: { played: 2, wins: 1, draws: 0, losses: 1, goals_for: 3, goals_against: 2, goal_difference: 4 } })],
    ["states that do not add up", performanceBody({ matches: { in_scope: 9, finished: 3, pending: 1, counted: 2, finished_without_valid_score: 1, excluded_by_version_conflict: 0, unknown_status: 0 } })],
    ["a boolean count of replaced events", performanceBody({ matches: { in_scope: 4, finished: 3, pending: 1, counted: 2, finished_without_valid_score: 1, excluded_by_version_conflict: 0, unknown_status: 0, replaced_locally: true } })],
    ["a record that does not match its results", performanceBody({ record: { played: 2, wins: 2, draws: 0, losses: 0, goals_for: 3, goals_against: 2, goal_difference: 1 } })],
    ["venues that do not match the results", performanceBody({ venues: { home: { wins: 1, draws: 0, losses: 0 }, away: { wins: 0, draws: 0, losses: 1 } } })],
    ["a result from another competition", performanceBody({}, [
      { match: { ...perfMatch(1, 135672, 2841, 0, 3, 1), tournament_id: 150 }, side: "away", goals_for: 3, goals_against: 0, result: "win" },
      { match: perfMatch(2, 2841, 24322, 0, 2, 2), side: "home", goals_for: 0, goals_against: 2, result: "loss" }])],
    ["a boolean score", performanceBody({}, [
      { match: perfMatch(1, 135672, 2841, true, 3, 1), side: "away", goals_for: 3, goals_against: 1, result: "win" },
      { match: perfMatch(2, 2841, 24322, 0, 2, 2), side: "home", goals_for: 0, goals_against: 2, result: "loss" }])],
    ["goals that do not match the score", performanceBody({}, [
      { match: perfMatch(1, 135672, 2841, 0, 3, 1), side: "away", goals_for: 0, goals_against: 3, result: "win" },
      { match: perfMatch(2, 2841, 24322, 0, 2, 2), side: "home", goals_for: 0, goals_against: 2, result: "loss" }])],
    ["a result that does not match the goals", performanceBody({}, [
      { match: perfMatch(1, 135672, 2841, 0, 3, 1), side: "away", goals_for: 3, goals_against: 0, result: "draw" },
      { match: perfMatch(2, 2841, 24322, 0, 2, 2), side: "home", goals_for: 0, goals_against: 2, result: "loss" }])],
    ["a pending match among the results", performanceBody({}, [
      { match: { ...perfMatch(1, 135672, 2841, null, null, 1), status: "notstarted" }, side: "away", goals_for: 0, goals_against: 0, result: "draw" },
      { match: perfMatch(2, 2841, 24322, 0, 2, 2), side: "home", goals_for: 0, goals_against: 2, result: "loss" }])],
    ["a side the team does not play", performanceBody({}, [
      { match: perfMatch(1, 135672, 2841, 0, 3, 1), side: "home", goals_for: 0, goals_against: 3, result: "loss" },
      { match: perfMatch(2, 2841, 24322, 0, 2, 2), side: "home", goals_for: 0, goals_against: 2, result: "loss" }])],
    ["another team's aggregate", { data: { team_id: 7, competitions: [] } }],
  ])("rejects %s: the whole answer is invalid, never shown", async (_case, body) => {
    const names = teamApi(null);
    const fetch = vi.fn(async (input: RequestInfo | URL) =>
      String(input).endsWith("/performance") ? new Response(JSON.stringify(body)) : names(input));
    await expect(performanceSource(fetch)("team-real-jaen")).rejects.toBeInstanceOf(UpstreamUnavailableError);
  });

  it.each([
    ["no output (an older service)", undefined],
    ["a boolean average", { ...OUTPUT, season: { ...OUTPUT.season, xg: average(true as unknown as number, 1, 2) } }],
    ["an infinite or negative average", { ...OUTPUT, season: { ...OUTPUT.season, xg: average(-1, 1, 2) } }],
    ["a possession over 100 %", { ...OUTPUT, season: { ...OUTPUT.season, possession: average(101, 2, 2) } }],
    ["a zero average without matches", { ...OUTPUT, season: { ...OUTPUT.season, corners: average(0, 0, 2) } }],
    ["more matches with data than counted", { ...OUTPUT, season: { ...OUTPUT.season, xg: average(1, 3, 2) } }],
    ["season averages over other matches", { ...OUTPUT, season: averages(3) }],
    ["a «Últimos 3» that is not the most recent", { ...OUTPUT, last3: { ...OUTPUT.last3, match_ids: [2, 1] } }],
    ["goals that do not match the scores", { ...OUTPUT, season: { ...OUTPUT.season, goals: average(2, 2, 2) } }],
    ["a missing indicator", { ...OUTPUT, season: { goals: average(1.5, 2, 2) } }],
  ])("keeps the record but leaves the figures out with %s", async (_case, output) => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const names = teamApi(null);
    const fetch = vi.fn(async (input: RequestInfo | URL) =>
      String(input).endsWith("/performance") ? new Response(JSON.stringify(performanceBody({ output }))) : names(input));
    const [competition] = (await performanceSource(fetch)("team-real-jaen"))!.competitions;
    expect(competition.output).toBeNull();
    expect(competition.record.played).toBe(2);
    expect(console.error).toHaveBeenCalled();
  });

  it("leaves an undated match out of «Últimos 3», as the service does", async () => {
    const names = teamApi(null);
    const undated = performanceBody({ output: { ...OUTPUT, last3: { ...OUTPUT.last3, match_ids: [1] , values: averages(1, { goals: average(3, 1, 1), goalsAgainst: average(0, 1, 1) }) }, undated: 1 } }, [
      { match: perfMatch(1, 135672, 2841, 0, 3, 1), side: "away", goals_for: 3, goals_against: 0, result: "win" },
      { match: { ...perfMatch(2, 2841, 24322, 0, 2, 2), kickoff_utc: null }, side: "home", goals_for: 0, goals_against: 2, result: "loss" },
    ]);
    const fetch = vi.fn(async (input: RequestInfo | URL) =>
      String(input).endsWith("/performance") ? new Response(JSON.stringify(undated)) : names(input));
    const [competition] = (await performanceSource(fetch)("team-real-jaen"))!.competitions;
    expect(competition.output!.last3.matchIds).toEqual(["sofascore-match-1"]);
    expect(competition.output!.undated).toBe(1);
  });

  it("keeps zero matches apart from a failure: an empty record is an answer, a failing service is not", async () => {
    const names = teamApi(null);
    const empty = performanceBody({
      matches: { in_scope: 0, finished: 0, pending: 0, counted: 0, finished_without_valid_score: 0, excluded_by_version_conflict: 0, unknown_status: 0 },
      record: { played: 0, wins: 0, draws: 0, losses: 0, goals_for: 0, goals_against: 0, goal_difference: 0 },
      venues: { home: { wins: 0, draws: 0, losses: 0 }, away: { wins: 0, draws: 0, losses: 0 } },
      output: { season: averages(0), last3: { match_ids: [], values: averages(0) }, undated: 0 },
    }, []);
    const fetch = vi.fn(async (input: RequestInfo | URL) =>
      String(input).endsWith("/performance") ? new Response(JSON.stringify(empty)) : names(input));
    expect((await performanceSource(fetch)("team-real-jaen"))!.competitions[0].record.played).toBe(0);
    await expect(performanceSource(answer({}, 500))("team-real-jaen")).rejects.toThrow("HTTP 500");
  });
});


/** `GET /api/v1/teams/2841/player-stats` for a starter, a used substitute and one called up without confirmed participation. */
const figure = (value: unknown, matches: number, of: number) => ({ value, matches, of });
const squadRow = (overrides: Record<string, unknown> = {}) => ({
  player_id: 942663, name: "Marco Siverio Toro", usual_position: "F", height_cm: 184, preferred_foot: "Right", last_shirt_number: 9,
  call_ups: 3, starts: figure(3, 3, 3), appearances: figure(3, 3, 3), minutes: figure(250, 3, 3), goals: figure(2, 2, 3),
  assists: figure(0, 3, 3), ...overrides,
});
const SQUAD_ROWS = [
  squadRow(),
  squadRow({ player_id: 1982588, name: "Francisco Gallego", usual_position: "M", height_cm: null, preferred_foot: null, last_shirt_number: 7,
    call_ups: 2, starts: figure(0, 2, 2), appearances: figure(1, 1, 2), minutes: figure(13, 1, 2), goals: figure(null, 0, 2), assists: figure(0, 1, 2) }),
  squadRow({ player_id: 1086428, name: "Salvi", usual_position: "G", height_cm: 190, preferred_foot: null, last_shirt_number: 13,
    call_ups: 3, starts: figure(0, 3, 3), appearances: figure(0, 3, 3), minutes: figure(0, 3, 3), goals: figure(0, 3, 3), assists: figure(0, 3, 3) }),
];
const squadBody = (overrides: Record<string, unknown> = {}, players: unknown[] = SQUAD_ROWS) => ({
  data: { team_id: 2841, competitions: [{
    name: "Primera Federación, Grupo 2", season: "26/27", unique_tournament_id: 17073, tournament_id: 94191, season_id: 97382,
    matches: { counted: 3, with_call_ups: 3 },
    call_ups: { total: 8, played: 4, unused: 3, unknown: 1, contradictions: 0, mismatches: 0, team_warnings: 0, duplicate_records: 0, discarded_records: 0 },
    players, ...overrides,
  }] },
  meta: { note: "Acumulados de los jugadores observados…" },
});
const squadSource = (fetch: typeof globalThis.fetch) => createLocalApiObservedSquad({ baseUrl: "http://127.0.0.1:8001", fetch });
const squadApi = (body: unknown) => vi.fn(async (input: RequestInfo | URL) =>
  scoped(input) ?? new Response(JSON.stringify(body)));

describe("local API adapter: observed squad", () => {
  it("adapts the service's figures per scope competition, with personal data in the same answer", async () => {
    const fetch = squadApi(squadBody());
    const result = await squadSource(fetch)("team-real-jaen");
    expect(fetch.mock.calls.map((call) => String(call[0]))).toEqual([SCOPE_URL, "http://127.0.0.1:8001/api/v1/teams/2841/player-stats"]);
    const [competition] = result!.competitions;
    expect([competition.id, competition.name, competition.season]).toEqual(["sofascore-scope-94191-97382", "Primera Federación, Grupo 2", "26/27"]);
    expect(competition.matches).toEqual({ counted: 3, withCallUps: 3 });
    expect(competition.callUps).toEqual({ total: 8, played: 4, unused: 3, unknown: 1, contradictions: 0, mismatches: 0, teamWarnings: 0,
      duplicateRecords: 0, discardedRecords: 0 });
    const [starter, substitute, unused] = competition.players;
    expect(starter).toEqual({
      player: { id: "sofascore-player-942663", name: "Marco Siverio Toro", position: "forward" }, heightCm: 184, preferredFoot: "right",
      lastShirtNumber: 9, callUps: 3, stats: {
        appearances: { value: 3, coverage: "complete", matches: 3, of: 3 }, starts: { value: 3, coverage: "complete", matches: 3, of: 3 },
        minutes: { value: 250, coverage: "complete", matches: 3, of: 3 }, goals: { value: 2, coverage: "partial", matches: 2, of: 3 },
        assists: { value: 0, coverage: "complete", matches: 3, of: 3 },
      },
    });
    // Played 1; the other call-up could not be resolved: a partial count, never «played 2» nor «played 1 of 2».
    expect(substitute.stats.appearances).toEqual({ value: 1, coverage: "partial", matches: 1, of: 2 });
    expect(substitute.stats.goals).toEqual({ value: null, coverage: "none", reason: "not_available", matches: 0, of: 2 });
    // Unused in all 3 call-ups: 0 appearances and 0 minutes, complete.
    expect(unused.stats.appearances).toEqual({ value: 0, coverage: "complete", matches: 3, of: 3 });
    expect(unused.stats.minutes).toEqual({ value: 0, coverage: "complete", matches: 3, of: 3 });
    expect(result!.scope).toMatch(/No garantiza toda la temporada ni es una plantilla completa o necesariamente actual/);
    expect(result!.source).toEqual({ synthetic: false, label: "Datos reales" });
  });

  it("answers null without explicit correspondence or for a team the source does not know", async () => {
    const fetch = squadApi(squadBody());
    expect(await squadSource(fetch)("team-montealto")).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
    expect(await squadSource(vi.fn(async (input: RequestInfo | URL) => scoped(input) ?? new Response("{}", { status: 404 })))("team-real-jaen")).toBeNull();
  });

  it.each([
    ["a competition outside the declared scope", squadBody({ tournament_id: 94190 })],
    ["a boolean figure", squadBody({}, [squadRow({ goals: figure(true, 2, 3) })])],
    ["a decimal minute total", squadBody({}, [squadRow({ minutes: figure(250.5, 3, 3) })])],
    ["a negative figure", squadBody({}, [squadRow({ assists: figure(-1, 3, 3) })])],
    ["a zero without call-ups that have it", squadBody({}, [squadRow({ goals: figure(0, 0, 3) })])],
    ["a figure over other call-ups", squadBody({}, [squadRow({ minutes: figure(250, 3, 4) })])],
    ["more call-ups with data than call-ups", squadBody({}, [squadRow({ minutes: figure(250, 4, 3) })])],
    ["more starts than flags", squadBody({}, [squadRow({ starts: figure(3, 2, 3) })])],
    ["more appearances than known participations", squadBody({}, [squadRow({ appearances: figure(3, 2, 3) })])],
    ["more call-ups than matches with call-ups", squadBody({}, [squadRow({ call_ups: 4 })])],
    ["a repeated player", squadBody({}, [squadRow(), squadRow()])],
    ["a player without name", squadBody({}, [squadRow({ name: null })])],
    ["a boolean height", squadBody({}, [squadRow({ height_cm: true })])],
    ["a boolean shirt number", squadBody({}, [squadRow({ last_shirt_number: true })])],
    ["call-up totals that do not add up", squadBody({ call_ups: { total: 9, played: 4, unused: 3, unknown: 2, contradictions: 0, mismatches: 0, team_warnings: 0, duplicate_records: 0, discarded_records: 0 } })],
    ["played totals that do not add up", squadBody({ call_ups: { total: 8, played: 5, unused: 2, unknown: 1, contradictions: 0, mismatches: 0, team_warnings: 0, duplicate_records: 0, discarded_records: 0 } })],
    ["unused totals that do not add up", squadBody({ call_ups: { total: 8, played: 4, unused: 2, unknown: 2, contradictions: 0, mismatches: 0, team_warnings: 0, duplicate_records: 0, discarded_records: 0 } })],
    ["more contradictions than unresolved call-ups", squadBody({ call_ups: { ...squadBody().data.competitions[0].call_ups, contradictions: 2 } })],
    ["more matches with call-ups than counted", squadBody({ matches: { counted: 2, with_call_ups: 3 } })],
    ["another team's answer", { ...squadBody(), data: { ...squadBody().data, team_id: 9001 } }],
  ])("refuses the whole answer with %s: no figure from a response that does not hold", async (_case, body) => {
    await expect(squadSource(squadApi(body))("team-real-jaen")).rejects.toThrow(UpstreamUnavailableError);
  });

  it("keeps an empty competition apart from a failure", async () => {
    const empty = squadBody({ matches: { counted: 0, with_call_ups: 0 },
      call_ups: { total: 0, played: 0, unused: 0, unknown: 0, contradictions: 0, mismatches: 0, team_warnings: 0, duplicate_records: 0, discarded_records: 0 } }, []);
    expect((await squadSource(squadApi(empty))("team-real-jaen"))!.competitions[0].players).toEqual([]);
    await expect(squadSource(answer({}, 500))("team-real-jaen")).rejects.toThrow("HTTP 500");
  });
});

describe("local API adapter: teams that can be analysed", () => {
  /** The scope with the given teams declared as having an export of their own (`undefined`: an older service). */
  const declaring = (teams: number[] | undefined, inner: typeof globalThis.fetch) => vi.fn(async (input: RequestInfo | URL) =>
    String(input).endsWith("/api/v1/scope")
      ? new Response(JSON.stringify({ data: { ...SCOPE_BODY.data,
        teams_with_own_export: teams === undefined ? undefined : teams.map((team_id) => ({ team_id, exports: [] })) } }))
      : inner(input));
  const names = teamApi(null, { ...NAMES, "24368": "UD Ibiza" });
  const service = (body: unknown) => vi.fn(async (input: RequestInfo | URL) =>
    /\/teams\/\d+\/performance$/.test(String(input)) ? new Response(JSON.stringify(body)) : names(input));

  it("lists only teams with explicit correspondence and an export of their own, the club first", async () => {
    const both = await performanceSource(declaring([2841, 24368], service(performanceBody())))("team-real-jaen");
    expect(both!.teams).toEqual([{ id: "team-real-jaen", name: "Real Jaén" }, { id: "sofascore-team-24368", name: "UD Ibiza" }]);
    // With correspondence but no export of its own declared, UD Ibiza is not offered.
    const onlyClub = await performanceSource(declaring([2841], service(performanceBody())))("team-real-jaen");
    expect(onlyClub!.teams.map((t) => t.id)).toEqual(["team-real-jaen"]);
  });

  it("never analyses a rival seen only in other teams' matches, nor anything when the service declares no team", async () => {
    const fetch = declaring([2841], service(performanceBody()));
    expect(await performanceSource(fetch)("sofascore-team-24368")).toBeNull();
    expect(urls(fetch as unknown as ReturnType<typeof teamApi>)).toEqual([SCOPE_URL]);
    expect(await performanceSource(declaring(undefined, service(performanceBody())))("team-real-jaen")).toBeNull();
    expect(await squadSource(declaring([2841], squadApi(squadBody())))("sofascore-team-24368")).toBeNull();
  });

  it("keeps a match whose versions contradict each other out of the record, and checks the counts add up", async () => {
    const body = performanceBody({ matches: { in_scope: 5, finished: 4, pending: 1, counted: 2, finished_without_valid_score: 1,
      excluded_by_version_conflict: 1, unknown_status: 0 } });
    const [competition] = (await performanceSource(declaring([2841], service(body)))("team-real-jaen"))!.competitions;
    expect(competition.matches.excludedByVersionConflict).toBe(1);
    const wrong = performanceBody({ matches: { in_scope: 5, finished: 4, pending: 1, counted: 3, finished_without_valid_score: 1,
      excluded_by_version_conflict: 1, unknown_status: 0 } });
    await expect(performanceSource(declaring([2841], service(wrong)))("team-real-jaen")).rejects.toThrow(UpstreamUnavailableError);
  });
});

describe("local API adapter: explicit team correspondence", () => {
  it("offers the 20 teams of Group 2 26/27 with their own export, the club first, each with the id the match lists already use", async () => {
    const group2 = [2841, 24368, 24322, 4489, 24323, 24326, 43751, 217210, 135672, 24329, 2840, 2877, 24265, 263407, 202074, 5069, 4487, 2815, 24363, 24338];
    const names = teamApi(null, Object.fromEntries(group2.map((id) => [String(id), id === 2841 ? "Real Jaén" : `Equipo ${id}`])));
    const fetch = vi.fn(async (input: RequestInfo | URL) =>
      String(input).endsWith("/api/v1/scope")
        ? new Response(JSON.stringify({ data: { ...SCOPE_BODY.data, teams_with_own_export: group2.map((team_id) => ({ team_id, exports: [] })) } }))
        : /\/teams\/\d+\/performance$/.test(String(input)) ? new Response(JSON.stringify(performanceBody())) : names(input));
    const { teams } = (await performanceSource(fetch)("team-real-jaen"))!;
    expect(teams).toHaveLength(20);
    expect(teams[0]).toEqual({ id: "team-real-jaen", name: "Real Jaén" });
    expect(teams.slice(1).map((t) => t.id)).toEqual(group2.slice(1).map((id) => `sofascore-team-${id}`));
  });
});
