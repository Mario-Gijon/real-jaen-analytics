/**
 * Goals and assists of one player in one match, from the match's enriched
 * narrative events. Pure and React-free.
 *
 * Attribution uses structured data only: the participant's SofaScore player
 * id, the source slot (`sourceRole`) and the role assigned by the enrichment
 * service (`role`). Names and narrative text never attribute anything; the
 * enrichment's `goal_status` and `goal_kind` only exclude events.
 */
import type { EnrichedComment, EnrichedParticipant, Stat } from "@/contract";
import { periodFor } from "@/features/matches/event-video-location";

export type PlayerMatchEventKind = "goal" | "penalty_goal" | "assist";

/** The other participant of the event: the assistant of a goal, the scorer of an assist. */
export type RelatedPlayer = { id: string; name: string | null };

export type PlayerMatchEvent = {
  /** Stable: the source event (capture + comment id) and the kind. */
  key: string;
  kind: PlayerMatchEventKind;
  minute: number | null;
  period: "first" | "second" | null;
  /** The original enriched event, kept whole for video location (`locationParameters`). */
  item: EnrichedComment;
  related: RelatedPlayer | null;
};

export type PlayerMatchEventsResult = {
  /** In match order (the source's comment order, as the match timeline). */
  events: PlayerMatchEvent[];
  /** Candidate assists hidden because the official assist count does not confirm them. */
  omittedAssists: number;
};

const PLAYER_ID = /^sofascore-player-[1-9]\d*$/;

/** Same identity as the match timeline: one row per source comment of one capture. */
function sourceEventKey(item: EnrichedComment): string {
  return `${item.captureId ?? "capture"}-${typeof item.id}-${item.id}`;
}

function identified(participant: EnrichedParticipant): participant is EnrichedParticipant & { player: { id: string } } {
  return participant.player !== null && PLAYER_ID.test(participant.player.id);
}

function related(participant: EnrichedParticipant | null): RelatedPlayer | null {
  return participant && identified(participant) ? { id: participant.player.id, name: participant.name } : null;
}

type ValidGoal = {
  kind: "goal" | "penalty_goal";
  scorer: EnrichedParticipant & { player: { id: string } };
  /** The single structured assistant; `null` when there is none. */
  assistant: EnrichedParticipant | null;
  /** More than one assistant slot: no assist of this goal can be attributed. */
  ambiguousAssist: boolean;
};

/**
 * A valid goal with exactly one identified scorer, or `null`.
 * Excluded: disallowed goals (`goal_review`/`varCancelledGoal`), VAR decisions,
 * own goals, any status other than `valid` or kind other than `ordinary`
 * (unknown included), and events whose scorer is missing, unidentified or repeated.
 */
function validGoal(item: EnrichedComment): ValidGoal | null {
  if (item.category !== "goal") return null;
  const kind = item.originalType === "scoreChange" ? "goal" : item.originalType === "penaltyScored" ? "penalty_goal" : null;
  if (kind === null) return null;
  if (item.attributes.goal_status !== "valid" || item.attributes.goal_kind !== "ordinary") return null;
  if (item.participants.some((participant) => participant.role === "own_goal_author")) return null;
  const scorerRole = kind === "goal" ? "scorer" : "penalty_scorer";
  const scorers = item.participants.filter((participant) => participant.sourceRole === "player" && participant.role === scorerRole);
  if (scorers.length !== 1 || !identified(scorers[0])) return null;
  // Only an ordinary goal has an assistant role; a penalty's `assist1`, if any, has no role.
  const assistants = kind === "goal"
    ? item.participants.filter((participant) => participant.sourceRole === "assist1" && participant.role === "assistant")
    : [];
  return { kind, scorer: scorers[0], assistant: assistants.length === 1 ? assistants[0] : null, ambiguousAssist: assistants.length > 1 };
}

/** The official count only when it is a real non-negative integer: never a missing value read as 0. */
function officialCount(stat: Stat | null | undefined): number | null {
  if (!stat || typeof stat.value !== "number") return null;
  return Number.isSafeInteger(stat.value) && stat.value >= 0 ? stat.value : null;
}

/**
 * Goals, penalty goals and confirmed assists of `playerId` in one match.
 *
 * - Duplicates: one event per source comment (`captureId` + `id`); the first wins.
 * - A player listed as both scorer and assistant of one goal gets the goal only.
 * - Assists: the candidates (assistant of a valid ordinary goal, not ambiguous)
 *   are shown only when their number equals the official `assists` stat; otherwise
 *   all are omitted (none is picked to fit) and counted in `omittedAssists`.
 *   Goals never depend on that check.
 */
export function playerMatchEvents(items: readonly EnrichedComment[], playerId: string, officialAssists: Stat | null | undefined): PlayerMatchEventsResult {
  const seen = new Set<string>();
  const goals: PlayerMatchEvent[] = [];
  const assists: PlayerMatchEvent[] = [];
  let unconfirmable = false;
  for (const item of items) {
    const eventKey = sourceEventKey(item);
    if (seen.has(eventKey)) continue;
    seen.add(eventKey);
    const goal = validGoal(item);
    if (!goal) continue;
    const base = { minute: item.minute, period: periodFor(item.periodName), item };
    if (goal.scorer.player.id === playerId) {
      goals.push({ ...base, key: `${eventKey}:${goal.kind}`, kind: goal.kind, related: related(goal.assistant) });
      continue;
    }
    if (goal.kind !== "goal") continue;
    const assistedBy = item.participants.filter((participant) =>
      participant.sourceRole === "assist1" && participant.role === "assistant" && identified(participant) && participant.player.id === playerId);
    if (!assistedBy.length) continue;
    if (goal.ambiguousAssist) unconfirmable = true;
    assists.push({ ...base, key: `${eventKey}:assist`, kind: "assist", related: related(goal.scorer) });
  }
  const official = officialCount(officialAssists);
  const confirmed = !unconfirmable && official !== null && official === assists.length;
  const events = confirmed ? [...goals, ...assists] : goals;
  // Higher source position = earlier in the match (the match timeline's order).
  events.sort((left, right) => right.item.position - left.item.position || kindOrder(left.kind) - kindOrder(right.kind));
  return { events, omittedAssists: confirmed ? 0 : assists.length };
}

function kindOrder(kind: PlayerMatchEventKind): number {
  return kind === "assist" ? 1 : 0;
}
