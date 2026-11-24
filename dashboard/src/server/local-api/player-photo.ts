/** The only binary read from the real local API: a current WebP for one exact player and content hash. */
import { createHash } from "node:crypto";
import { baseOf, DEFAULT_TIMEOUT_MS, UpstreamUnavailableError, type Options } from "./http";
import { sourcePlayerId } from "./identifiers";

const SHA256 = /^[0-9a-f]{64}$/;
const MAX_PHOTO_BYTES = 4 * 1024 * 1024;

export type LocalPlayerPhoto = { bytes: Uint8Array } | { notModified: true };

async function readPhoto(response: Response): Promise<Uint8Array> {
  const declared = response.headers.get("content-length");
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > MAX_PHOTO_BYTES)) {
    throw new UpstreamUnavailableError("Tamaño de fotografía no válido en la fuente de datos");
  }
  if (!response.body) return new Uint8Array();

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > MAX_PHOTO_BYTES) {
        void reader.cancel().catch(() => {});
        throw new UpstreamUnavailableError("La fotografía supera el tamaño admitido");
      }
      chunks.push(value);
    }
  } catch (cause) {
    if (cause instanceof UpstreamUnavailableError) throw cause;
    throw new UpstreamUnavailableError("No se pudo leer la fotografía de la fuente de datos", { cause });
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

function isWebP(bytes: Uint8Array): boolean {
  return bytes.byteLength >= 20
    && bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x46
    && bytes[8] === 0x57 && bytes[9] === 0x45 && bytes[10] === 0x42 && bytes[11] === 0x50
    && new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getUint32(4, true) + 8 === bytes.byteLength;
}

/** Fetches one fixed Sports Data path; never accepts an upstream URL from the request or the player record. */
export function createLocalApiPlayerPhoto({ fetch: fetchImpl = fetch, timeoutMs = DEFAULT_TIMEOUT_MS, ...options }: Options) {
  return async (playerId: string, sha256: string, ifNoneMatch?: string | null): Promise<LocalPlayerPhoto | null> => {
    const sourceId = sourcePlayerId(playerId);
    if (sourceId === null || !SHA256.test(sha256)) return null;

    const headers = new Headers({ Accept: "image/webp" });
    const expectedEtag = `"${sha256}"`;
    if (ifNoneMatch?.trim() === expectedEtag) headers.set("If-None-Match", expectedEtag);
    const url = `${baseOf(options)}/api/v1/players/${sourceId}/photo/${sha256}`;
    let response: Response;
    try {
      response = await fetchImpl(url, {
        headers,
        cache: "no-store",
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (cause) {
      const timedOut = cause instanceof Error && cause.name === "TimeoutError";
      throw new UpstreamUnavailableError(
        timedOut ? `Sin respuesta en ${timeoutMs} ms: ${url}` : `Sin conexión con ${url}`,
        { cause },
      );
    }
    if (response.status === 404) return null;
    if (response.status === 304) {
      if (ifNoneMatch?.split(",").some((tag) => tag.trim() === expectedEtag)) return { notModified: true };
      throw new UpstreamUnavailableError("La fuente devolvió 304 sin una validación condicional solicitada");
    }
    if (!response.ok) throw new UpstreamUnavailableError(`HTTP ${response.status} en ${url}`);

    const mediaType = response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
    if (mediaType !== "image/webp") throw new UpstreamUnavailableError("La fuente devolvió un formato de imagen no admitido");
    const bytes = await readPhoto(response);
    if (!isWebP(bytes)) throw new UpstreamUnavailableError("La fuente devolvió una imagen WebP no válida");
    if (createHash("sha256").update(bytes).digest("hex") !== sha256) {
      throw new UpstreamUnavailableError("La fotografía no coincide con el hash solicitado");
    }
    if (ifNoneMatch?.split(",").some((tag) => tag.trim() === expectedEtag)) return { notModified: true };
    return { bytes };
  };
}
