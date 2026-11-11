// @vitest-environment node
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

const root = fileURLToPath(new URL("../../", import.meta.url));
const eslint = new ESLint({ cwd: root });

function sourceFiles(dir: string): string[] {
  return readdirSync(join(root, dir), { recursive: true, encoding: "utf8" })
    .filter((file) => /\.tsx?$/.test(file))
    .map((file) => join(root, dir, file));
}

async function restrictedImports(filePath: string, code: string) {
  const [result] = await eslint.lintText(code, { filePath });
  return result.messages.filter((message) => message.ruleId === "no-restricted-imports");
}

/** The boundary UI → client → Next.js BFF (/api/v1) → DataSource is enforced by lint. */
// ESLint loads its whole config on the first lint: allow for a cold start under parallel tests.
describe("architecture boundary", { timeout: 30_000 }, () => {
  it.each([
    ["src/features/matches/MatchList.tsx", 'import { getDataSource } from "@/server/data-source";'],
    [
      "src/ui/Widget.tsx",
      'import { syntheticSource } from "../server/synthetic/synthetic-source";',
    ],
    ["src/app/matches/page.tsx", 'import season from "../../../data/synthetic/season.json";'],
    ["src/client/api.ts", 'import { getDataSource } from "@/server/data-source";'],
  ])("%s cannot reach server code or data files", async (filePath, code) => {
    expect(await restrictedImports(filePath, code)).toHaveLength(1);
  });

  it("lets API route handlers use the data source", async () => {
    const code =
      'import { getDataSource } from "@/server/data-source";\nexport const source = getDataSource;';
    expect(await restrictedImports("src/app/api/v1/matches/route.ts", code)).toHaveLength(0);
  });

  it.each([
    ["src/lib/example.ts", 'import { MatchList } from "@/features/matches/MatchList";'],
    ["src/lib/example.ts", 'import { MatchList } from "../../features/matches/MatchList";'],
    ["src/lib/example.ts", 'import { api } from "@/client/api";'],
    ["src/hooks/example.ts", 'import { MatchList } from "@/features/matches/MatchList";'],
    ["src/hooks/example.ts", 'import { TeamCrest } from "@/ui/TeamCrest";'],
  ])("%s cannot depend on application layers", async (filePath, code) => {
    expect(await restrictedImports(filePath, code)).toHaveLength(1);
  });

  it("lets shared hooks use the typed client and shared utilities use contracts", async () => {
    expect(await restrictedImports("src/hooks/example.ts", 'import { api } from "@/client/api";')).toHaveLength(0);
    expect(await restrictedImports("src/lib/example.ts", 'import type { Match } from "@/contract";')).toHaveLength(0);
  });

  it("lets only the synthetic adapter know where the synthetic data live", () => {
    const readers = sourceFiles("src")
      .filter((file) => !file.endsWith(".test.ts"))
      .filter((file) =>
        /data[/\\]synthetic|["']data["'],\s*["']synthetic["']/.test(readFileSync(file, "utf8")),
      )
      .map((file) => relative(root, file).replaceAll("\\", "/"));
    expect(readers.length).toBeGreaterThan(0);
    for (const file of readers) expect(file.startsWith("src/server/synthetic/"), file).toBe(true);
  });

  /**
   * The exceptions are the update control, access session, and existing video-association management route.
   * The sporting data routes stay read-only.
   */
  const UPDATE_ROUTES: Record<string, string[]> = {
    "src/app/api/v1/updates/route.ts": ["POST"],
    "src/app/api/v1/updates/current/route.ts": ["GET"],
    "src/app/api/v1/updates/[updateId]/route.ts": ["GET"],
    "src/app/api/v1/updates/[updateId]/cancel/route.ts": ["POST"],
    "src/app/api/v1/session/route.ts": ["DELETE", "GET", "POST"],
  };
  const VIDEO_ROUTES: Record<string, string[]> = {
    "src/app/api/v1/matches/[matchId]/video/route.ts": ["DELETE", "GET"],
    "src/app/api/v1/matches/[matchId]/video/jobs/route.ts": ["GET", "POST"],
  };

  it("keeps sporting /api/v1 routes read-only, with explicit update, session, and video-management exceptions", async () => {
    const routes = sourceFiles("src/app/api").filter((file) => file.endsWith("route.ts"));
    expect(routes.length).toBeGreaterThanOrEqual(7);
    const updates: string[] = [];
    for (const file of routes) {
      const name = relative(root, file).replaceAll("\\", "/");
      const exported = Object.keys(await import(/* @vite-ignore */ pathToFileURL(file).href)).sort();
      if (name in UPDATE_ROUTES) {
        updates.push(name);
        expect(exported, name).toEqual(UPDATE_ROUTES[name]);
      } else if (name in VIDEO_ROUTES) {
        expect(exported, name).toEqual(VIDEO_ROUTES[name]);
      } else {
        expect(exported, name).toEqual(["GET"]);
      }
    }
    expect(updates.sort()).toEqual(Object.keys(UPDATE_ROUTES).sort());
  });

  it("keeps the server independent from screens and the browser client", async () => {
    expect(
      await restrictedImports("src/server/synthetic/x.ts", 'import { api } from "@/client/api";'),
    ).toHaveLength(1);
    expect(
      await restrictedImports(
        "src/server/synthetic/y.ts",
        'import { AppShell } from "@/ui/AppShell";',
      ),
    ).toHaveLength(1);
  });
});

/*
 * Real data and the demonstration never meet in a screen. Each module is
 * known by the client functions it imports: real (the local API's sections)
 * or demonstration (the synthetic season). Following its imports, only a
 * mode switch (it asks `getSource` and loads nothing itself) may lead to both;
 * a module that loads nothing (shared presentation, pure functions) leads to
 * neither.
 */
const REAL_DATA = ["getObservedPlayers", "getObservedSquad", "getPlayerProfile", "getPlayerObservedMatches",
  "getTeamObservedMatches", "getTeamObservedPerformance", "getObservedMatchDetail"];
const DEMO_DATA = ["getCompetitions", "getSeasonMatches", "getSeasonTeams", "getTeamSeason", "getMatch", "getPlayer"];

type Module = { real: boolean; demo: boolean; switch: boolean; imports: string[] };

function uiModules(): Map<string, Module> {
  const files = [...sourceFiles("src/features"), ...sourceFiles("src/ui")].map((file) => relative(root, file).replaceAll("\\", "/"));
  const modules = new Map<string, Module>();
  for (const file of files) {
    const text = readFileSync(join(root, file), "utf8");
    const client = [...text.matchAll(/import\s*{([^}]*)}\s*from\s*"@\/client\/api"/g)]
      .flatMap((match) => match[1].split(",").map((name) => name.trim().replace(/^type\s+/, "")));
    const imports = [...text.matchAll(/from\s*"((?:\.{1,2}\/|@\/(?:features|ui)\/)[^"]+)"/g)]
      .map((match) => match[1]).filter((path) => !path.endsWith(".css"))
      .map((path) => {
        const base = path.startsWith("@/") ? join("src", path.slice(2)) : join(dirname(file), path);
        const found = [".ts", ".tsx"].map((ext) => base + ext).find((candidate) => existsSync(join(root, candidate)));
        return found?.replaceAll("\\", "/");
      })
      .filter((path): path is string => path !== undefined);
    modules.set(file, { real: client.some((name) => REAL_DATA.includes(name)), demo: client.some((name) => DEMO_DATA.includes(name)),
      switch: client.includes("getSource"), imports });
  }
  return modules;
}

/** The data modes a module leads to, itself or through what it imports. */
function reach(modules: Map<string, Module>, file: string, seen = new Set<string>()): { real: boolean; demo: boolean } {
  const entry = modules.get(file)!;
  seen.add(file);
  return entry.imports.filter((dep) => modules.has(dep) && !seen.has(dep)).reduce((acc, dep) => {
    const sub = reach(modules, dep, seen);
    return { real: acc.real || sub.real, demo: acc.demo || sub.demo };
  }, { real: entry.real, demo: entry.demo });
}

describe("real data and the demonstration stay apart", () => {
  const modules = uiModules();

  it("finds the modes in the screens: real and demonstration modules, and their switches", () => {
    const named = (predicate: (entry: Module) => boolean) => [...modules].filter(([, entry]) => predicate(entry)).map(([file]) => file);
    expect(named((m) => m.real)).toContain("src/features/teams/RealTeamsView.tsx");
    expect(named((m) => m.demo)).toContain("src/features/teams/SyntheticTeamsView.tsx");
    expect(named((m) => m.switch)).toEqual(expect.arrayContaining(["src/features/teams/TeamsView.tsx", "src/features/home/HomeView.tsx",
      "src/features/matches/MatchesView.tsx", "src/features/matches/MatchView.tsx", "src/features/players/PlayerView.tsx"]));
  });

  it("lets only a mode switch lead to both, and a switch loads no data itself", () => {
    const both = [...modules.keys()].filter((file) => { const r = reach(modules, file); return r.real && r.demo; });
    for (const file of both) expect(modules.get(file)!.switch, `${file} lleva a datos reales y de demostración`).toBe(true);
    for (const [file, entry] of modules) if (entry.switch) expect(entry.real || entry.demo, `${file} carga datos`).toBe(false);
  });

  it("keeps shared presentation and pure functions free of either mode's data", () => {
    for (const [file, entry] of modules) {
      if (entry.switch || entry.real || entry.demo) continue;
      const r = reach(modules, file);
      expect(r.real || r.demo, `${file} no carga datos pero lleva a los de un modo`).toBe(false);
    }
  });

  it("keeps the real adapter and the synthetic one independent on the server", () => {
    const crossing = (dir: string, other: string) => sourceFiles(dir)
      .filter((file) => [...readFileSync(file, "utf8").matchAll(/from\s*"([^"]*)"/g)].some(([, path]) => path.split("/").includes(other)))
      .map((file) => relative(root, file));
    expect(crossing("src/server/local-api", "synthetic")).toEqual([]);
    expect(crossing("src/server/synthetic", "local-api")).toEqual([]);
  });
});
