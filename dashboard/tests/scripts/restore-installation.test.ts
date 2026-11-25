// @vitest-environment node
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const repositoryRoot = fileURLToPath(new URL("../../../", import.meta.url));

function fixture(existingVolume = "", failAfterFirstVolume = false, activeMount = "", modelVideoCopyUp = false) {
  const root = mkdtempSync(join(tmpdir(), "rja-restore-script-"));
  const bin = join(root, "bin");
  const imports = join(root, "imports");
  const backup = join(imports, "backup");
  const catalogParent = join(root, "catalogs");
  const catalog = join(catalogParent, "restored-catalog");
  mkdirSync(bin);
  mkdirSync(backup, { recursive: true });
  mkdirSync(catalogParent);
  writeFileSync(join(backup, "manifest.json"), JSON.stringify({
    formato_respaldo: 2,
    fotos: { formato: 1, incluidas: false },
    catalogo_video: { formato: 1, incluida: true },
  }));
  const port = 33401;
  const prefix = "rja-isolated-test";
  const project = "real-jaen-analytics-restore-test";
  const config = {
    name: project,
    services: {
      "sports-data": { image: "local/rja/sports:restore", volumes: [
        { type: "volume", source: "sports-state", target: "/state" },
        { type: "bind", source: imports, target: "/imports", read_only: true },
      ] },
      acquisition: { image: "local/rja/acquisition:restore", volumes: [
        { type: "volume", source: "acquisition-jobs", target: "/acquisition-jobs" },
        { type: "volume", source: "acquisition-profiles", target: "/profiles" },
      ] },
      "video-service": { image: "local/rja/video:restore", environment: { VIDEO_SERVICE_MANIFEST: "videos.json" }, volumes: [
        { type: "volume", source: "video-state", target: "/state" },
        { type: "bind", source: catalog, target: "/app/data", read_only: true },
      ] },
      dashboard: { ports: [{ target: 3000, published: String(port), host_ip: "127.0.0.1" }], volumes: [
        { type: "volume", source: "dashboard-next", target: "/app/.next" },
      ] },
    },
    volumes: Object.fromEntries(["sports-state", "acquisition-jobs", "acquisition-profiles", "video-state", "dashboard-next"]
      .map((name) => [name, { name: `${prefix}-${name}` }])),
    networks: { edge: { name: `${prefix}-edge` } },
  };
  const configPath = join(root, "compose.json");
  writeFileSync(configPath, JSON.stringify(config));
  const markerPath = join(imports, ".real-jaen-restore-incomplete");
  const callsPath = join(root, "docker-calls.log");
  const videoFirstMountPath = join(root, "video-volume-first-mount");
  const videoCopyUpPath = join(root, "video-volume-image-copy-up");
  const fakeDocker = join(bin, "docker");
  const fakePython = join(bin, "python3");
  writeFileSync(fakePython, `#!/bin/sh
case "$*" in *--check-dashboard-port*) exit 0 ;; esac
exec "$REAL_PYTHON" "$@"
`);
  chmodSync(fakePython, 0o755);
  writeFileSync(fakeDocker, `#!/bin/sh
printf '%s\\n' "$*" >> "$DOCKER_CALLS"
if [ "$1" = compose ] && [ "$*" != "" ]; then
  case "$*" in
    *"config --images sports-data"*) echo local/rja/sports:restore; exit 0 ;;
    *"config --format json"*) cat "$COMPOSE_JSON"; exit 0 ;;
  esac
fi
case "$*" in
  *instalacion_compose.py*)
    printf '%s\\n' \\
      "project=$PROJECT" 'sports_data_image=local/rja/sports:restore' \\
      'acquisition_image=local/rja/acquisition:restore' 'video_service_image=local/rja/video:restore' \\
      "sports_state=$PREFIX-sports-state" "acquisition_jobs=$PREFIX-acquisition-jobs" \\
      "acquisition_profiles=$PREFIX-acquisition-profiles" "video_state=$PREFIX-video-state" \\
      "imports=$IMPORTS" "video_catalog_dir=$CATALOG" 'video_manifest=videos.json' \\
      "volume_names=$PREFIX-acquisition-jobs,$PREFIX-acquisition-profiles,$PREFIX-dashboard-next,$PREFIX-sports-state,$PREFIX-video-state" \\
      "network_names=$PREFIX-edge" 'dashboard_bind=127.0.0.1' "dashboard_port=$PORT"
    exit 0 ;;
  'volume inspect '*) last=; for argument do last=$argument; done; [ "\${EXISTING_VOLUME:-}" = "$last" ] && exit 0; exit 1 ;;
  'network inspect '*) exit 1 ;;
  'ps -aq --filter label=com.docker.compose.project='*) exit 0 ;;
  'ps -aq'*) [ -z "$ACTIVE_MOUNT" ] || echo active-container; exit 0 ;;
  *Mounts* ) [ -z "$ACTIVE_MOUNT" ] || echo "$ACTIVE_MOUNT"; exit 0 ;;
  'info'|'image inspect '*) exit 0 ;;
  'volume create '*) [ -f "$RESTORE_MARKER" ] || exit 91; echo 'marker-present' >> "$DOCKER_CALLS"; exit 0 ;;
  'run '*)
    if [ "$MODEL_VIDEO_COPY_UP" = 1 ]; then
      case "$*" in
        *"source=$PREFIX-video-state,target=/state"*)
          if [ ! -f "$VIDEO_FIRST_MOUNT" ]; then
            touch "$VIDEO_FIRST_MOUNT"
            case "$*" in *volume-nocopy*) ;; *) touch "$VIDEO_COPY_UP" ;; esac
          fi ;;
      esac
      case "$*" in
        *"stage = root / '.restore-state'"*)
          if [ -f "$VIDEO_COPY_UP" ]; then echo 'FileExistsError: /video/calibrations' >&2; exit 88; fi ;;
      esac
    fi
    if [ "\${FAIL_AFTER_FIRST_VOLUME:-0}" = 1 ] && [ -f "$RESTORE_MARKER" ]; then
      case "$*" in *"source=$PREFIX-sports-state,target=/state"*) exit 92 ;; esac
    fi
    exit 0 ;;
esac
echo "unexpected fake docker call: $*" >&2
exit 93
`);
  chmodSync(fakeDocker, 0o755);
  const envFile = join(root, ".env.prod");
  writeFileSync(envFile, "COMPOSE_PROJECT_NAME=real-jaen-analytics-restore-test\n");
  const env = {
    ...process.env,
    PATH: `${bin}:${process.env.PATH ?? ""}`,
    DOCKER_CALLS: callsPath,
    COMPOSE_JSON: configPath,
    PROJECT: project,
    PREFIX: prefix,
    IMPORTS: imports,
    CATALOG: catalog,
    PORT: String(port),
    RESTORE_MARKER: markerPath,
    EXISTING_VOLUME: existingVolume,
    FAIL_AFTER_FIRST_VOLUME: failAfterFirstVolume ? "1" : "0",
    MODEL_VIDEO_COPY_UP: modelVideoCopyUp ? "1" : "0",
    VIDEO_FIRST_MOUNT: videoFirstMountPath,
    VIDEO_COPY_UP: videoCopyUpPath,
    ACTIVE_MOUNT: activeMount === "imports" ? imports : activeMount,
    REAL_PYTHON: spawnSync("which", ["python3"], { encoding: "utf8" }).stdout.trim(),
    ENV_FILE: envFile,
    RJ_RESTORE_VIDEO_CATALOG_DIR: catalog,
  };
  const result = spawnSync("sh", [join(repositoryRoot, "dashboard/scripts/restaurar-instalacion.sh"), backup], {
    cwd: repositoryRoot, env, encoding: "utf8",
  });
  return { root, imports, markerPath, callsPath, result, prefix };
}

describe("installation restore script", () => {
  it("validates first and creates the external marker before its first target volume", () => {
    const test = fixture();
    try {
      expect(test.result.status, test.result.stderr + test.result.stdout).toBe(0);
      const calls = readFileSync(test.callsPath, "utf8").split("\n").filter(Boolean);
      const firstVolume = calls.findIndex((call) => call.startsWith("volume create "));
      const packageValidation = calls.findIndex((call) => call.includes("restauracion_backup.py validar"));
      const videoValidation = calls.findIndex((call) => call.includes("video-catalog-respaldo.py validar"));
      expect(packageValidation).toBeGreaterThan(-1);
      expect(videoValidation).toBeGreaterThan(-1);
      expect(packageValidation).toBeLessThan(firstVolume);
      expect(videoValidation).toBeLessThan(firstVolume);
      expect(calls[firstVolume]).toContain(`com.docker.compose.volume=sports-state`);
      expect(calls[firstVolume]).toContain(`${test.prefix}-sports-state`);
      expect(calls[firstVolume + 1]).toBe("marker-present");
      expect(calls.filter((call) => call.startsWith("volume create "))).toHaveLength(4);
      const videoStateArchive = calls.findIndex((call) => call.includes("video-service-state.tar"));
      const videoStateMerge = calls.findIndex((call) => call.includes("stage = root / '.restore-state'"));
      const videoGuardRemoval = calls.findIndex((call) => call.includes("Path('/state/.real-jaen-restore-incomplete').unlink()"));
      const acquisitionJobsRestore = calls.findIndex((call) => call.includes("acquisition-jobs.tar"));
      const coordinatorCheck = calls.findIndex((call) => call.includes("Generation accepted; Acquisition SQLite integrity verified."));
      expect(videoStateArchive).toBeGreaterThan(firstVolume);
      expect(calls[videoStateArchive]).toContain("--destination /video/.restore-state");
      expect(videoStateMerge).toBeGreaterThan(videoStateArchive);
      expect(videoGuardRemoval).toBeGreaterThan(videoStateMerge);
      expect(acquisitionJobsRestore).toBeGreaterThan(firstVolume);
      expect(acquisitionJobsRestore).toBeLessThan(coordinatorCheck);
      expect(test.result.stdout).toContain("sin arrancar servicios");
    } finally {
      rmSync(test.root, { recursive: true, force: true });
    }
  });

  it("uses volume-nocopy on fresh volumes so image calibrations cannot collide with restored state", () => {
    // Model Docker's copy-up: the Video Service image contains /state/calibrations,
    // which would make the later guarded restore merge fail without volume-nocopy.
    const test = fixture("", false, "", true);
    try {
      expect(test.result.status, test.result.stderr + test.result.stdout).toBe(0);
      const calls = readFileSync(test.callsPath, "utf8").split("\n").filter(Boolean);
      const sportsMarker = calls.find((call) => call.includes(`source=${test.prefix}-sports-state,target=/state`));
      const videoMarker = calls.find((call) => call.includes(`source=${test.prefix}-video-state,target=/state`));
      const volumeOwnership = calls.find((call) => call.includes("os.chown(p,10001,10001)"));
      expect(sportsMarker).toContain("volume-nocopy");
      expect(videoMarker).toContain("volume-nocopy");
      expect(volumeOwnership).toContain(`source=${test.prefix}-acquisition-jobs,target=/jobs,volume-nocopy`);
      expect(volumeOwnership).toContain(`source=${test.prefix}-acquisition-profiles,target=/profiles,volume-nocopy`);
      expect(volumeOwnership).toContain(`source=${test.prefix}-sports-state,target=/state,volume-nocopy`);
      expect(volumeOwnership).toContain(`source=${test.prefix}-video-state,target=/video,volume-nocopy`);
    } finally {
      rmSync(test.root, { recursive: true, force: true });
    }
  });

  it("refuses existing volumes and retains marker plus first volume after interruption", () => {
    const existing = fixture("rja-isolated-test-sports-state");
    try {
      expect(existing.result.status).not.toBe(0);
      expect(existing.result.stderr).toContain("volumen destino ya existe");
      expect(readFileSync(existing.callsPath, "utf8")).not.toContain("volume create");
      expect(() => readFileSync(existing.markerPath)).toThrow();
    } finally {
      rmSync(existing.root, { recursive: true, force: true });
    }

    const interrupted = fixture("", true);
    try {
      expect(interrupted.result.status).not.toBe(0);
      expect(interrupted.result.stderr).toContain("Restauración incompleta");
      expect(readFileSync(interrupted.markerPath, "utf8")).toContain("project=real-jaen-analytics-restore-test");
      const calls = readFileSync(interrupted.callsPath, "utf8").split("\n").filter(Boolean);
      const volumeCreates = calls.filter((call) => call.startsWith("volume create "));
      expect(volumeCreates).toHaveLength(1);
      expect(volumeCreates[0]).toContain("com.docker.compose.volume=sports-state");
      expect(volumeCreates[0]).toContain(`${interrupted.prefix}-sports-state`);
      expect(calls).not.toContain("volume rm");
    } finally {
      rmSync(interrupted.root, { recursive: true, force: true });
    }
  });

  it("refuses an imports path mounted by another active container before creating volumes", () => {
    const mounted = fixture("", false, "imports");
    try {
      expect(mounted.result.status).not.toBe(0);
      expect(mounted.result.stderr).toContain("imports se solapa con un montaje activo");
      expect(readFileSync(mounted.callsPath, "utf8")).not.toContain("volume create");
      expect(() => readFileSync(mounted.markerPath)).toThrow();
    } finally {
      rmSync(mounted.root, { recursive: true, force: true });
    }
  });
});
