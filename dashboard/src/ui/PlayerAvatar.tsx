"use client";

import { useState, type CSSProperties } from "react";
import css from "./PlayerAvatar.module.css";

const initials = (name: string) =>
  name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toLocaleUpperCase("es") || "?";

/**
 * Small round player picture. Without an image (or if it fails) it shows the
 * player's initials on a neutral disc: never an invented photo. Decorative,
 * because the name is always next to it.
 */
export function PlayerAvatar({
  name,
  src = null,
  size = 26,
}: {
  name: string;
  src?: string | null;
  size?: number;
}) {
  const [failed, setFailed] = useState<string | null>(null);
  const image = src && src !== failed ? src : null;
  return (
    <span
      aria-hidden="true"
      className={css.avatar}
      data-avatar={image ? "image" : "fallback"}
      style={{ "--avatar-size": `${size}px` } as CSSProperties}
    >
      {image ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img alt="" onError={() => setFailed(image)} src={image} />
      ) : (
        initials(name)
      )}
    </span>
  );
}
