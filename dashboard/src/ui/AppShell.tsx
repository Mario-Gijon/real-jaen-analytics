import Image from "next/image";
import Link from "next/link";
import type { ReactNode } from "react";
import { HiddenOnAccess } from "./HiddenOnAccess";
import { appPath } from "@/contract/base-path";
import { MainNav } from "./MainNav";
import { SourceBadge } from "./SourceBadge";
import css from "./AppShell.module.css";

/**
 * Analyst frame: Real Jaén identity, the two sections and the data-source
 * notice in one top bar (no sidebar for two entries), then the page content.
 * `actions`: an optional account-bar control shown immediately before `session`. On the access screen only the
 * identity is shown.
 */
export function AppShell({ children, actions, session }: { children: ReactNode; actions?: ReactNode; session?: ReactNode }) {
  return (
    <>
      <a className={css.skip} href="#contenido">
        Saltar al contenido
      </a>
      <header className={css.bar}>
        <div className={css.inner}>
          <div className={css.identity}>
            <Link className={css.brand} href="/matches">
              <Image
                alt=""
                className={css.crest}
                height={44}
                priority
                src={appPath("/crests/real-jaen.webp")}
                width={44}
              />
              <span className={css.brandText}>
                <strong>Real Jaén CF</strong>
                <span>Análisis</span>
              </span>
            </Link>
            <HiddenOnAccess>
              {actions || session ? (
                <div className={css.accountActions}>
                  {actions}
                  {session ? <div className={css.session}>{session}</div> : null}
                </div>
              ) : null}
            </HiddenOnAccess>
          </div>
          <HiddenOnAccess>
            <div className={css.navRow}>
              <MainNav />
              <SourceBadge />
            </div>
          </HiddenOnAccess>
        </div>
      </header>
      <main className={css.main} id="contenido">
        {children}
      </main>
    </>
  );
}
