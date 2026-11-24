import { NextResponse } from "next/server";
import { badRequest, misconfigured, notFound, upstreamUnavailable } from "@/server/http";
import { DataConfigurationError, getDataSource } from "@/server/data-source";
import { UpstreamUnavailableError } from "@/server/local-api/local-api-source";
import { sourcePlayerId } from "@/server/local-api/identifiers";

type Context = { params: Promise<{ playerId: string; sha256: string }> };
const SHA256 = /^[0-9a-f]{64}$/;

/** Serve only a validated player's immutable WebP through the Dashboard's authenticated same-origin BFF. */
export async function GET(request: Request, { params }: Context) {
  const { playerId, sha256 } = await params;
  if (!SHA256.test(sha256)) return badRequest("Hash de fotografía no válido.");
  if (sourcePlayerId(playerId) === null) return notFound("Fotografía no encontrada.");

  try {
    const photo = await getDataSource().playerPhoto(playerId, sha256, request.headers.get("if-none-match"));
    if (!photo) return notFound("Fotografía no encontrada.");
    const headers = {
      "Content-Type": "image/webp",
      "Cache-Control": "private, max-age=31536000, immutable",
      ETag: `"${sha256}"`,
      "X-Content-Type-Options": "nosniff",
    };
    if ("notModified" in photo) return new NextResponse(null, { status: 304, headers });
    const body = new ArrayBuffer(photo.bytes.byteLength);
    new Uint8Array(body).set(photo.bytes);
    return new NextResponse(body, { status: 200, headers });
  } catch (failure) {
    if (failure instanceof DataConfigurationError) return misconfigured(failure);
    if (!(failure instanceof UpstreamUnavailableError)) throw failure;
    console.error(`[api] fotografía de ${playerId}:`, failure.message);
    return upstreamUnavailable("La fuente de datos real no está disponible.");
  }
}
