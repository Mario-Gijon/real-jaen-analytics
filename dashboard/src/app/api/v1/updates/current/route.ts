import type { NextResponse } from "next/server";
import type { ApiError, UpdateStatus } from "@/contract";
import { controlStatus } from "@/server/updates/control-client";
import { fromControl, noStore, refuse, updatesConfigured } from "@/server/updates/responses";

const NONE: UpdateStatus = { available: false, scope: null, lastPublished: null, activeJob: null, lastJob: null, lastCoverage: null };

/** Whether updates exist here, their real scope, the last publication and the active and last jobs (any session). */
export async function GET(request: Request): Promise<NextResponse<UpdateStatus | ApiError>> {
  if (!updatesConfigured()) return noStore<UpdateStatus>(NONE);
  const refused = refuse(request, { changesState: false });
  if (refused) return refused;
  try {
    return noStore<UpdateStatus>({ available: true, ...(await controlStatus()) });
  } catch (error) {
    return fromControl(error, "estado");
  }
}
