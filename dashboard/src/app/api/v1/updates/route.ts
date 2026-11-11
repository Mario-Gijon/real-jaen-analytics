import type { NextResponse } from "next/server";
import type { ApiError, UpdateStart } from "@/contract";
import { startUpdate } from "@/server/updates/control-client";
import { failure, fromControl, noStore, refuse } from "@/server/updates/responses";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

/**
 * Starts the update, or returns the job of the same `Idempotency-Key` or the one already active: repeated
 * clicks, another tab or another operator never create a second job. The body is ignored: no parameter is accepted.
 */
export async function POST(request: Request): Promise<NextResponse<UpdateStart | ApiError>> {
  const refused = refuse(request, { changesState: true });
  if (refused) return refused;
  const key = request.headers.get("idempotency-key") ?? "";
  if (!UUID.test(key)) return failure(400, "BAD_REQUEST", "Falta una Idempotency-Key UUID.");
  try {
    const started = await startUpdate(key);
    return noStore(started, 202, { Location: `/api/v1/updates/${started.job.id}` });
  } catch (error) {
    return fromControl(error, "inicio");
  }
}
