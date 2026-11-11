import type { NextResponse } from "next/server";
import type { ApiError, UpdateJob } from "@/contract";
import { cancelUpdate } from "@/server/updates/control-client";
import { failure, fromControl, noStore, refuse } from "@/server/updates/responses";

type Context = { params: Promise<{ updateId: string }> };

/** Asks to stop an update while it is still acquiring; idempotent. Verifying or publishing is never cut. */
export async function POST(request: Request, { params }: Context): Promise<NextResponse<UpdateJob | ApiError>> {
  const refused = refuse(request, { changesState: true });
  if (refused) return refused;
  const { updateId } = await params;
  try {
    const job = await cancelUpdate(updateId);
    return job ? noStore(job, 202) : failure(404, "NOT_FOUND", "Actualización no encontrada.");
  } catch (error) {
    return fromControl(error, `cancelación ${updateId}`);
  }
}
