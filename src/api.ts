/** Base URL for the Node server (no trailing slash). Empty uses same origin (Vite dev proxy). */
export function apiUrl(path: string): string {
  const raw = import.meta.env.VITE_API_BASE as string | undefined;
  const base = raw?.replace(/\/$/, "") ?? "";
  const p = path.startsWith("/") ? path : `/${path}`;
  return base ? `${base}${p}` : p;
}
