import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccessView } from "@/features/access/AccessView";
import { SessionControl } from "@/features/access/SessionControl";

type Handler = (url: string, init?: RequestInit) => { status?: number; body?: unknown };
function api(handler: Handler) {
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const { status = 200, body = null } = handler(String(input), init);
    return status === 204 ? new Response(null, { status }) : new Response(JSON.stringify(body), { status });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const assign = vi.fn();
beforeEach(() => {
  assign.mockReset();
  vi.stubGlobal("location", { ...window.location, assign, pathname: "/acceso", search: "" });
});
afterEach(() => vi.unstubAllGlobals());

describe("AccessView (/acceso)", () => {
  it("opens the session with the password and returns where the analyst was going; never keeps the password", async () => {
    const fetch = api((url, init) => {
      if (init?.method !== "POST") return { body: { required: true, signedIn: false } };
      const { password } = JSON.parse(String(init?.body));
      return password === "la-buena-contrasena" ? { status: 204 }
        : { status: 401, body: { error: { code: "UNAUTHORIZED", message: "Contraseña incorrecta." } } };
    });
    render(<AccessView next="/teams" />);
    const user = userEvent.setup();
    const field = screen.getByLabelText("Contraseña");
    expect(field).toHaveAttribute("type", "password");
    expect(field).toHaveAttribute("autocomplete", "current-password");
    expect(screen.getByRole("heading", { name: "Bienvenido" })).toBeVisible();
    expect(screen.getByText("La sesión se mantendrá durante 90 días en este navegador.")).toBeVisible();
    await user.type(field, "otra");
    await user.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Contraseña incorrecta.");
    expect(assign).not.toHaveBeenCalled();
    await user.clear(field);
    await user.type(field, "la-buena-contrasena");
    await user.click(screen.getByRole("button", { name: "Entrar" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("http://localhost:3000/teams"));
    expect(fetch.mock.calls.every(([url]) => String(url) === "/api/v1/session")).toBe(true);
    expect(JSON.stringify(window.localStorage)).not.toContain("la-buena-contrasena");
    expect(JSON.stringify(window.sessionStorage)).not.toContain("la-buena-contrasena");
  });

  it("says when there were too many attempts, and when access is not configured", async () => {
    api((url, init) => init?.method === "POST" ? { status: 429, body: { error: { code: "TOO_MANY_REQUESTS", message: "x" } } }
      : { body: { required: true, signedIn: false } });
    const { unmount } = render(<AccessView next="/" />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Contraseña"), "lo-que-sea");
    await user.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Demasiados intentos. Espera unos minutos.");
    unmount();
    api(() => ({ status: 503, body: { error: { code: "UPSTREAM_UNAVAILABLE", message: "x" } } }));
    render(<AccessView next="/" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("El acceso no está configurado en esta instalación.");
    expect(screen.queryByLabelText("Contraseña")).toBeNull();
  });
});

describe("SessionControl (top bar)", () => {
  it("closes the session and goes to the access screen", async () => {
    const fetch = api((url, init) => init?.method === "DELETE" ? { status: 204 } : { body: { required: true, signedIn: true } });
    render(<SessionControl />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Cerrar sesión" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("http://localhost:3000/acceso"));
    expect(fetch.mock.calls.some(([url, init]) => String(url) === "/api/v1/session" && init?.method === "DELETE")).toBe(true);
  });

  it("is not shown in an open demonstration", async () => {
    const fetch = api(() => ({ body: { required: false, signedIn: true } }));
    const { container } = render(<SessionControl />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
