import type { SourceInfo } from "@/contract";
import { DataConfigurationError, getDataSource } from "@/server/data-source";
import { json, misconfigured } from "@/server/http";

/** Whether the data are synthetic; the source itself stays hidden. */
export function GET() {
  try {
    return json<SourceInfo>(getDataSource().info());
  } catch (failure) {
    if (failure instanceof DataConfigurationError) return misconfigured(failure);
    throw failure;
  }
}
