import type { Competition } from "@/contract";
import { getDataSource } from "@/server/data-source";
import { guarded, json } from "@/server/http";

/** Competitions with their seasons, newest first. */
export async function GET() {
  return guarded(async () => json<Competition[]>(await getDataSource().competitions()));
}
