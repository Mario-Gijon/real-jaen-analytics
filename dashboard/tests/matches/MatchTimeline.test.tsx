import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { GoalEvent, MatchDetail, MatchEvent, PlayerRef, TeamRef } from "@/contract";
import { MatchTimeline } from "@/features/matches/MatchTimeline";

const home: TeamRef = { id: "home", name: "Real Jaén CF", shortName: "Real Jaén",
  crestUrl: null, colors: { primary: "#4b2677", secondary: "#ffffff" } };
const away: TeamRef = { ...home, id: "away", name: "CD Rival", shortName: "Rival" };
const player: PlayerRef = { id: "p1", name: "García", position: "forward" };
const other: PlayerRef = { ...player, id: "p2", name: "Pérez" };
const base = { id: "e1", matchId: "m1", teamId: home.id, minute: 18, addedTime: null, clip: null };
const goal: GoalEvent = { ...base, type: "goal", player, assist: other, penalty: false, ownGoal: false };
const match: MatchDetail = { id: "m1", seasonId: "s1", round: 1, kickoff: null,
  status: "finished", home, away, score: { home: 1, away: 0 },
  teamStats: null, lineups: null, playerStats: {}, events: [goal] };

describe("Match chronology", () => {
  it("orders minutes and added time, retaining source order for ties without mutating events", () => {
    const events: MatchEvent[] = [
      { ...goal, id: "late", minute: 90, addedTime: 3 },
      { ...goal, id: "tie-z", minute: 45, addedTime: 2, player: { ...player, name: "Primero" } },
      { ...goal, id: "early", minute: 18 },
      { ...goal, id: "tie-a", minute: 45, addedTime: 2, player: { ...player, name: "Segundo" } },
      { ...goal, id: "regular", minute: 45 },
    ];
    render(<MatchTimeline match={{ ...match, events }} />);
    const rows = within(screen.getByRole("list", { name: "Eventos del partido" })).getAllByRole("listitem");
    expect(rows.map(row => row.textContent?.match(/\d+(?:\+\d+)?′/)?.[0])).toEqual(["18′", "45′", "45+2′", "45+2′", "90+3′"]);
    expect(rows[2]).toHaveTextContent("Primero");
    expect(rows[3]).toHaveTextContent("Segundo");
    expect(events.map(e => e.id)).toEqual(["late", "tie-z", "early", "tie-a", "regular"]);
  });

  it("shows a goal and assist on the actor team's side", () => {
    render(<MatchTimeline match={match} />);
    expect(screen.getByText("García")).toBeVisible();
    expect(screen.getByRole("img", { name: "Gol" })).toBeVisible();
    expect(screen.queryByText("Gol · García")).toBeNull();
    expect(screen.getByRole("link", { name: "Pérez" }).closest("p")).toHaveTextContent("Asist. Pérez");
    expect(screen.getByRole("link", { name: "García" })).toHaveAttribute("href", "/players/p1?season=s1&tab=matches&match=m1&from=match&origin=m1&originTab=timeline");
    expect(screen.getByRole("link", { name: "Pérez" })).toHaveAttribute("href", "/players/p2?season=s1&tab=matches&match=m1&from=match&origin=m1&originTab=timeline");
    expect(screen.getByRole("listitem", { name: "Real Jaén CF · Local" })).toHaveAttribute("data-side", "home");
    expect(screen.queryByText("Penalti")).toBeNull();
  });

  it("identifies a penalty without inventing an assist", () => {
    render(<MatchTimeline match={{ ...match, events: [{ ...goal, teamId: away.id, penalty: true, assist: null }] }} />);
    expect(screen.getByText("Penalti")).toBeVisible();
    expect(screen.getByRole("listitem", { name: "CD Rival · Visitante" })).toHaveAttribute("data-side", "away");
    expect(screen.queryByText(/Asist\./)).toBeNull();
  });

  it.each(["home", "away"] as const)("places an own goal by %s on the beneficiary side and identifies its author", (actorSide) => {
    const beneficiary = actorSide === "home" ? "away" : "home";
    render(<MatchTimeline match={{ ...match, events: [{ ...goal, teamId: match[actorSide].id, ownGoal: true, assist: null }] }} />);
    const row = screen.getByRole("listitem");
    expect(row).toHaveAttribute("data-side", beneficiary);
    expect(row).toHaveAccessibleName(`${match[beneficiary].name} · ${beneficiary === "home" ? "Local" : "Visitante"}`);
    expect(row).toHaveTextContent(`Propia puerta · ${match[actorSide].shortName}`);
    expect(row).toHaveTextContent("García");
    expect(within(row).getByRole("link", { name: "García" })).toHaveAttribute("href", "/players/p1?season=s1&tab=matches&match=m1&from=match&origin=m1&originTab=timeline");
    expect(screen.queryByText(/Asist\./)).toBeNull();
  });

  it.each([
    ["yellow", "Amarilla"], ["red", "Roja"], ["second_yellow", "Segunda amarilla"],
  ] as const)("distinguishes the %s card", (card, label) => {
    render(<MatchTimeline match={{ ...match, events: [{ ...base, type: "card", card, player }] }} />);
    expect(screen.getByText(label)).toBeVisible();
    expect(screen.getByText("García")).toBeVisible();
    expect(screen.getByRole("link", { name: "García" })).toHaveAttribute("href", "/players/p1?season=s1&tab=matches&match=m1&from=match&origin=m1&originTab=timeline");
  });

  it("identifies the incoming and outgoing player and substitution minute", () => {
    render(<MatchTimeline match={{ ...match, events: [{ ...base, minute: 61, type: "substitution", playerIn: player, playerOut: other }] }} />);
    expect(screen.getByText("61′")).toBeVisible();
    const incoming = screen.getByRole("link", { name: "García" });
    const outgoing = screen.getByRole("link", { name: "Pérez" });
    expect(incoming.parentElement).toHaveTextContent("Entra · García");
    expect(outgoing.parentElement).toHaveTextContent("Sale · Pérez");
    expect(incoming).toHaveAttribute("href", "/players/p1?season=s1&tab=matches&match=m1&from=match&origin=m1&originTab=timeline");
    expect(outgoing).toHaveAttribute("href", "/players/p2?season=s1&tab=matches&match=m1&from=match&origin=m1&originTab=timeline");
  });

  it("uses the current match season and leaves unidentified names as text", () => {
    render(<MatchTimeline match={{ ...match, seasonId: "season-other", events: [{ ...goal,
      player: { ...player, id: "" }, assist: other }] }} />);
    expect(screen.getByText(player.name)).toBeVisible();
    expect(screen.queryByRole("link", { name: player.name })).toBeNull();
    expect(screen.getByRole("link", { name: other.name })).toHaveAttribute("href", "/players/p2?season=season-other&tab=matches&match=m1&from=match&origin=m1&originTab=timeline");
    const homeLinks = screen.getAllByRole("link", { name: "Real Jaén" });
    expect(homeLinks).toHaveLength(2);
    expect(homeLinks.every(link => link.getAttribute("href") === "/teams?season=season-other&team=home")).toBe(true);
    expect(homeLinks.every(link => link.textContent === "Real Jaén")).toBe(true);
    expect(screen.getByRole("link", { name: "Rival" })).toHaveAttribute("href", "/teams?season=season-other&team=away");
    expect(screen.getByLabelText("Equipos del partido")).toHaveTextContent("Real Jaén · Local");
    expect(screen.getByLabelText("Equipos del partido")).toHaveTextContent("Rival · Visitante");
    expect(screen.getAllByRole("link")).toHaveLength(4);
  });

  it.each([
    ["scheduled", "El partido todavía no se ha disputado."],
    ["postponed", "Partido aplazado. No hay cronología disponible."],
    ["finished", "No se registraron eventos del partido."],
  ] as const)("explains the empty state for %s", (status, text) => {
    render(<MatchTimeline match={{ ...match, status, events: [], score: status === "finished" ? match.score : null }} />);
    expect(screen.getByRole("status")).toHaveTextContent(text);
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("shows cards and substitutions even in a 0–0 draw", () => {
    render(<MatchTimeline match={{ ...match, score: { home: 0, away: 0 }, events: [
      { ...base, type: "card", player, card: "yellow" },
      { ...base, id: "sub", minute: 61, type: "substitution", playerIn: other, playerOut: player },
    ] }} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryByRole("status")).toBeNull();
  });
});
