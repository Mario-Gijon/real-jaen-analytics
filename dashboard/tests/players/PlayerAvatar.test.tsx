import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PlayerAvatar } from "@/ui/PlayerAvatar";

afterEach(() => vi.unstubAllEnvs());

describe("PlayerAvatar", () => {
  it("uses initials without a photo and falls back to the same initials after an image error", () => {
    const { rerender } = render(<PlayerAvatar name="Moha Keita" />);
    expect(screen.getByText("MK")).toBeVisible();

    rerender(<PlayerAvatar name="Moha Keita" src="/api/v1/players/sofascore-player-1/photo/abc" />);
    const image = document.querySelector("img")!;
    expect(image).toHaveAttribute("src", "/api/v1/players/sofascore-player-1/photo/abc");
    fireEvent.error(image);
    expect(screen.getByText("MK")).toBeVisible();
    expect(document.querySelector("img")).toBeNull();
  });

  it("prefixes the same-origin photo route with the production basePath", () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", "/real-jaen");
    render(<PlayerAvatar name="Moha Keita" src="/api/v1/players/sofascore-player-1/photo/abc" />);
    expect(document.querySelector("img")).toHaveAttribute("src", "/real-jaen/api/v1/players/sofascore-player-1/photo/abc");
  });
});
