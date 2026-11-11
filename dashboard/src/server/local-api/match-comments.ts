/** Typed adapter for the separate narrative-comments route. */
import { type MatchComments, type NarrativeComment, type NarrativeCommentsAvailability, type NarrativeCommentParticipant } from "@/contract";
import { baseOf, getJson, type Options } from "./http";
import { invalid, isRecord, optionalText } from "./validation";
import { sourceMatchId } from "./identifiers";

const STATES = new Set<NarrativeCommentsAvailability>(["available", "empty", "not_collected", "error"]);
const ROLES = ["player", "playerIn", "playerOut", "assist1"] as const;

function nonnegativeInteger(value: unknown, field: string): number | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) throw invalid(field);
  return value;
}

function availability(value: unknown, field: string): NarrativeCommentsAvailability {
  if (typeof value !== "string" || !STATES.has(value as NarrativeCommentsAvailability)) throw invalid(field);
  return value as NarrativeCommentsAvailability;
}

function parseParticipant(value: unknown, field: string): NarrativeCommentParticipant {
  if (!isRecord(value)) throw invalid(field);
  const text = (key: string) => {
    const found = optionalText(value[key], `${field}.${key}`);
    return found;
  };
  return {
    name: text("name"), shortName: text("shortName"), position: text("position"), jerseyNumber: text("jerseyNumber"),
  };
}

function parseComment(value: unknown, index: number): NarrativeComment {
  const field = `comments[${index}]`;
  if (!isRecord(value)) throw invalid(field);
  if (typeof value.id !== "string" || !value.id.trim()) throw invalid(`${field}.id`);
  if (typeof value.text !== "string" || !value.text.trim()) throw invalid(`${field}.text`);
  if (typeof value.type !== "string" || !value.type.trim()) throw invalid(`${field}.type`);
  const position = nonnegativeInteger(value.position, `${field}.position`);
  if (position === null) throw invalid(`${field}.position`);
  const minute = nonnegativeInteger(value.minute, `${field}.minute`);
  const periodName = optionalText(value.periodName, `${field}.periodName`);
  const isHome = value.isHome ?? null;
  if (isHome !== null && typeof isHome !== "boolean") throw invalid(`${field}.isHome`);
  let score: NarrativeComment["score"] = null;
  if (value.score !== undefined && value.score !== null) {
    if (!isRecord(value.score)) throw invalid(`${field}.score`);
    score = {
      home: nonnegativeInteger(value.score.home, `${field}.score.home`),
      away: nonnegativeInteger(value.score.away, `${field}.score.away`),
    };
  }
  const incidentId = value.incidentId === undefined ? null : optionalText(value.incidentId, `${field}.incidentId`);
  const participants: NarrativeComment["participants"] = {};
  if (value.participants !== undefined && !isRecord(value.participants)) throw invalid(`${field}.participants`);
  if (isRecord(value.participants)) {
    for (const role of ROLES) {
      if (value.participants[role] === undefined || value.participants[role] === null) continue;
      participants[role] = parseParticipant(value.participants[role], `${field}.participants.${role}`);
    }
  }
  return { id: value.id, position, text: value.text, type: value.type, minute, periodName, isHome,
    score, incidentId, participants };
}

function parseMatchComments(value: unknown, requestedMatchId: string): MatchComments {
  if (!isRecord(value) || value.matchId !== requestedMatchId) throw invalid("matchId");
  const capturedAt = optionalText(value.capturedAt, "capturedAt");
  const responseSha256 = optionalText(value.responseSha256, "responseSha256");
  if (responseSha256 !== null && !/^[0-9a-f]{64}$/.test(responseSha256)) throw invalid("responseSha256");
  if (!isRecord(value.lastAttempt)) throw invalid("lastAttempt");
  const lastAttempt = {
    availability: availability(value.lastAttempt.availability, "lastAttempt.availability"),
    occurredAt: optionalText(value.lastAttempt.occurredAt, "lastAttempt.occurredAt"),
    httpStatus: nonnegativeInteger(value.lastAttempt.httpStatus, "lastAttempt.httpStatus"),
    errorCode: optionalText(value.lastAttempt.errorCode, "lastAttempt.errorCode"),
  };
  if (lastAttempt.httpStatus !== null && (lastAttempt.httpStatus < 100 || lastAttempt.httpStatus > 599)) {
    throw invalid("lastAttempt.httpStatus");
  }
  if (!Array.isArray(value.comments)) throw invalid("comments");
  const comments = value.comments.map(parseComment);
  if (new Set(comments.map((comment) => comment.id)).size !== comments.length) throw invalid("comments.id repetido");
  if (new Set(comments.map((comment) => comment.position)).size !== comments.length) throw invalid("comments.position repetido");
  const current = availability(value.availability, "availability");
  if (current === "available" && !comments.length) throw invalid("availability: available sin comentarios");
  if (current === "empty" && comments.length) throw invalid("availability: empty con comentarios");
  return { matchId: requestedMatchId, availability: current, capturedAt, responseSha256, lastAttempt, comments };
}

export function createLocalApiMatchComments(options: Options) {
  return async (matchId: string): Promise<MatchComments | null> => {
    if (sourceMatchId(matchId) === null) return null;
    const body = await getJson(options, `${baseOf(options)}/api/v1/matches/${encodeURIComponent(matchId)}/comments`);
    return body === null ? null : parseMatchComments(body, matchId);
  };
}
