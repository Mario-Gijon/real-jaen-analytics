import { getDataSource } from "@/server/data-source";
import { jsonFromSource } from "@/server/http";

type Context = { params: Promise<{ teamId: string }> };

/** Resolve only the team's public identity, including when it has no own statistics export. */
export async function GET(_request: Request, { params }: Context) {
  const { teamId } = await params;
  return jsonFromSource(
    () => getDataSource().teamIdentity(teamId),
    "No se pudo resolver la identidad de este equipo en la fuente real.",
    `identidad real de ${teamId}`,
  );
}
