// @vitest-environment node
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const repositoryRoot = fileURLToPath(new URL("../../../", import.meta.url));

describe("installation restore script", () => {
  it("makes each service volume writable before restoring its non-root-owned data", () => {
    const temporaryRoot = mkdtempSync(join(tmpdir(), "rja-restore-script-"));
    try {
      const bin = join(temporaryRoot, "bin");
      const imports = join(temporaryRoot, "imports");
      const backup = join(imports, "backup");
      mkdirSync(bin);
      mkdirSync(backup, { recursive: true });

      const files = [
        "real-jaen.sqlite",
        "actualizaciones.tar",
        "acquisition-jobs.tar",
        "video-service-state.tar",
        "manifest.json",
        "snapshot-copy.json",
      ];
      const checksums: string[] = [];
      for (const name of files) {
        const contents = name === "manifest.json" ? '{"generacion":"fixture-generation"}\n' : `${name}\n`;
        writeFileSync(join(backup, name), contents);
        const digest = createHash("sha256").update(contents).digest("hex");
        checksums.push(`${digest}  ./${name}`);
      }
      writeFileSync(join(backup, "SHA256SUMS"), `${checksums.join("\n")}\n`);

      const callsPath = join(temporaryRoot, "docker-calls.log");
      const fakeDocker = join(bin, "docker");
      writeFileSync(
        fakeDocker,
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$DOCKER_CALLS"\nif [ "$1" = volume ] && [ "$2" = inspect ]; then exit 1; fi\nexit 0\n',
      );
      chmodSync(fakeDocker, 0o755);

      const envFile = join(temporaryRoot, ".env.prod");
      writeFileSync(
        envFile,
        [
          "COMPOSE_PROJECT_NAME=rja-restore-test",
          "RJ_PROD_SPORTS_STATE_VOLUME=rja-test-sports",
          "RJ_PROD_ACQUISITION_JOBS_VOLUME=rja-test-jobs",
          "RJ_PROD_ACQUISITION_PROFILES_VOLUME=rja-test-profiles",
          "RJ_PROD_VIDEO_STATE_VOLUME=rja-test-video",
          `RJ_IMPORT_DIR=${imports}`,
          "RJ_SPORTS_DATA_IMAGE=fake:sports-data",
        ].join("\n") + "\n",
      );

      const result = spawnSync(
        "sh",
        [join(repositoryRoot, "dashboard/scripts/restaurar-instalacion.sh"), backup],
        {
          cwd: repositoryRoot,
          encoding: "utf8",
          env: {
            ...process.env,
            ENV_FILE: envFile,
            PATH: `${bin}:${process.env.PATH ?? ""}`,
            DOCKER_CALLS: callsPath,
          },
        },
      );

      expect(result.status, result.stderr || result.stdout).toBe(0);
      const calls = readFileSync(callsPath, "utf8").split("\n").filter(Boolean);
      const acquisitionOwnership = calls.findIndex(
        (call) => call.includes("--entrypoint chown acquisition 10001:10001 /profiles /acquisition-jobs"),
      );
      const videoOwnership = calls.findIndex(
        (call) => call.includes("--entrypoint chown video-service 10001:10001 /state"),
      );
      const acquisitionRestore = calls.findIndex((call) => call.includes("acquisition-jobs.tar"));
      const videoRestore = calls.findIndex((call) => call.includes("video-service-state.tar"));

      expect(acquisitionOwnership).toBeGreaterThan(-1);
      expect(videoOwnership).toBeGreaterThan(-1);
      expect(acquisitionRestore).toBeGreaterThan(acquisitionOwnership);
      expect(videoRestore).toBeGreaterThan(videoOwnership);
    } finally {
      rmSync(temporaryRoot, { recursive: true, force: true });
    }
  });
});
