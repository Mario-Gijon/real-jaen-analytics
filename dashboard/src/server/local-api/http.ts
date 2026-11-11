/** HTTP access to the real local API. */
export const DEFAULT_TIMEOUT_MS = 5000;

/** The local API did not answer, failed or answered something unusable. */
export class UpstreamUnavailableError extends Error {
  constructor(message: string, options?: { cause?: unknown }) {
    super(message, options);
    this.name = "UpstreamUnavailableError";
  }
}

export type Options = { baseUrl: string; timeoutMs?: number; fetch?: typeof fetch };

/**
 * GET against the local API: the parsed body, or `null` for 404. Any other
 * failure, timeout or non-JSON body is an {@link UpstreamUnavailableError}.
 */
export async function getJson(
  { fetch: fetchImpl = fetch, timeoutMs = DEFAULT_TIMEOUT_MS }: Options,
  url: string,
): Promise<unknown> {
  let response: Response;
  try {
    response = await fetchImpl(url, {
      headers: { Accept: "application/json" },
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
  if (!response.ok) throw new UpstreamUnavailableError(`HTTP ${response.status} en ${url}`);
  try {
    return await response.json();
  } catch (cause) {
    throw new UpstreamUnavailableError(`Respuesta no JSON en ${url}`, { cause });
  }
}

export const baseOf = ({ baseUrl }: Options) => baseUrl.replace(/\/+$/, "");
