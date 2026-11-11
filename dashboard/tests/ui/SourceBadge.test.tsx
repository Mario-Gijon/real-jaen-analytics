import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SourceBadge } from "@/ui/SourceBadge";

function answer(body: unknown, status = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), { status })),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("SourceBadge", () => {
  it("labels synthetic data, as told by the API", async () => {
    answer({ synthetic: true, label: "Datos sintéticos" });
    render(<SourceBadge />);
    expect(await screen.findByText("Datos sintéticos")).toBeVisible();
    expect(fetch).toHaveBeenCalledWith("/api/v1/source", expect.anything());
  });

  it("claims nothing for real data or when the API does not answer", async () => {
    answer({ synthetic: false, label: "Datos del club" });
    const { container, unmount } = render(<SourceBadge />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    unmount();

    answer({ error: { code: "UNAVAILABLE", message: "Sin fuente" } }, 503);
    const failed = render(<SourceBadge />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(failed.container).toBeEmptyDOMElement();
  });
});
