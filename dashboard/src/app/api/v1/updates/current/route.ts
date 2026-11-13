import type { NextResponse } from "next/server";
import type { ApiError, UpdateStatus } from "@/contract";
import { controlStatus } from "@/server/updates/control-client";
import { fromControl, noStore, refusePermission, updatesConfigured } from "@/server/updates/responses";

const NONE: UpdateStatus = { available: false, scope: null, lastPublished: null, activeJob: null, lastJob: null, lastCoverage: null };

/** Whether updates exist here, their real scope, the last publication and the active and last jobs (admin session). */
export async function GET(request: Request): Promise<NextResponse<UpdateStatus | ApiError>> {
  const refused = refusePermission(request);
  if (refused) return refused;
  if (!updatesConfigured()) return noStore<UpdateStatus>(NONE);
  try {
    return noStore<UpdateStatus>(await controlStatus());
  } catch (error) {
    return fromControl(error, "estado");
  }
}
