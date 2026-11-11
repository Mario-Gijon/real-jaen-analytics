import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { LinkedTableRow } from "@/ui/LinkedTableRow";
import Link from "next/link";

it("activates the native link from a row cell and preserves navigation modifiers", () => {
  const click = vi.fn(event => event.preventDefault());
  render(<table><tbody><LinkedTableRow><th><Link href="/matches/m1" onClick={click}>Rival</Link></th><td>2–0</td></LinkedTableRow></tbody></table>);
  fireEvent.click(screen.getByText("2–0"), { ctrlKey: true });
  expect(click).toHaveBeenCalledTimes(1);
  expect(click.mock.calls[0][0].ctrlKey).toBe(true);
  fireEvent.click(screen.getByRole("link"));
  expect(click).toHaveBeenCalledTimes(2);
  screen.getByRole("link").focus();
  expect(screen.getByRole("link")).toHaveFocus();
});

it("keeps a team link independent and delegates blank cells to the match result", () => {
  const teamClick = vi.fn(event => event.preventDefault());
  const matchClick = vi.fn(event => event.preventDefault());
  render(<table><tbody><LinkedTableRow tabIndex={0}>
    <th><Link href="/teams?team=rival" onClick={teamClick}>Rival</Link></th>
    <td><Link href="/matches/m1" data-row-primary="" onClick={matchClick}>2–0</Link></td>
    <td>Finalizado</td>
  </LinkedTableRow></tbody></table>);
  const row = screen.getByRole("row");
  expect(row.querySelectorAll("a a")).toHaveLength(0);
  fireEvent.click(screen.getByRole("link", { name: "Rival" }));
  expect(teamClick).toHaveBeenCalledTimes(1);
  expect(matchClick).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Finalizado"), { metaKey: true });
  expect(matchClick).toHaveBeenCalledTimes(1);
  expect(matchClick.mock.calls[0][0].metaKey).toBe(true);
  row.focus();
  fireEvent.keyDown(row, { key: "Enter" });
  expect(matchClick).toHaveBeenCalledTimes(2);
});
