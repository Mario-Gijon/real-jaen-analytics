import type { NextResponse } from "next/server";
import type { ApiError, UpdateJob } from "@/contract";
import { updateJob } from "@/server/updates/control-client";
import { failure, fromControl, noStore, refuse } from "@/server/updates/responses";

type Context = { params: Promise<{ updateId: string }> };

/** Progress or result of one update. */
export async function GET(request: Request, { params }: Context): Promise<NextResponse<UpdateJob | ApiError>> {
  const refused = refuse(request, { changesState: false });
  if (refused) return refused;
  const { updateId } = await params;
  try {
    const job = await updateJob(updateId);
    return job ? noStore(job) : failure(404, "NOT_FOUND", "Actualización no encontrada.");
  } catch (error) {
    return fromControl(error, `trabajo ${updateId}`);
  }
}
