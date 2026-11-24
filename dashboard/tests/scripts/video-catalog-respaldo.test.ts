// @vitest-environment node
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const temporary: string[] = [];
afterEach(() => { for (const path of temporary.splice(0)) rmSync(path, { recursive: true, force: true }); });

function fixture(manifest: object = {
  schema_version: 1,
  videos: [{ video_id: "sample", match_ref: "match-1", calibration_file: "calibrations/sample.json" }],
}) {
  const directory = mkdtempSync(join(tmpdir(), "rja-video-catalog-backup-"));
  temporary.push(directory);
  const data = join(directory, "data");
  const out = join(directory, "out");
  mkdirSync(join(data, "calibrations"), { recursive: true });
  mkdirSync(out);
  writeFileSync(join(data, "videos.json"), JSON.stringify(manifest));
  writeFileSync(join(data, "calibrations", "sample.json"), JSON.stringify({ segments: [] }));
  writeFileSync(join(data, "original-video.mp4"), "video bytes must stay outside the backup");
  return { directory, data, out };
}

function run(data: string, out: string, manifest = "videos.json") {
  return spawnSync("python3", [join(root, "dashboard/scripts/video-catalog-respaldo.py"), "respaldar",
    "--data", data, "--manifest", manifest, "--out", out], { encoding: "utf8" });
}

describe("video catalog backup", () => {
  it("archives only the configured manifest and referenced calibration files", () => {
    const { data, out } = fixture();
    const result = run(data, out);

    expect(result.status, result.stderr).toBe(0);
    expect(JSON.parse(result.stdout)).toMatchObject({ incluida: true, calibraciones: 1, archivos: 2 });
    const listing = spawnSync("tar", ["-tf", join(out, "video-catalog.tar")], { encoding: "utf8" });
    expect(listing.status).toBe(0);
    expect(listing.stdout.trim().split("\n").sort()).toEqual([
      "video-catalog/calibrations/sample.json", "video-catalog/videos.json",
    ]);
    expect(listing.stdout).not.toContain("original-video.mp4");
    expect(existsSync(join(out, "video-catalog.tar"))).toBe(true);
  });

  it("rejects unsafe calibration paths and sensitive catalog keys", () => {
    const unsafe = fixture({ schema_version: 1,
      videos: [{ video_id: "sample", calibration_file: "../outside.json" }],
    });
    expect(run(unsafe.data, unsafe.out).status).not.toBe(0);
    expect(existsSync(join(unsafe.out, "video-catalog.tar"))).toBe(false);

    const sensitive = fixture({ schema_version: 1, token: "must-not-be-archived", videos: [] });
    const result = run(sensitive.data, sensitive.out);
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain("clave sensible");
    expect(readFileSync(join(sensitive.data, "videos.json"), "utf8")).toContain("must-not-be-archived");
    expect(existsSync(join(sensitive.out, "video-catalog.tar"))).toBe(false);
  });
});
