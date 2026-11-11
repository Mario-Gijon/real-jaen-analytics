import type { Metadata } from "next";
import { OperatorView } from "@/features/updates/OperatorView";

export const metadata: Metadata = { title: "Actualización de datos", robots: { index: false, follow: false } };

/** Operator screen of the update (not in the navigation). */
export default function OperatorPage() {
  return <OperatorView />;
}
