import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { useApi } from "@/hooks/use-api";

type Deferred<T> = { promise: Promise<T>; resolve: (value: T) => void };
function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function Probe({ request }: { request: (key: string, signal: AbortSignal) => Promise<string> }) {
  const [key, setKey] = useState("first");
  const state = useApi(key, (signal) => request(key, signal));
  return <div>
    <button onClick={() => setKey("second")}>Seleccionar segundo</button>
    <div role="status">{state.loading ? "Cargando" : state.data ?? "Error"}</div>
  </div>;
}

describe("useApi request changes", () => {
  it("keeps the latest selection when the earlier response arrives late", async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const signals = new Map<string, AbortSignal>();
    const request = vi.fn((key: string, signal: AbortSignal) => {
      signals.set(key, signal);
      return key === "first" ? first.promise : second.promise;
    });
    render(<Probe request={request} />);

    expect(screen.getByRole("status").textContent).toBe("Cargando");
    fireEvent.click(screen.getByRole("button", { name: "Seleccionar segundo" }));
    expect(signals.get("first")?.aborted).toBe(true);
    expect(screen.getByRole("status").textContent).toBe("Cargando");

    act(() => second.resolve("Segundo equipo"));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Segundo equipo"));
    act(() => first.resolve("Primer equipo tardío"));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Segundo equipo"));
  });
});
