/** Typed adapter for the read-only enrichment endpoint. */
import type { EnrichedComment, EnrichedCommentsFilter, EnrichedCommentsPage, EnrichmentAttributeValue } from "@/contract";
import { baseOf, getJson, type Options } from "./http";
import { playerIdFromSource } from "./identifiers";
import { count, invalid, isRecord, optionalText } from "./validation";

function parseAttributes(value: unknown, field: string): Record<string, EnrichmentAttributeValue> {
  if (!isRecord(value)) throw invalid(field);
  const attributes: Record<string, EnrichmentAttributeValue> = {};
  for (const [key, item] of Object.entries(value)) {
    if (item === null || typeof item === "string") {
      attributes[key] = item;
    } else if (Array.isArray(item) && item.every((entry) => typeof entry === "string")) {
      attributes[key] = item;
    } else {
      throw invalid(`${field}.${key}`);
    }
  }
  return attributes;
}

function requiredText(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) throw invalid(field);
  return value;
}

function optionalBoolean(value: unknown, field: string): boolean | null {
  if (value === undefined || value === null) return null;
  if (value === true || value === 1) return true;
  if (value === false || value === 0) return false;
  throw invalid(field);
}

function parseParticipant(value: unknown, field: string) {
  if (!isRecord(value)) throw invalid(field);
  const sourceRole = requiredText(value.source_role, `${field}.source_role`);
  const role = optionalText(value.role, `${field}.role`);
  const normalizerRole = optionalText(value.normalizer_role, `${field}.normalizer_role`);
  const original = isRecord(value.original) ? value.original : null;
  const name = original ? optionalText(original.name, `${field}.original.name`) : null;
  const rawId = value.source_id;
  const numericId = typeof rawId === "number" && Number.isSafeInteger(rawId) && rawId > 0
    ? rawId
    : typeof rawId === "string" && /^[1-9]\d*$/.test(rawId) && Number.isSafeInteger(Number(rawId))
      ? Number(rawId)
      : null;
  const sourceId = numericId === null ? null : rawId as number | string;
  return {
    sourceRole, role, normalizerRole, sourceId, name,
    player: numericId === null ? null : {
      id: playerIdFromSource(numericId), name: name ?? "Jugador", position: null,
    },
  };
}

function parseComment(value: unknown, index: number): EnrichedComment {
  const field = `items[${index}]`;
  if (!isRecord(value) || !isRecord(value.comment) || !isRecord(value.enrichment)) throw invalid(field);
  const comment = value.comment;
  const enrichment = value.enrichment;
  const id = comment.source_comment_id;
  if (!(typeof id === "string" && id.trim()) && !(typeof id === "number" && Number.isSafeInteger(id) && id >= 0)) {
    throw invalid(`${field}.comment.source_comment_id`);
  }
  const text = requiredText(comment.text, `${field}.comment.text`);
  const originalType = requiredText(comment.type, `${field}.comment.type`);
  const category = optionalText(enrichment.category, `${field}.enrichment.category`);
  const minute = comment.minute === null ? null : count(comment.minute, `${field}.comment.minute`);
  const homeScore = comment.home_score === null || comment.home_score === undefined
    ? null : count(comment.home_score, `${field}.comment.home_score`);
  const awayScore = comment.away_score === null || comment.away_score === undefined
    ? null : count(comment.away_score, `${field}.comment.away_score`);
  const provenance = isRecord(value.provenance) ? value.provenance : null;
  const captureId = provenance?.capture_id === null || provenance?.capture_id === undefined
    ? null : count(provenance.capture_id, `${field}.provenance.capture_id`);
  if (!Array.isArray(enrichment.participants)) throw invalid(`${field}.enrichment.participants`);
  return {
    id,
    captureId,
    position: count(comment.source_position, `${field}.comment.source_position`),
    text,
    originalType,
    minute,
    periodName: optionalText(comment.period_name, `${field}.comment.period_name`),
    isHome: optionalBoolean(comment.is_home, `${field}.comment.is_home`),
    score: homeScore === null && awayScore === null ? null : { home: homeScore, away: awayScore },
    category,
    attributes: parseAttributes(enrichment.attributes, `${field}.enrichment.attributes`),
    participants: enrichment.participants.map((participant, participantIndex) =>
      parseParticipant(participant, `${field}.enrichment.participants[${participantIndex}]`)),
  };
}

function parsePage(value: unknown): EnrichedCommentsPage {
  if (!isRecord(value) || !Array.isArray(value.items)) throw invalid("enrichment response");
  const enrichmentVersion = optionalText(value.enrichment_version, "enrichment_version");
  if (!enrichmentVersion) throw invalid("enrichment_version");
  const total = count(value.total, "total");
  const limit = count(value.limit, "limit");
  const offset = count(value.offset, "offset");
  if (limit < 1 || limit > 100 || value.items.length > limit) throw invalid("pagination");
  return { enrichmentVersion, total, limit, offset, items: value.items.map(parseComment) };
}

export function createLocalApiEnrichedComments(options: Options) {
  return async (filter: EnrichedCommentsFilter): Promise<EnrichedCommentsPage | null> => {
    if (!/^[1-9]\d*$/.test(filter.sourceEventId)) throw invalid("sourceEventId");
    const params = new URLSearchParams({ source_event_id: filter.sourceEventId });
    if (filter.category) params.set("category", filter.category);
    if (filter.originalType) params.set("original_type", filter.originalType);
    if (filter.limit !== undefined) params.set("limit", String(filter.limit));
    if (filter.offset !== undefined) params.set("offset", String(filter.offset));
    const body = await getJson(options, `${baseOf(options)}/api/v1/enrichment/comments?${params}`);
    return body === null ? null : parsePage(body);
  };
}
