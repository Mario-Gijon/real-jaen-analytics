/**
 * `GET /api/v1/source` of a real deployment. The e2e server runs the
 * demonstration (`DATA_MODE=demo`); the real-data specs answer the browser's
 * requests themselves, this one included.
 */
export const REAL_SOURCE = { synthetic: false, label: "Datos reales", realSections: ["observedPlayers", "matches", "teams"] };
