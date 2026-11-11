"use client";

import { useState, type CSSProperties } from "react";
import type { TeamRef } from "@/contract";
import css from "./TeamCrest.module.css";

/** Short code drawn on the fallback: initials of several words, or 3 letters of one. */
export function crestCode(shortName: string): string {
  const words = shortName.normalize("NFD").replace(/\p{M}/gu, "").split(/\s+/).filter(Boolean);
  const code = words.length > 1 ? words.map((w) => w[0]).join("") : (words[0] ?? "?").slice(0, 3);
  return code.slice(0, 3).toLocaleUpperCase("es");
}

/**
 * A club crest. Without an image (or if it fails) it draws a neutral shield in
 * the club colours with a short code: recognisable, and clearly not a real
 * crest. Decorative by default, because the club name is always next to it.
 */
export function TeamCrest({
  team,
  size = 32,
  decorative = true,
}: {
  team: Pick<TeamRef, "name" | "shortName" | "crestUrl" | "colors">;
  size?: number;
  decorative?: boolean;
}) {
  const [failed, setFailed] = useState<string | null>(null);
  const src = team.crestUrl && team.crestUrl !== failed ? team.crestUrl : null;
  const style = { "--crest-size": `${size}px` } as CSSProperties;
  const label = decorative ? undefined : `Escudo de ${team.name}`;
  if (src) {
    return (
      <span className={css.crest} data-crest="image" style={style}>
        {/* Crests are small local assets; next/image adds nothing here. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img alt={label ?? ""} onError={() => setFailed(src)} src={src} />
      </span>
    );
  }
  const { primary, secondary } = team.colors;
  return (
    <span
      aria-hidden={decorative || undefined}
      aria-label={label}
      className={css.crest}
      data-crest="fallback"
      role={decorative ? undefined : "img"}
      style={style}
    >
      <svg viewBox="0 0 40 46">
        <path
          d="M20 2 37 7v15c0 11-7.4 18.7-17 22C10.4 40.7 3 33 3 22V7z"
          fill={primary}
          stroke={secondary}
          strokeWidth="2.5"
        />
        <text
          dominantBaseline="central"
          fill={secondary}
          fontSize="12"
          fontWeight="800"
          textAnchor="middle"
          x="20"
          y="22"
        >
          {crestCode(team.shortName)}
        </text>
      </svg>
    </span>
  );
}
