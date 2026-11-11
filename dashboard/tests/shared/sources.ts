import type { SourceInfo } from "@/contract";

/** `GET /api/v1/source` in each data mode, as the server answers it. */
export const REAL_SOURCE: SourceInfo = { synthetic: false, label: "Datos reales", realSections: ["observedPlayers", "matches", "teams"] };
export const DEMO_SOURCE: SourceInfo = { synthetic: true, label: "Demostración · datos sintéticos", realSections: [] };
