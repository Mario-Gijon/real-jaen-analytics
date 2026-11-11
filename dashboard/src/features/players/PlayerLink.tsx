import Link from "next/link";
import type { PlayerRef } from "@/contract";
import type { ProfileOrigin } from "@/lib/navigation";
import css from "./PlayerLink.module.css";
import { playerHref, playerProfileHref, type PlayerOrigin } from "./navigation";

/** Navigate using the identified player and the season of the current context. */
export function PlayerLink({ player, seasonId, className = "", origin, profileOrigin }: {
  player: PlayerRef; seasonId?: string; className?: string; origin?: PlayerOrigin; profileOrigin?: ProfileOrigin;
}) {
  if (!player.id.trim() || (!profileOrigin && !seasonId)) return <span className={className}>{player.name}</span>;
  return <Link className={`${css.link} ${className}`.trim()} prefetch={false}
    href={profileOrigin ? playerProfileHref(player.id, null, profileOrigin) : playerHref(player.id, seasonId!, origin)}>
    {player.name}
  </Link>;
}
