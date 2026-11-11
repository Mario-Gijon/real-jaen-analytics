/** Read-only rule annotations returned for the currently selected Spanish captures. */
export type EnrichmentAttributeValue = string | string[] | null;

export type EnrichedParticipant = {
  sourceRole: string;
  role: string | null;
  normalizerRole: string | null;
  sourceId: number | string | null;
  name: string | null;
  player: { id: string; name: string; position: null } | null;
};

export type EnrichedComment = {
  id: string | number;
  /** Selected source capture; used to ensure pages belong to one coherent result. */
  captureId: number | null;
  position: number;
  text: string;
  originalType: string;
  minute: number | null;
  periodName: string | null;
  isHome: boolean | null;
  score: { home: number | null; away: number | null } | null;
  category: string | null;
  attributes: Record<string, EnrichmentAttributeValue>;
  participants: EnrichedParticipant[];
};

export type EnrichedCommentsPage = {
  enrichmentVersion: string;
  total: number;
  limit: number;
  offset: number;
  items: EnrichedComment[];
};

export type EnrichedCommentsFilter = {
  sourceEventId: string;
  category?: string;
  originalType?: string;
  limit?: number;
  offset?: number;
};
