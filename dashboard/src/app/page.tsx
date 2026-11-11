import type { Metadata } from "next";
import { HomeView } from "@/features/home/HomeView";

export const metadata: Metadata = { title: "Preparación de rival" };

export default async function Home({ searchParams }: {
  searchParams: Promise<{ season?: string | string[]; match?: string | string[] }>;
}) {
  const query = await searchParams;
  const now = await readClock();
  return <HomeView seasonId={typeof query.season === "string" ? query.season : undefined}
    matchId={typeof query.match === "string" ? query.match : undefined} now={now} />;
}

async function readClock() { return Date.now(); }
