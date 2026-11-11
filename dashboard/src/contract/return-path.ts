/**
 * A relative path of this application to return to after signing in (`/acceso?next=`); anything else (another
 * site, a protocol-relative URL, the access page itself) goes to the start. Shared by the access gate and screen.
 */
export function returnPath(value: string | null | undefined): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\") || value.startsWith("/acceso")) return "/";
  return value;
}
