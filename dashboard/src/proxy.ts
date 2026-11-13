import { NextResponse, type NextRequest } from "next/server";
import { returnPath } from "@/contract/return-path";
import { accessConfigured, accessRequired, sameOrigin, sessionCapability } from "@/server/auth/session";

/**
 * The access gate of the whole application (README.md): every page and every /api route needs a
 * valid session, except the access page and the session route that opens one. State changes must come from the
 * same origin. The update routes check the session again (refuse in server/updates/responses.ts).
 */
const SESSION_ROUTE = "/api/v1/session";
const ACCESS_PAGE = "/acceso";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const UPDATE_API = /^\/api\/v1\/updates(?:\/|$)/;

const problem = (status: number, code: string, message: string) =>
  NextResponse.json({ error: { code, message } }, { status, headers: { "Cache-Control": "no-store" } });

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const api = pathname.startsWith("/api/");
  const updateApi = UPDATE_API.test(pathname);

  // The update API is always private, including in demo mode. Check authorization before CSRF or service
  // availability so anonymous callers get 401 and read-only sessions get 403 without reaching Sports Data.
  if (updateApi) {
    if (accessRequired() && !accessConfigured()) {
      return problem(503, "UPSTREAM_UNAVAILABLE", "El acceso no está configurado en esta instalación.");
    }
    const capability = sessionCapability(request);
    if (capability === null) return problem(401, "UNAUTHORIZED", "Inicia sesión.");
    if (!capability) return problem(403, "FORBIDDEN", "No tienes permiso para actualizar.");
  }

  if (api && !SAFE_METHODS.has(request.method) && !sameOrigin(request)) return problem(403, "FORBIDDEN", "Origen no permitido.");
  if (pathname === SESSION_ROUTE) return NextResponse.next();  // signs in and out by itself
  if (!accessRequired()) return NextResponse.next();
  if (!accessConfigured()) {
    return api
      ? problem(503, "UPSTREAM_UNAVAILABLE", "El acceso no está configurado en esta instalación.")
      : pathname === ACCESS_PAGE ? NextResponse.next() : NextResponse.redirect(new URL(ACCESS_PAGE, request.url));
  }
  const capability = sessionCapability(request);
  const signedIn = capability !== null;
  if (pathname === ACCESS_PAGE) {
    return signedIn ? NextResponse.redirect(new URL(returnPath(request.nextUrl.searchParams.get("next")), request.url)) : NextResponse.next();
  }
  if ((pathname === "/operador" || pathname.startsWith("/operador/")) && capability === false) {
    return NextResponse.redirect(new URL("/", request.url));
  }
  if (signedIn) return NextResponse.next();
  if (api) return problem(401, "UNAUTHORIZED", "Inicia sesión.");
  const target = new URL(ACCESS_PAGE, request.url);
  if (pathname !== "/") target.searchParams.set("next", `${pathname}${search}`);
  return NextResponse.redirect(target);
}

export const config = {
  // Static build files and public images (crests, icon) need no session; everything else does.
  matcher: ["/((?!_next/static|_next/image|favicon\\.ico|icon\\.svg|crests/).*)"],
};
