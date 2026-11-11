"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

/** Navigation, data notices and controls of the top bar: not on the access screen, where nothing is readable yet. */
export function HiddenOnAccess({ children }: { children: ReactNode }) {
  return usePathname() === "/acceso" ? null : <>{children}</>;
}
