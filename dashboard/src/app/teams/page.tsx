import type { Metadata } from "next";
import { teamsInitialView } from "@/features/players/navigation";
import { TeamsView } from "@/features/teams/TeamsView";

export const metadata: Metadata = { title: "Equipos" };

export default async function TeamsPage({ searchParams }: { searchParams: Promise<{ team?: string; season?: string; tab?: string }> }) {
  return <TeamsView {...teamsInitialView(await searchParams)} />;
}
