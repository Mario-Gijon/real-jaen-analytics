import { getDataSource } from "@/server/data-source";
import { jsonFromSource } from "@/server/http";
import { noStore } from "@/server/updates/responses";
import type { MatchComments } from "@/contract";

type Context = { params: Promise<{ matchId: string }> };

/** Separate narrative comments for a match; no fallback to incidents or synthetic narratives in real mode. */
export async function GET(_request: Request, { params }: Context) {
  const { matchId } = await params;
  const response = await jsonFromSource(
    () => getDataSource().matchComments(matchId),
    "Partido no encontrado en la fuente de comentarios.",
    `comentarios narrativos del partido ${matchId}`,
  );
  if (!response.ok) return response;
  const body = await response.json() as MatchComments;
  return noStore(body);
}
