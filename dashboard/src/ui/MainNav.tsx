"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import css from "./AppShell.module.css";

/** Players open from matches and teams, outside the main navigation. */
export const SECTIONS = [
  { href: "/", label: "Inicio" },
  { href: "/matches", label: "Partidos" },
  { href: "/teams", label: "Equipos" },
] as const;

export function MainNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Secciones" className={css.nav}>
      {SECTIONS.map((section) => {
        const current =
          pathname === section.href || pathname.startsWith(`${section.href}/`);
        return (
          <Link
            aria-current={current ? "page" : undefined}
            className={css.navLink}
            href={section.href}
            key={section.href}
          >
            {section.label}
          </Link>
        );
      })}
    </nav>
  );
}
