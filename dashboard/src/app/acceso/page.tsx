import type { Metadata } from "next";
import { returnPath } from "@/contract/return-path";
import { AccessView } from "@/features/access/AccessView";

export const metadata: Metadata = { title: "Acceso", robots: { index: false, follow: false } };

/** The access screen of the whole application: the shared password opens a 90-day session. */
export default async function AccessPage({ searchParams }: { searchParams: Promise<{ next?: string | string[] }> }) {
  const { next } = await searchParams;
  return <AccessView next={returnPath(typeof next === "string" ? next : null)} />;
}
