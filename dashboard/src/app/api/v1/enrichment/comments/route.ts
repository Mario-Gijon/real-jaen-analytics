import { getDataSource } from "@/server/data-source";
import { badRequest, jsonFromSource } from "@/server/http";
import { noStore } from "@/server/updates/responses";
import type { EnrichedCommentsFilter } from "@/contract";

function positiveInteger(value: string | null, name: string): number | undefined | null {
  if (value === null) return undefined;
  if (!/^(0|[1-9]\d*)$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && (name !== "limit" || (parsed >= 1 && parsed <= 100)) ? parsed : null;
}

/** Protected same-origin BFF for the optional, read-only enrichment API. */
export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const sourceEventId = params.get("source_event_id");
  if (!sourceEventId || !/^[1-9]\d*$/.test(sourceEventId)) return badRequest("Indica un ID de evento SofaScore válido.");

  const category = params.get("category") ?? undefined;
  const originalType = params.get("original_type") ?? undefined;
  if ([category, originalType].some((value) => value !== undefined && (!value.trim() || value !== value.trim()))) {
    return badRequest("Los filtros de categoría y tipo deben ser texto sin espacios exteriores.");
  }

  const limit = positiveInteger(params.get("limit"), "limit");
  const offset = positiveInteger(params.get("offset"), "offset");
  if (limit === null || offset === null) return badRequest("La paginación no es válida.");
  const filter: EnrichedCommentsFilter = {
    sourceEventId,
    ...(category ? { category } : {}),
    ...(originalType ? { originalType } : {}),
    ...(limit !== undefined ? { limit } : {}),
    ...(offset !== undefined ? { offset } : {}),
  };

  const response = await jsonFromSource(
    () => getDataSource().enrichedComments(filter),
    "No hay comentarios enriquecidos para este evento.",
    `enriquecimiento del evento ${sourceEventId}`,
  );
  if (!response.ok) return response;
  return noStore(await response.json());
}
