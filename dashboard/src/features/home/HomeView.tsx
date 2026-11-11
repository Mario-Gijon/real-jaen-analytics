"use client";

import { getSource } from "@/client/api";
import { failureReason, useApi } from "@/hooks/use-api";
import { LoadError } from "./PreparationParts";
import { RealHomeView } from "./RealHomeView";
import { SyntheticHomeView, type Props } from "./SyntheticHomeView";
import { LoadingState } from "@/ui/LoadingState";
import css from "./HomeView.module.css";

/**
 * «Inicio»: the next encounter of the club and its rival's last matches, from
 * the real source, or from the demonstration when the API says it is one
 * (`DATA_MODE=demo`). The two never mix; if the API cannot say, nothing is
 * shown.
 */
export function HomeView(props: Props) {
  const source = useApi("source", signal => getSource({ signal }));
  return <div className={css.page}>
    {source.error ? <LoadError message={`No se pudo determinar la fuente de los datos. ${failureReason(source.error)}`} retry={source.retry} />
      : !source.data ? <LoadingState variant="page" label="Cargando preparación…" />
        : source.data.synthetic ? <SyntheticHomeView {...props} /> : <RealHomeView {...props} />}
  </div>;
}
