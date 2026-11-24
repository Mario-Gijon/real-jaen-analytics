import { describe, expect, it } from "vitest";
import type { EnrichedComment, EnrichedParticipant, Stat } from "@/contract";
import { locationParameters } from "@/features/matches/event-video-location";
import { playerMatchEvents } from "@/features/players/player-match-events";

// Shapes and roles as the enrichment service returns them (rules-v1), from real events.
const SIVERIO = 942663, KEITA = 966303, POLO = 1010341, SERRANO = 1545211, ADRI = 268117;
const id = (source: number) => `sofascore-player-${source}`;

function participant(sourceRole: string, role: string | null, source: number | null, name: string | null = null): EnrichedParticipant {
  return {
    sourceRole, role, normalizerRole: null, sourceId: source, name,
    player: source === null ? null : { id: id(source), name: name ?? "Jugador", position: null },
  };
}
const scorer = (source: number | null, name?: string) => participant("player", "scorer", source, name);
const assistant = (source: number | null, name?: string) => participant("assist1", "assistant", source, name);

let nextId = 1;
function event(overrides: Partial<EnrichedComment> & { goal?: Record<string, string | null> } = {}): EnrichedComment {
  const { goal, ...rest } = overrides;
  const comment = nextId++;
  return {
    id: String(40000000 + comment), captureId: 7, position: 100 - comment, text: "¡Gol!", originalType: "scoreChange",
    minute: 30, periodName: "1ST", isHome: true, score: { home: 1, away: 0 }, category: "goal",
    attributes: { goal_status: "valid", goal_kind: "ordinary", ...goal }, participants: [], ...rest,
  };
}
const goal = (participants: EnrichedParticipant[], overrides: Partial<EnrichedComment> = {}) => event({ participants, ...overrides });
const penalty = (source: number, overrides: Partial<EnrichedComment> = {}) => event({
  originalType: "penaltyScored", score: null, participants: [participant("player", "penalty_scorer", source, "Adri")], ...overrides,
});
const count = (value: number): Stat => ({ value });
const missing: Stat = { value: null, reason: "not_available" };
const kinds = (items: EnrichedComment[], player: number, assists: Stat = count(0)) =>
  playerMatchEvents(items, id(player), assists).events.map((row) => [row.kind, row.item.id]);

describe("playerMatchEvents", () => {
  it("1. returns a valid ordinary goal of the player, keeping the original event", () => {
    const item = goal([scorer(SIVERIO, "Marco Siverio Toro")], { minute: 63, periodName: "2ND" });
    const { events, omittedAssists } = playerMatchEvents([item], id(SIVERIO), count(0));
    expect(events).toEqual([{ key: `7-string-${item.id}:goal`, kind: "goal", minute: 63, period: "second", item, related: null }]);
    expect(omittedAssists).toBe(0);
  });

  it("2. returns a valid penalty goal as penalty_goal", () => {
    const item = penalty(ADRI, { minute: 62, periodName: "2ND" });
    expect(playerMatchEvents([item], id(ADRI), count(0)).events).toEqual([
      expect.objectContaining({ kind: "penalty_goal", minute: 62, period: "second", item, related: null }),
    ]);
  });

  it("3. labels the scorer's assistant as narrative evidence, not as a confirmed assist", () => {
    const item = goal([scorer(KEITA, "Moha Keita"), assistant(SERRANO, "David Serrano")]);
    expect(playerMatchEvents([item], id(KEITA), count(0)).events[0].related)
      .toEqual({ id: id(SERRANO), name: "David Serrano", attribution: "narrative" });
  });

  it("4. gives a goal without an assistant no related player", () => {
    expect(playerMatchEvents([goal([scorer(SIVERIO)])], id(SIVERIO), missing).events[0])
      .toEqual(expect.objectContaining({ kind: "goal", related: null }));
  });

  it("5. shows an assist confirmed by the official count, with the scorer as related player", () => {
    const item = goal([scorer(SIVERIO, "Marco Siverio Toro"), assistant(KEITA, "Moha Keita")]);
    const { events, omittedAssists } = playerMatchEvents([item], id(KEITA), count(1));
    expect(events).toEqual([expect.objectContaining({ kind: "assist", key: `7-string-${item.id}:assist`,
      related: { id: id(SIVERIO), name: "Marco Siverio Toro", attribution: "confirmed" } })]);
    expect(omittedAssists).toBe(0);
  });

  it("6. omits an assist the official count does not confirm (Moha Keita, 16599928)", () => {
    const item = goal([scorer(POLO, "Peque Polo"), assistant(KEITA, "Moha Keita")], { minute: 32 });
    expect(playerMatchEvents([item], id(KEITA), count(0))).toEqual({ events: [], omittedAssists: 1, unidentifiedOfficialAssists: 0 });
  });

  it("7. never picks some candidates to fit a different official count", () => {
    const items = [goal([scorer(SIVERIO), assistant(KEITA)]), goal([scorer(POLO), assistant(KEITA)])];
    expect(playerMatchEvents(items, id(KEITA), count(1))).toEqual({ events: [], omittedAssists: 2, unidentifiedOfficialAssists: 0 });
    expect(playerMatchEvents(items, id(KEITA), count(3))).toEqual({ events: [], omittedAssists: 2, unidentifiedOfficialAssists: 1 });
    expect(playerMatchEvents(items, id(KEITA), count(2)).events).toHaveLength(2);
  });

  it("8. cannot confirm assists without a usable official count, never reading it as 0", () => {
    const items = [goal([scorer(SIVERIO), assistant(KEITA)])];
    for (const stat of [missing, { value: null, reason: "inconsistent" } as Stat, null, undefined, count(-1), count(0.5), count(Number.NaN)]) {
      expect(playerMatchEvents(items, id(KEITA), stat), JSON.stringify(stat)).toEqual({ events: [], omittedAssists: 1, unidentifiedOfficialAssists: 0 });
    }
    // No candidate: nothing is hidden, whatever the count.
    expect(playerMatchEvents([], id(KEITA), missing)).toEqual({ events: [], omittedAssists: 0, unidentifiedOfficialAssists: 0 });
  });

  it("reports official assists when the narrative has no identifiable matching events", () => {
    expect(playerMatchEvents([], id(KEITA), count(2))).toEqual({
      events: [], omittedAssists: 0, unidentifiedOfficialAssists: 2,
    });
    const noId = goal([scorer(SIVERIO), assistant(null, "Moha Keita")]);
    expect(playerMatchEvents([noId], id(KEITA), count(1))).toEqual({
      events: [], omittedAssists: 0, unidentifiedOfficialAssists: 1,
    });
  });

  it("9. never credits an own goal (own_goal_author) as a goal", () => {
    const ownGoal = event({ goal: { goal_kind: "own_goal" }, text: "Autogol de Fran Carmona.",
      participants: [{ ...scorer(902092), role: "own_goal_author", normalizerRole: "scorer" }] });
    expect(kinds([ownGoal], 902092)).toEqual([]);
  });

  it("10. never credits the assistant of an own goal", () => {
    const ownGoal = event({ goal: { goal_kind: "own_goal" },
      participants: [{ ...scorer(1163260), role: "own_goal_author", normalizerRole: "scorer" }, assistant(904135)] });
    expect(playerMatchEvents([ownGoal], id(904135), count(1))).toEqual({ events: [], omittedAssists: 0, unidentifiedOfficialAssists: 1 });
    // Even if its kind were missing, the own-goal role alone excludes it.
    const unlabelled = { ...ownGoal, attributes: { goal_status: "valid", goal_kind: "ordinary" } };
    expect(playerMatchEvents([unlabelled], id(904135), count(1)).events).toEqual([]);
  });

  it("11. excludes disallowed goals and their assistants", () => {
    const cancelled = event({ originalType: "varCancelledGoal", category: "goal_review", score: null,
      goal: { goal_status: "disallowed", goal_kind: null },
      participants: [participant("player", "disallowed_scorer", 1634376), participant("assist1", "disallowed_goal_assistant", 1596558)] });
    expect(kinds([cancelled], 1634376)).toEqual([]);
    expect(playerMatchEvents([cancelled], id(1596558), count(1))).toEqual({ events: [], omittedAssists: 0, unidentifiedOfficialAssists: 1 });
    // A goal whose status or kind is not exactly valid/ordinary is excluded too.
    const incomplete: EnrichedComment["attributes"][] = [{ goal_status: null, goal_kind: "ordinary" }, { goal_status: "valid", goal_kind: null }, {}];
    for (const attributes of incomplete) {
      expect(kinds([event({ attributes, participants: [scorer(SIVERIO)] })], SIVERIO)).toEqual([]);
    }
  });

  it("12. ignores VAR decisions, even about a goal", () => {
    const decision = event({ originalType: "videoAssistantReferee", category: "var", attributes: {},
      text: "Decisión del VAR: Gol Real Jaén", participants: [participant("player", null, 848589)] });
    expect(kinds([decision], 848589)).toEqual([]);
  });

  it("13. ignores participants without a valid structured id", () => {
    expect(kinds([goal([scorer(null, "Marco Siverio Toro")])], SIVERIO)).toEqual([]);
    const badId = { ...scorer(SIVERIO), player: { id: "Marco Siverio Toro", name: "Marco Siverio Toro", position: null } };
    expect(playerMatchEvents([goal([badId])], "Marco Siverio Toro", count(0)).events).toEqual([]);
    // An assistant without id is no related player, but the goal stays.
    expect(playerMatchEvents([goal([scorer(KEITA), assistant(null, "David Serrano")])], id(KEITA), count(0)).events[0].related).toBeNull();
  });

  it("14. attributes nothing to another player, and never by name", () => {
    const items = [goal([scorer(SIVERIO, "Moha Keita"), assistant(SERRANO, "Moha Keita")])];
    expect(playerMatchEvents(items, id(KEITA), count(1))).toEqual({ events: [], omittedAssists: 0, unidentifiedOfficialAssists: 1 });
  });

  it("15. reads each role of a multi-participant event, and gives one row when the player has two roles", () => {
    const item = goal([scorer(KEITA), assistant(SERRANO), participant("playerIn", null, POLO)]);
    expect(kinds([item], KEITA)).toEqual([["goal", item.id]]);
    expect(kinds([item], SERRANO, count(1))).toEqual([["assist", item.id]]);
    expect(kinds([item], POLO, count(1))).toEqual([]);
    // Scorer and assistant at once: the goal wins; no assist candidate is counted.
    const both = goal([scorer(KEITA), assistant(KEITA)]);
    expect(playerMatchEvents([both], id(KEITA), count(0))).toEqual({ events: [expect.objectContaining({ kind: "goal" })], omittedAssists: 0, unidentifiedOfficialAssists: 0 });
    // Two scorer slots, or two assistants: not attributable.
    expect(kinds([goal([scorer(KEITA), scorer(SIVERIO)])], KEITA)).toEqual([]);
    expect(playerMatchEvents([goal([scorer(SIVERIO), assistant(KEITA), assistant(SERRANO)])], id(KEITA), count(1)))
      .toEqual({ events: [], omittedAssists: 1, unidentifiedOfficialAssists: 0 });
  });

  it("16. keeps one row per source event: a repeated event id is a duplicate, the same minute is not", () => {
    const first = goal([scorer(SIVERIO)], { minute: 63 });
    const repeated = { ...first, text: "Otra narración" };
    const sameMinute = goal([scorer(SIVERIO)], { minute: 63 });
    const otherCapture = { ...first, captureId: 8 };
    expect(kinds([first, repeated, sameMinute], SIVERIO)).toEqual([["goal", first.id], ["goal", sameMinute.id]]);
    expect(playerMatchEvents([first, otherCapture], id(SIVERIO), count(0)).events).toHaveLength(2);
    expect(playerMatchEvents([{ ...first, id: 5 }, { ...first, id: "5" }], id(SIVERIO), count(0)).events).toHaveLength(2);
  });

  it("17. orders events by the source's match order, whatever the input order", () => {
    const early = goal([scorer(SIVERIO)], { minute: 1, position: 120 });
    const middle = goal([scorer(POLO), assistant(SIVERIO)], { minute: 40, position: 80 });
    const late = penalty(SIVERIO, { minute: 91, periodName: "2ND", position: 5 });
    expect(kinds([late, early, middle], SIVERIO, count(1))).toEqual([["goal", early.id], ["assist", middle.id], ["penalty_goal", late.id]]);
  });

  it("18. keeps stoppage minutes and their period, so first-half stoppage precedes the second half", () => {
    const stoppage = goal([scorer(SIVERIO)], { minute: 47, periodName: "1ST", position: 60 });
    const secondHalf = goal([scorer(SIVERIO)], { minute: 46, periodName: "2ND", position: 50 });
    const late = goal([scorer(SIVERIO)], { minute: 104, periodName: "2ND", position: 2 });
    const { events } = playerMatchEvents([late, secondHalf, stoppage], id(SIVERIO), count(0));
    expect(events.map((row) => [row.minute, row.period])).toEqual([[47, "first"], [46, "second"], [104, "second"]]);
    // The original event still yields the video location parameters (phase 3).
    expect(locationParameters(events[0].item)).toEqual({ period: "first", eventKind: "other", minute: "45+2" });
    expect(locationParameters(events[2].item)).toEqual({ period: "second", eventKind: "other", minute: "104" });
  });

  it("19. accepts a valid goal without score", () => {
    expect(kinds([goal([scorer(SIVERIO)], { score: null }), penalty(SIVERIO)], SIVERIO))
      .toEqual([["goal", expect.any(String)], ["penalty_goal", expect.any(String)]]);
  });

  it("20. never drops valid goals because of an assist discrepancy", () => {
    const keitaGoal = goal([scorer(KEITA, "Moha Keita")], { position: 90 });
    const assisted = goal([scorer(POLO, "Peque Polo"), assistant(KEITA, "Moha Keita")], { position: 80 });
    const { events, omittedAssists } = playerMatchEvents([assisted, keitaGoal], id(KEITA), count(0));
    expect(events.map((row) => row.kind)).toEqual(["goal"]);
    expect(omittedAssists).toBe(1);
  });

  it("ignores other event types and a penalty's unmapped assist slot", () => {
    const shot = event({ originalType: "shotSaved", category: "shot", attributes: {}, participants: [participant("player", "shooter", SIVERIO)] });
    const card = event({ originalType: "yellowCard", category: "discipline", attributes: {}, participants: [participant("player", "booked_player", SIVERIO)] });
    const awarded = event({ originalType: "penaltyAwarded", category: "penalty", attributes: {}, participants: [participant("player", "penalty_foul_recipient", SIVERIO)] });
    const scored = penalty(ADRI, { participants: [participant("player", "penalty_scorer", ADRI), participant("assist1", null, SIVERIO)] });
    expect(playerMatchEvents([shot, card, awarded, scored], id(SIVERIO), count(1))).toEqual({ events: [], omittedAssists: 0, unidentifiedOfficialAssists: 1 });
  });
});
