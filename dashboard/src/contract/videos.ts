/** Optional, read-only video references exposed to the dashboard through its BFF. */
export type MatchVideoReference =
  | {
      available: true;
      youtubeVideoId: string;
      synchronizationStatus: "calibrated" | "uncertain" | "not_calibrated";
    }
  | { available: false };

/** Only a resolved response carries a single playback position. */
export type VideoEventLocation =
  | { status: "resolved"; videoSecond: number; eventVideoSecond: number; eventClockSecond: number;
      eventClockSecondEstimate: number; leadSeconds: number; estimated: boolean;
      method: "event_second_interpolation" | "minute_tail" | "boundary_extrapolation";
      confidence: { ocr: string | null; period: string | null; location: string | null };
      warnings: string[] }
  | { status: "ambiguous" | "uncertain" | "out_of_coverage" };

export type VideoJobStatus = "pending" | "downloading" | "calibrating" | "saving" | "completed" | "failed";

export type VideoJob = {
  jobId: string;
  match: { provider: "sofascore"; eventId: string };
  youtubeVideoId: string;
  status: VideoJobStatus;
  message: string;
  errorCode: string | null;
  createdAt: string;
  updatedAt: string;
};
