import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { GoalEvent, Lineup, LineupPlayer, MatchDetail, SubstitutionEvent, TeamRef } from "@/contract";
import { MatchLineups } from "@/features/matches/MatchLineups";

const home: TeamRef = { id: "home", name: "Real Jaén CF", shortName: "Real Jaén",
  crestUrl: null, colors: { primary: "#4b2677", secondary: "#ffffff" } };
const away: TeamRef = { ...home, id: "away", name: "CD Rival", shortName: "Rival" };
function member(id: string, name: string, shirtNumber: number | null): LineupPlayer {
  return { player: { id, name, position: "midfielder" }, shirtNumber, slot: null };
}
const starter = member("p1", "Guillermo Marchal", 11);
const bench = member("p2", "Sergio Segura", 18);
const unused = member("p3", "Diego Cabrera", 20);
const rival = member("p4", "Javier Rueda", 7);
const lineup: Lineup = { formation: "4-2-3-1", starters: [starter], bench: [bench, unused] };
const change: SubstitutionEvent = {
  id: "sub", matchId: "m1", type: "substitution", teamId: home.id,
  minute: 66, addedTime: null, clip: null, playerIn: bench.player, playerOut: starter.player,
};
const match: MatchDetail = {
  id: "m1", seasonId: "s1", round: 1, kickoff: null, status: "finished", home, away,
  score: { home: 1, away: 0 }, teamStats: null, playerStats: {}, events: [change],
  lineups: { home: lineup, away: { formation: "4-3-3", starters: [rival], bench: [] } },
};
const goal: GoalEvent = { id: "goal", matchId: match.id, teamId: home.id, minute: 18,
  addedTime: null, clip: null, type: "goal", player: starter.player, assist: null,
  penalty: false, ownGoal: false };
const firstText = (name: string) => screen.getAllByText(name, { exact: true })[0];
const playerRow = (name: string) => firstText(name).closest("li")!;

describe("Match lineup lists", () => {
  it("shows one goal with a single football and no unnecessary count", () => {
    render(<MatchLineups match={{ ...match, events: [goal] }} />);
    const row = playerRow(starter.player.name);
    expect(within(row).getByRole("img", { name: "1 gol" })).toBeVisible();
    expect(row).not.toHaveTextContent(/A \d/);
    expect(playerRow(unused.player.name).querySelector("svg")).toBeNull();
  });

  it("shows several goals as one football plus a count", () => {
    render(<MatchLineups match={{ ...match, events: [goal, { ...goal, id: "goal2", minute: 35 }] }} />);
    const row = playerRow(starter.player.name);
    expect(within(row).getAllByRole("img")).toHaveLength(1);
    expect(within(row).getByRole("img", { name: "2 goles" })).toBeVisible();
    expect(within(row).getByText("2", { exact: true })).toBeVisible();
  });

  it("counts only explicitly recorded assists and scales the textual indicator", () => {
    render(<MatchLineups match={{ ...match, events: [
      { ...goal, assist: unused.player },
      { ...goal, id: "goal2", minute: 35, assist: unused.player },
      { ...goal, id: "unassisted", minute: 50 },
    ] }} />);
    const row = playerRow(unused.player.name);
    expect(within(row).getByRole("img", { name: "2 asistencias" })).toBeVisible();
    expect(within(row).getByText("2", { exact: true })).toBeVisible();
    expect(within(row).queryByRole("img", { name: /gol/ })).toBeNull();
  });

  it("keeps contributions on starters and combines substitute contributions with the entry detail", () => {
    render(<MatchLineups match={{ ...match, events: [
      goal, { ...goal, id: "goal2", minute: 35, player: bench.player, assist: starter.player },
      { ...goal, id: "goal3", minute: 75, player: unused.player, assist: bench.player }, change,
    ] }} />);
    const outgoing = playerRow(starter.player.name);
    expect(within(outgoing).getByRole("img", { name: "1 gol" })).toBeVisible();
    expect(within(outgoing).getByRole("img", { name: "1 asistencia" })).toBeVisible();
    expect(within(outgoing).getByText("1", { exact: true })).toBeVisible();
    expect(within(outgoing).queryByLabelText(/Entra|Sale/)).toBeNull();
    const incoming = playerRow(bench.player.name);
    expect(within(incoming).getByRole("img", { name: "1 gol" })).toBeVisible();
    expect(within(incoming).getByRole("img", { name: "1 asistencia" })).toBeVisible();
    expect(within(incoming).getByLabelText("Entra en el minuto 66′")).toBeVisible();
    expect(incoming).toHaveTextContent("Sale · Guillermo Marchal");
  });

  it("counts a penalty as an individual goal without a penalty label", () => {
    render(<MatchLineups match={{ ...match, events: [{ ...goal, penalty: true }] }} />);
    expect(screen.getByRole("img", { name: "1 gol" })).toBeVisible();
    expect(screen.queryByText("Penalti")).toBeNull();
  });

  it("excludes own goals from individual contributions", () => {
    render(<MatchLineups match={{ ...match, events: [{ ...goal, ownGoal: true }] }} />);
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.queryByLabelText(/asistencia/)).toBeNull();
    expect(firstText(starter.player.name)).toBeVisible();
  });

  it("shows no contribution indicators for players without recorded goals or assists", () => {
    render(<MatchLineups match={match} />);
    expect(screen.queryByRole("img", { name: /gol|asistencia/ })).toBeNull();
    expect(screen.getByRole("img", { name: "Sustituido" })).toBeVisible();
    expect(screen.queryByLabelText(/asistencia/)).toBeNull();
    expect(screen.queryByLabelText("Sale en el minuto 66′")).toBeNull();
    expect(screen.getByLabelText("Entra en el minuto 66′")).toBeVisible();
  });
  it("identifies both teams and separates starters and bench", () => {
    render(<MatchLineups match={match} />);
    expect(screen.getByRole("region", { name: home.name })).toBeVisible();
    expect(screen.getByRole("region", { name: away.name })).toBeVisible();
    expect(within(screen.getByRole("list", { name: `Once inicial de ${home.name}` })).getByText(starter.player.name)).toBeVisible();
    expect(within(screen.getByRole("list", { name: `Once inicial de ${away.name}` })).getByText(rival.player.name)).toBeVisible();
    expect(within(screen.getByRole("list", { name: `Banquillo de ${home.name}` })).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getAllByRole("heading", { name: "Once inicial" })).toHaveLength(2);
    expect(screen.getAllByRole("heading", { name: "Banquillo" })).toHaveLength(2);
    const links = screen.getAllByRole("link");
    expect(links.filter(link => link.getAttribute("href")?.startsWith("/teams"))).toHaveLength(2);
    expect(links.filter(link => link.getAttribute("href")?.startsWith("/players"))).toHaveLength(5);
    expect(document.querySelectorAll("a a")).toHaveLength(0);
  });

  it("links starter and bench names by their own IDs and the match season", () => {
    render(<MatchLineups match={{ ...match, seasonId: "season-other" }} />);
    for (const member of [starter, bench, unused, rival]) {
      const link = screen.getAllByRole("link", { name: member.player.name })[0];
      expect(link).toHaveAttribute("href", `/players/${member.player.id}?season=season-other&tab=matches&match=m1&from=match&origin=m1&originTab=lineups`);
      expect(link).toHaveTextContent(member.player.name);
      expect(link.closest("li")).not.toBe(link);
    }
  });

  it("shows no substitution symbol for a starter who completes the match", () => {
    render(<MatchLineups match={{ ...match, events: [] }} />);
    expect(screen.queryByRole("img", { name: "Sustituido" })).toBeNull();
  });

  it.each([
    [false, false], [true, false], [false, true], [true, true],
  ])("marks a substituted starter compactly with goals=%s and assists=%s", (scored, assisted) => {
    const events: MatchDetail["events"] = [change];
    if (scored) events.push(goal);
    if (assisted) events.push({ ...goal, id: "assisted-goal", player: bench.player, assist: starter.player });
    render(<MatchLineups match={{ ...match, events }} />);
    const starterRow = playerRow(starter.player.name);
    expect(within(starterRow).getByRole("img", { name: "Sustituido" })).toBeVisible();
    expect(within(starterRow).queryByRole("img", { name: "1 gol" }) !== null).toBe(scored);
    expect(within(starterRow).queryByRole("img", { name: "1 asistencia" }) !== null).toBe(assisted);
    expect(starterRow).not.toHaveTextContent(/66|Sale|Sergio Segura/);
    const benchRow = playerRow(bench.player.name);
    expect(within(benchRow).queryByRole("img", { name: "Sustituido" })).toBeNull();
    expect(within(benchRow).getByLabelText("Entra en el minuto 66′")).toBeVisible();
    expect(benchRow).toHaveTextContent("Sale · Guillermo Marchal");
  });

  it("shows available shirt numbers, general positions and text formations", () => {
    render(<MatchLineups match={match} />);
    expect(screen.getByLabelText("Dorsal 11")).toHaveTextContent("11");
    expect(screen.getAllByLabelText("Centrocampista")[0]).toHaveTextContent("MED");
    expect(screen.getByText("Formación: 4-2-3-1")).toBeVisible();
    expect(screen.getByText("Formación: 4-3-3")).toBeVisible();
  });

  it("preserves missing shirt number and position while retaining the player", () => {
    const missing: LineupPlayer = { ...starter, shirtNumber: null, player: { ...starter.player, position: null } };
    render(<MatchLineups match={{ ...match, lineups: { home: { ...lineup, starters: [missing] }, away: match.lineups!.away } }} />);
    expect(screen.getByLabelText("Dorsal no disponible")).toHaveTextContent("—");
    expect(screen.getByLabelText("Posición no disponible")).toHaveTextContent("—");
    expect(firstText(starter.player.name)).toBeVisible();
  });

  it("keeps the announced order regardless of formation and tactical slots", () => {
    const starters: LineupPlayer[] = [
      { ...starter, slot: { line: 4, order: 2 } },
      { ...unused, slot: { line: 0, order: 0 } },
      bench,
    ];
    render(<MatchLineups match={{ ...match, lineups: {
      home: { formation: null, starters, bench: [] }, away: match.lineups!.away,
    } }} />);
    const region = screen.getByRole("region", { name: home.name });
    expect(within(region).queryByText(/Formación/)).toBeNull();
    const rows = within(region).getByRole("list", { name: `Once inicial de ${home.name}` }).querySelectorAll("li");
    expect([...rows].map(row => row.textContent?.includes(starters[0].player.name))).toEqual([true, false, false]);
    expect(rows[1]).toHaveTextContent(unused.player.name);
    expect(rows[2]).toHaveTextContent(bench.player.name);
  });

  it("shows the minute and replaced player only on the incoming bench player's row", () => {
    render(<MatchLineups match={match} />);
    const outgoing = playerRow(starter.player.name);
    const incoming = playerRow(bench.player.name);
    expect(within(outgoing).queryByLabelText(/Entra|Sale/)).toBeNull();
    expect(outgoing).not.toHaveTextContent("66′");
    expect(within(incoming).getByLabelText("Entra en el minuto 66′")).toBeVisible();
    expect(incoming).toHaveTextContent("Sale · Guillermo Marchal");
    expect(playerRow(unused.player.name)).not.toHaveTextContent("66′");
    expect(within(playerRow(unused.player.name)).queryByLabelText(/Entra|Sale/)).toBeNull();
  });

  it.each([[45, 2], [90, 3]])("preserves %s+%s added time on entries", (minute, addedTime) => {
    render(<MatchLineups match={{ ...match, events: [{ ...change, minute, addedTime }] }} />);
    expect(screen.getByLabelText(`Entra en el minuto ${minute}+${addedTime}′`)).toBeVisible();
    expect(screen.queryByLabelText(/Sale en el minuto/)).toBeNull();
    expect(screen.getByLabelText(`Entra en el minuto ${minute}+${addedTime}′`).closest("li")).toHaveTextContent("Sale · Guillermo Marchal");
  });

  it("keeps the entry relation when a substitute later leaves, without an outgoing mark", () => {
    render(<MatchLineups match={{ ...match, events: [
      { ...change, id: "later", minute: 80, playerOut: bench.player, playerIn: unused.player }, change,
    ] }} />);
    const row = playerRow(bench.player.name);
    expect(within(row).getByLabelText("Entra en el minuto 66′")).toBeVisible();
    expect(within(row).queryByLabelText(/Sale en el minuto/)).toBeNull();
    const later = playerRow(unused.player.name);
    expect(within(later).getByLabelText("Entra en el minuto 80′")).toBeVisible();
    expect(later).toHaveTextContent("Sale · Sergio Segura");
  });

  it("matches the incoming player by ID and reads the replaced player directly from the event", () => {
    render(<MatchLineups match={{ ...match, events: [{ ...change,
      playerIn: { ...bench.player, name: "Nombre del evento" },
      playerOut: { ...rival.player, name: "Jugador sustituido registrado" },
    }] }} />);
    const incoming = playerRow(bench.player.name);
    expect(incoming).toHaveTextContent("Sale · Jugador sustituido registrado");
    expect(within(incoming).getByLabelText("Entra en el minuto 66′")).toBeVisible();
    expect(within(incoming).queryByRole("link", { name: "Guillermo Marchal" })).toBeNull();
  });

  it("associates substitution annotations only with their team", () => {
    render(<MatchLineups match={{ ...match, events: [{ ...change, teamId: away.id }] }} />);
    expect(screen.queryByLabelText(/Entra en el minuto|Sale en el minuto/)).toBeNull();
  });

  it.each([
    ["scheduled", "Las alineaciones todavía no están disponibles."],
    ["postponed", "Partido aplazado. Las alineaciones no están disponibles."],
    ["finished", "No hay alineaciones disponibles para este partido."],
  ] as const)("explains missing lineups for %s without empty columns", (status, message) => {
    render(<MatchLineups match={{ ...match, status, lineups: null }} />);
    expect(screen.getByRole("status")).toHaveTextContent(message);
    expect(screen.queryByRole("list")).toBeNull();
    expect(screen.queryByRole("region")).toBeNull();
  });
});
