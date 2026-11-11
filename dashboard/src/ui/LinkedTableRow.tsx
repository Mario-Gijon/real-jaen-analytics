"use client";

import type { ComponentPropsWithoutRef } from "react";
import { activateRowLink } from "./activate-row-link";

/** One native link is the keyboard stop; clicks elsewhere in its row activate it. */
export function LinkedTableRow(props: ComponentPropsWithoutRef<"tr">) {
  return <tr {...props} onClick={event => {
    activateRowLink(event.target, event.currentTarget, event);
  }} onKeyDown={event => {
    if (event.target === event.currentTarget && event.key === "Enter") {
      (event.currentTarget.querySelector<HTMLAnchorElement>('a[data-row-primary][href]')
        ?? event.currentTarget.querySelector<HTMLAnchorElement>("a[href]"))?.click();
    }
  }} />;
}
