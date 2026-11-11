"use client";

import { useSearchParams } from "next/navigation";
import { getSource } from "@/client/api";
import { matchesInitialState } from "@/lib/navigation";
import { failureReason, useApi } from "@/hooks/use-api";
import { LoadingState } from "@/ui/LoadingState";
import { RealMatchesView } from "./RealMatchesView";
import { LoadError, SyntheticMatchesView, type Props } from "./SyntheticMatchesView";

/**
 * «Partidos»: the real matches, or the demonstration's when the API says it is
 * one; never both in the same list. If the API cannot say, nothing is assumed.
 */
export function MatchesView(serverView: Props = {}) {
  // The URL in the browser rules (after Back it keeps the team this page wrote there); without a router (tests), the props.
  const search = useSearchParams();
  const props: Props = search ? { initialSeasonId: search.get("season") ?? undefined, initialTeamId: search.get("team") ?? undefined,
    initialState: matchesInitialState({ state: search.get("state") }) } : serverView;
  const source = useApi("source", (signal) => getSource({ signal }));
  if (source.error) return <>
    <LoadError message={`No se pudo determinar la fuente de los partidos. ${failureReason(source.error)}`} retry={source.retry} />
  </>;
  if (!source.data) return <LoadingState variant="page" label="Cargando partidos…" />;
  return source.data.synthetic ? <SyntheticMatchesView {...props} /> : <RealMatchesView {...props} />;
}
