import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LoadingState } from "@/ui/LoadingState";

describe("LoadingState", () => {
  it.each(["page", "section", "inline"] as const)("announces its contextual label for the %s variant", (variant) => {
    render(<LoadingState variant={variant} label="Cargando partidos del rival…" />);

    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Cargando partidos del rival…");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveAttribute("aria-atomic", "true");
    expect(status).toHaveAttribute("data-variant", variant);
    expect(status.querySelector("[aria-hidden='true'] svg")).toHaveAttribute("focusable", "false");
  });
});
