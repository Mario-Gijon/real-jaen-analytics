/** The Next.js basePath is fixed into production builds and empty in development. */
function configuredBasePath(): string {
  return (process.env.NEXT_PUBLIC_BASE_PATH ?? "").replace(/\/+$/, "");
}

/** Add the application prefix to a same-origin absolute path exactly once. */
export function appPath(path: string): string {
  if (!path.startsWith("/") || path.startsWith("//")) return path;
  const basePath = configuredBasePath();
  if (!basePath || path === basePath || path.startsWith(`${basePath}/`)) return path;
  return `${basePath}${path}`;
}

/** Convert a browser pathname to the route path used by the app. */
export function routePath(pathname: string): string {
  const basePath = configuredBasePath();
  if (!basePath) return pathname;
  if (pathname === basePath || pathname === `${basePath}/`) return "/";
  return pathname.startsWith(`${basePath}/`) ? pathname.slice(basePath.length) : pathname;
}

/** Cookie Path is scoped to this app in production, and remains site-wide in development. */
export function appCookiePath(): string {
  const path = appPath("/");
  return path === "/" ? "/" : path.replace(/\/$/, "");
}
