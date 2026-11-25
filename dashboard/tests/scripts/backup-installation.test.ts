// @vitest-environment node
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

const repositoryRoot = fileURLToPath(new URL("../../../", import.meta.url));
const roots: string[] = [];
afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

/**
 * A fake `docker` for respaldo-instalacion.sh: records each call, answers the checks the script reads, and writes into
 * the host folder mounted at /out the files each step would produce.
 *
 * Installation as Compose resolves it (instalacion_compose.py, whose own tests cover the parsing): FAKE_PROJECT and
 * FAKE_PREFIX (volumes <prefix>-sports-state...), FAKE_IMAGE. Volumes: MISSING_VOLUME (inspect fails), FOREIGN_VOLUME
 * (labelled for another project), RESTORED_LABEL=1 (labelled by restaurar-instalacion.sh instead of Compose).
 * Containers (id-<service>): ABSENT and STOPPED (space-separated services), UNHEALTHY (unhealthy before the backup),
 * WRONG_MOUNT=1 (sports-data mounts another volume at /state), NOT_BACK (a service that does not come back after
 * `docker start`). IMAGE_MISMATCH=1: the running Sports Data container has another image ID than the resolved image
 * (same tag, rebuilt). STATE_CHECK answers the initial read-only check: marker (incomplete restore), fail, empty.
 * PHOTO_FAIL=1 / COPY_FAIL=1 make the photo backup / the consistent SQLite copy fail.
 */
const FAKE_DOCKER = `#!/bin/sh
printf "%s\\n" "$*" >> "$DOCKER_CALLS"
project=\${FAKE_PROJECT:-rja-test}
prefix=\${FAKE_PREFIX:-rja-test}
video_catalog_dir=\${FAKE_VIDEO_CATALOG_DIR:-/srv/video-catalog}
copy_sha=\${FAKE_COPY_SHA:-46d466f25e7d220a5dfdd04e9533e73826c53b12d200f3ce3d8b3b063c7dd257}
has() { case " $1 " in *" $2 "*) return 0 ;; esac; return 1; }
out=""
for a in "$@"; do case "$a" in *:/out) out=\${a%:/out} ;; esac; done
case "$*" in
  "compose "*" config --images sports-data") echo "\${FAKE_IMAGE:-local/real-jaen-analytics/sports-data:prod}"; exit 0 ;;
  "compose "*" config --format json") echo '{"name": "from compose"}'; exit 0 ;;
  "image inspect -f {{.Id}} "*) echo "sha256:\${FAKE_IMAGE_ID:-1111}"; exit 0 ;;
  "image inspect "*) exit 0 ;;
  "inspect -f {{.Image}} id-sports-data")
    if [ "\${IMAGE_MISMATCH:-}" = 1 ]; then echo "sha256:2222"; else echo "sha256:\${FAKE_IMAGE_ID:-1111}"; fi
    exit 0 ;;
  *instalacion_compose.py*)
    printf '%s\\n' "project=$project" "sports_data_image=\${FAKE_IMAGE:-local/real-jaen-analytics/sports-data:prod}" \\
      "acquisition_image=local/real-jaen-analytics/acquisition:prod" "video_service_image=local/real-jaen-analytics/video-service:prod" \\
      "sports_state=$prefix-sports-state" "acquisition_jobs=$prefix-acquisition-jobs" \\
      "acquisition_profiles=$prefix-acquisition-profiles" "video_state=$prefix-video-state" "imports=/srv/imports" \\
      "video_catalog_dir=$video_catalog_dir" "video_manifest=videos.json" \\
      "volume_names=$prefix-acquisition-jobs,$prefix-acquisition-profiles,$prefix-sports-state,$prefix-video-state" \\
      "network_names=$prefix-dashboard-edge,$prefix-sports-data-internal,$prefix-video-internal" \\
      "dashboard_bind=127.0.0.1" "dashboard_port=8120"
    exit 0 ;;
  "volume inspect "*)
    for a in "$@"; do volume=$a; done
    [ "$volume" = "\${MISSING_VOLUME:-}" ] && { echo "Error: no such volume: $volume" >&2; exit 1; }
    [ "$volume" = "\${FOREIGN_VOLUME:-}" ] && { echo "otra-instalacion|"; exit 0; }
    if [ "\${RESTORED_LABEL:-}" = 1 ]; then echo "|$project"; else echo "$project|"; fi
    exit 0 ;;
  "compose "*" ps -a -q "*)
    for a in "$@"; do svc=$a; done
    has "\${ABSENT:-}" "$svc" || echo "id-$svc"
    exit 0 ;;
  "inspect -f {{index .Config.Labels"*)
    for a in "$@"; do container_id=$a; done
    svc=\${container_id#id-}
    running=true; has "\${STOPPED:-}" "$svc" && running=false
    health=healthy; has "\${UNHEALTHY:-}" "$svc" && health=unhealthy
    case "$svc" in
      sports-data) mounts=";/state=$prefix-sports-state;"; [ "\${WRONG_MOUNT:-}" = 1 ] && mounts=";/state=rja-dev-sports-state;" ;;
      acquisition) mounts=";/profiles=$prefix-acquisition-profiles;/acquisition-jobs=$prefix-acquisition-jobs;" ;;
      video-service) mounts=";/state=$prefix-video-state;bind:/app/data=$video_catalog_dir;" ;;
      *) mounts=";" ;;
    esac
    echo "$project|$running|$health|$mounts"
    exit 0 ;;
  "inspect -f {{.State.Status}}"*)
    for a in "$@"; do container_id=$a; done
    svc=\${container_id#id-}
    if has "\${NOT_BACK:-}" "$svc"; then echo "exited|"
    elif has "\${UNHEALTHY:-}" "$svc"; then echo "running|unhealthy"
    else echo "running|healthy"; fi
    exit 0 ;;
  "stop "*|"start "*) for a in "$@"; do container_id=$a; done; echo "$container_id"; exit 0 ;;
esac
case "$*" in
  *formato_respaldo=2*)
    if [ "\${MANIFEST_FAIL:-}" = 1 ]; then echo "manifest failure" >&2; exit 1; fi
    printf '%s\\n' '{"formato_respaldo":2,"fotos":{"formato":1,"incluidas":true,"ruta":"/state/player-photos"},"catalogo_video":{"incluida":true}}' ;;
  *"FROM job WHERE state IN"*)
    case "\${STATE_CHECK:-}" in
      marker) echo restauracion-incompleta ;;
      sports_active) echo actualizacion-deportiva-activa ;;
      acquisition_active) echo adquisicion-activa ;;
      video_active) echo trabajo-de-video-activo ;;
      fail) echo "PermissionError: /state" >&2; exit 1 ;;
      empty) ;;
      *) echo 0 ;;
    esac ;;
  *"persistencia copiar"*)
    if [ "\${COPY_FAIL:-}" = 1 ]; then echo "copia: base bloqueada" >&2; exit 1; fi
    printf '{"ruta": "/state/copia.sqlite", "sha256_archivo": "%s"}\\n' "$copy_sha" ;;
  *"--entrypoint sha256sum"*) printf '%s  /state/copia.sqlite\\n' "$copy_sha" ;;
  *"test -d /state/procedencia"*) exit 1 ;;
  *"cat '/state/copia.sqlite'"*)
    if [ "\${TRANSFER_FAIL:-}" = 1 ]; then echo "corrupt sqlite"; else echo "fake sqlite"; fi ;;
  *real-jaen.sqlite.actualizaciones*) echo "fake update archive" ;;
  *"-C /jobs ."*) echo "fake acquisition archive" ;;
  *"-C /video ."*) echo "fake video state archive" ;;
  *"fotos_respaldo.py respaldar"*)
    if [ "\${PHOTO_FAIL:-}" = 1 ]; then echo "Fotografías: blob dañado" >&2; exit 1; fi
    echo "fake photo archive"; echo '{"incluidas": true, "ruta": "/state/player-photos"}' >&2 ;;
  *"video_catalog_respaldo.py respaldar"*)
    if [ "\${VIDEO_FAIL:-}" = 1 ]; then echo "Catálogo de vídeo: manifiesto inválido" >&2; exit 1; fi
    echo "fake catalog archive"; echo '{"incluida": true, "directorio": "/app/data"}' >&2 ;;
esac
exit 0
`;

function backup(env: Record<string, string> = {}, envFileLines: readonly string[] = []) {
  const temporaryRoot = mkdtempSync(join(tmpdir(), "rja-backup-script-"));
  roots.push(temporaryRoot);
  const bin = join(temporaryRoot, "bin");
  mkdirSync(bin);
  const callsPath = join(temporaryRoot, "docker-calls.log");
  const videoCatalog = join(temporaryRoot, "video-catalog");
  mkdirSync(videoCatalog);
  writeFileSync(join(videoCatalog, "videos.json"), '{"schema_version":1,"videos":[]}\n');
  writeFileSync(join(bin, "docker"), FAKE_DOCKER);
  chmodSync(join(bin, "docker"), 0o755);
  // The script never reads the env file itself any more: Compose does (here, the fake answers for it).
  const envFile = join(temporaryRoot, ".env");
  writeFileSync(envFile, envFileLines.join("\n") + "\n");
  const destination = join(temporaryRoot, "respaldo");
  if (env.EXISTING_DESTINATION === "1") {
    mkdirSync(destination);
    writeFileSync(join(destination, "valid-backup.marker"), "preserve me");
  }
  const result = spawnSync("sh", [join(repositoryRoot, "dashboard/scripts/respaldo-instalacion.sh"), destination], {
    cwd: repositoryRoot,
    encoding: "utf8",
    env: { ...process.env, ENV_FILE: envFile, PATH: `${bin}:${process.env.PATH ?? ""}`, DOCKER_CALLS: callsPath,
      RJ_BACKUP_START_TIMEOUT: "0", FAKE_VIDEO_CATALOG_DIR: videoCatalog, ...env },
  });
  const calls = existsSync(callsPath) ? readFileSync(callsPath, "utf8").split("\n").filter(Boolean) : [];
  return { result, calls, destination, temporaryRoot, output: result.stderr + result.stdout };
}

const index = (calls: string[], text: string) => calls.findIndex((call) => call.includes(text));
const stops = (calls: string[]) => calls.filter((call) => call.startsWith("stop "));
const starts = (calls: string[]) => calls.filter((call) => call.startsWith("start "));
// Anything that would create, recreate, rebuild or remove containers, images or volumes.
const forbidden = (calls: string[]) => calls.filter((call) =>
  /^compose .* (up|create|build|down|restart|start|stop|rm|pull)( |$)/.test(call)
  || /^(volume (create|rm)|rm |rmi |build |create |container (create|rm)|image (rm|build|pull)|pull )/.test(call)
  || /--build|--force-recreate/.test(call));
// The fake logs each call's arguments, so a multi-line Python argument spans several entries: count invocations.
const invocations = (calls: string[]) => calls.filter((call) => /^(run|compose|volume|inspect|image|stop|start) /.test(call));
const dataMounts = (calls: string[]) => calls.flatMap((call) => [...call.matchAll(/-v (\S+):\/(state|jobs|video)(:ro)?/g)])
  .map((match) => match[1]);

/** The text of one service of a Compose file (from "  name:" to the next service at the same indentation). */
function service(file: string, name: string) {
  const text = readFileSync(join(repositoryRoot, file), "utf8");
  const start = text.indexOf(`\n  ${name}:\n`);
  expect(start, `${file}: ${name}`).toBeGreaterThan(-1);
  const rest = text.slice(start + 1);
  const end = rest.slice(1).search(/\n {2}[a-z-]+:\n|\n[a-z]+:\n/);
  return end === -1 ? rest : rest.slice(0, end + 1);
}
describe("installation backup script: restart", () => {
  it("waits for previously healthy containers with a configurable timeout", () => {
    const script = readFileSync(join(repositoryRoot, "dashboard/scripts/respaldo-instalacion.sh"), "utf8");
    expect(script).toContain("START_TIMEOUT=${RJ_BACKUP_START_TIMEOUT:-360}");
    expect(script).toContain("running\\|healthy");
  });

  it("sets the entrypoint of every one-off Compose container (compose.dev.yaml overrides Sports Data's)", () => {
    // compose.dev.yaml starts sports-data with `python -B -c <seed script>`: `run sports-data -m module` would run
    // `python -B -c -m` there. Each `dc run` names its entrypoint, as the image does in production (python -B).
    const devEntrypoint = service("compose.dev.yaml", "sports-data").match(/\n {4}entrypoint: (.*)\n/)?.[1];
    expect(devEntrypoint).toBe('["python", "-B", "-c"]');
    const file = "respaldo-instalacion.sh";
    const runs = readFileSync(join(repositoryRoot, "dashboard/scripts", file), "utf8").split("\n")
      .filter((line) => /\bdc run\b/.test(line));
    expect(runs.length, file).toBeGreaterThan(0);
    for (const line of runs) expect(line, `${file}: ${line}`).toMatch(/ --entrypoint python /);
    for (const line of runs.filter((l) => / -m /.test(l))) expect(line).toMatch(/--entrypoint python \S+ -B -m /);
  });

  it("never brings services up through Compose, and assumes no volume names", () => {
    for (const file of ["respaldo-instalacion.sh", "instalacion-compose.sh"]) {
      const script = readFileSync(join(repositoryRoot, "dashboard/scripts", file), "utf8");
      expect(script, file).not.toMatch(/\bdc (up|stop|start|restart|create|build|down)\b/);
      expect(script, file).not.toMatch(/rja-(dev|prod)-/);
      expect(script, file).not.toMatch(/sports-data:(dev|prod)/);
    }
  });

  it("supports the active development overlays and records the versioned photo location", () => {
    const script = readFileSync(join(repositoryRoot, "dashboard/scripts/respaldo-instalacion.sh"), "utf8");
    expect(script).toContain("COMPOSE_FILES=${COMPOSE_FILES:-${COMPOSE_FILE:-compose.prod.yaml}}");
    expect(script).toContain("COMPOSE_FILE=\"$COMPOSE_FILES\"");
    expect(script).toContain("formato_respaldo=2");
    expect(readFileSync(join(repositoryRoot, "sports-data/scripts/fotos_respaldo.py"), "utf8")).toContain("/state/player-photos");
    expect(script).toContain("video-catalog.tar");
  });
});

// POSIX shell scripts: run on Linux (on Windows the script does not accept a Windows ENV_FILE path).
describe.skipIf(process.platform === "win32")("installation backup script: installation identity", () => {
  it.each([
    ["the default development prefix", { COMPOSE_FILE: "compose.dev.yaml", FAKE_PROJECT: "real-jaen-analytics-dev",
      FAKE_PREFIX: "rja-dev", FAKE_IMAGE: "local/real-jaen-analytics/sports-data:dev" }, "rja-dev"],
    ["a custom prefix and project (RJ_DEV_VOLUME_PREFIX, RJ_DEV_PROJECT_NAME)", { COMPOSE_FILE: "compose.dev.yaml",
      FAKE_PROJECT: "real-jaen-analytics-alvaro", FAKE_PREFIX: "rja-alvaro" }, "rja-alvaro"],
    ["production names", { FAKE_PROJECT: "real-jaen-analytics-sinbad2-prod", FAKE_PREFIX: "rja-sinbad2" }, "rja-sinbad2"],
  ] as const)("backs up the volumes Compose resolves: %s", (_label, env, prefix) => {
    const { result, calls, output } = backup(env);

    expect(result.status, output).toBe(0);
    expect(index(calls, "config --format json")).toBeGreaterThan(-1);
    const mounts = dataMounts(calls);
    expect(mounts.length).toBeGreaterThan(0);
    expect(new Set(mounts)).toEqual(new Set([`${prefix}-sports-state`, `${prefix}-acquisition-jobs`,
      `${prefix}-video-state`]));
    // Each volume is checked (exists, belongs to the project) before any container mounts it.
    for (const volume of new Set(mounts)) {
      const inspected = calls.findIndex((c) => c.startsWith("volume inspect") && c.endsWith(` ${volume}`));
      expect(inspected, volume).toBeGreaterThan(-1);
      expect(inspected).toBeLessThan(calls.findIndex((c) => c.startsWith("run ") && c.includes(`${volume}:`)));
    }
    if (prefix !== "rja-dev") expect(calls.join("\n")).not.toContain("rja-dev-");
    const image = (env as Record<string, string>).FAKE_IMAGE ?? "local/real-jaen-analytics/sports-data:prod";
    expect(calls.filter((c) => c.startsWith("run ")).every((c) => c.includes(` ${image} `))).toBe(true);
    expect(output).toContain(`Instalación: proyecto ${env.FAKE_PROJECT}`);
  });

  it("accepts volumes created by restaurar-instalacion.sh (rj.installation label)", () => {
    const { result, output } = backup({ RESTORED_LABEL: "1" });
    expect(result.status, output).toBe(0);
  });

  it.each([
    ["a data volume does not exist", { MISSING_VOLUME: "rja-test-acquisition-jobs" }, "No existe el volumen rja-test-acquisition-jobs"],
    ["a data volume belongs to another installation", { FOREIGN_VOLUME: "rja-test-video-state" },
      "El volumen rja-test-video-state no pertenece a la instalación rja-test"],
    ["the running container mounts another volume", { WRONG_MOUNT: "1" }, "El contenedor de sports-data no monta /state=rja-test-sports-state"],
  ] as const)("does nothing when %s: no volume mounted or created, nothing stopped, no folder", (_label, env, message) => {
    const { result, calls, destination, output } = backup(env);

    expect(result.status).not.toBe(0);
    expect(output).toContain(message);
    expect(calls.filter((c) => c.startsWith("run ") && !c.includes("instalacion_compose.py"))).toEqual([]);
    expect(stops(calls)).toEqual([]);
    expect(starts(calls)).toEqual([]);
    expect(forbidden(calls)).toEqual([]);
    expect(existsSync(destination)).toBe(false);
  });
});

// POSIX shell scripts: run on Linux (on Windows the script does not accept a Windows ENV_FILE path).
describe.skipIf(process.platform === "win32")("installation backup script: original containers", () => {
  it("stops the running containers by ID and starts the same ones again, without Compose up or recreation", () => {
    const { result, calls, output } = backup();

    expect(result.status, output).toBe(0);
    expect(stops(calls)).toEqual(["stop id-dashboard", "stop id-acquisition", "stop id-video-service",
      "stop id-sports-data"]);
    expect(starts(calls)).toEqual(["start id-sports-data", "start id-video-service", "start id-acquisition",
      "start id-dashboard"]);
    expect(index(calls, "persistencia copiar")).toBeGreaterThan(index(calls, "stop id-sports-data"));
    expect(index(calls, "start id-sports-data")).toBeGreaterThan(index(calls, "fotos_respaldo.py respaldar"));
    expect(index(calls, "start id-sports-data")).toBeGreaterThan(index(calls, "formato_respaldo=2"));
    expect(forbidden(calls)).toEqual([]);
  });

  it("leaves stopped and absent containers as they are, and does not require a previously unhealthy one to heal", () => {
    const { result, calls, output } = backup({ STOPPED: "acquisition", ABSENT: "dashboard", UNHEALTHY: "video-service" });

    expect(result.status, output).toBe(0);
    expect(stops(calls)).toEqual(["stop id-video-service", "stop id-sports-data"]);
    expect(starts(calls)).toEqual(["start id-sports-data", "start id-video-service"]);
    expect(forbidden(calls)).toEqual([]);
  });

  it.each([
    ["the photo copy fails", { PHOTO_FAIL: "1" }, "blob dañado"],
    ["the video catalog copy fails", { VIDEO_FAIL: "1" }, "manifiesto inválido"],
    ["the consistent SQLite copy fails", { COPY_FAIL: "1" }, "base bloqueada"],
    ["the streamed SQLite copy changes", { TRANSFER_FAIL: "1" }, "copia SQLite transferida"],
    ["manifest generation fails", { MANIFEST_FAIL: "1" }, "manifest failure"],
  ] as const)("when %s: no complete-looking backup, and the same containers are started again", (_label, env, message) => {
    const { result, calls, destination, temporaryRoot, output } = backup(env);

    expect(result.status).not.toBe(0);
    expect(output).toContain(message);
    expect(output).not.toContain("Respaldo completo");
    expect(existsSync(join(destination, "SHA256SUMS"))).toBe(false); // restaurar-instalacion.sh refuses it
    expect(index(calls, "manifest.json")).toBe(-1);
    expect(starts(calls).map((c) => c.slice(6)).sort()).toEqual(stops(calls).map((c) => c.slice(5)).sort());
    expect(readdirSync(temporaryRoot).filter((name) => name.startsWith("respaldo.partial."))).toEqual([]);
    expect(forbidden(calls)).toEqual([]); // no up, no recreation, no rebuild
  });

  it("does not overwrite an existing valid backup destination", () => {
    const { result, calls, destination, output } = backup({ EXISTING_DESTINATION: "1" });

    expect(result.status).not.toBe(0);
    expect(output).toContain("El destino ya existe");
    expect(readFileSync(join(destination, "valid-backup.marker"), "utf8")).toBe("preserve me");
    expect(stops(calls)).toEqual([]);
    expect(starts(calls)).toEqual([]);
    expect(forbidden(calls)).toEqual([]);
  });

  it("reports a container that does not come back, without recreating it and without SHA256SUMS", () => {
    const { result, calls, destination, output } = backup({ NOT_BACK: "acquisition" });

    expect(result.status).not.toBe(0);
    expect(output).toContain("acquisition no ha vuelto a su estado anterior");
    expect(existsSync(join(destination, "SHA256SUMS"))).toBe(false);
    expect(starts(calls)).toContain("start id-acquisition");
    expect(forbidden(calls)).toEqual([]);
  });

  it("never runs Acquisition: it is only stopped and started again as the same container (no real acquisition)", () => {
    const { result, calls, output } = backup();

    expect(result.status, output).toBe(0);
    // No Compose run/up of the service and no container of its image: only its own container, stopped and started.
    expect(calls.filter((c) => /^compose .* (run|up|create|start)\b.* acquisition( |$)/.test(c))).toEqual([]);
    expect(calls.join("\n")).not.toContain("real-jaen-analytics/acquisition");
    expect(calls.filter((c) => c.endsWith("id-acquisition") && !c.startsWith("inspect "))).toEqual(
      ["stop id-acquisition", "start id-acquisition"]);
    expect(calls.join("\n")).not.toMatch(/ACQ_ENABLE_REAL|RJ_ACQUISITION_ENABLE_REAL/);
  });
});

// POSIX shell scripts: run on Linux (on Windows the script does not accept a Windows ENV_FILE path).
describe.skipIf(process.platform === "win32")("installation backup script: photos", () => {
  it("backs up the photo store with every service stopped and lists fotos.tar in SHA256SUMS", () => {
    const { result, calls, destination, output } = backup();

    expect(result.status, output).toBe(0);
    const stop = index(calls, "stop id-sports-data");
    const photos = index(calls, "fotos_respaldo.py respaldar");
    const restart = index(calls, "start id-sports-data");
    expect(stop).toBeGreaterThan(-1);
    expect(photos).toBeGreaterThan(stop);
    expect(restart).toBeGreaterThan(photos); // services are started again only after the photo copy
    expect(calls.join("\n")).toContain("rja-test-sports-state:/state:ro");
    expect(calls.join("\n")).toContain("sports-data/scripts/fotos_respaldo.py:/opt/rja/fotos_respaldo.py:ro");
    expect(index(calls, "formato_respaldo=2")).toBeGreaterThan(photos);
    expect(readFileSync(join(destination, "SHA256SUMS"), "utf8")).toMatch(/ \.\/fotos\.tar$/m);
    expect(existsSync(join(destination, "fotos.json"))).toBe(false); // folded into manifest.json
    expect(output).toContain("Respaldo completo");
  });

  it("checksums every generated archive and does not publish the intermediate photo summary", () => {
    const { result, calls, destination, output } = backup();

    expect(result.status, output).toBe(0);
    const listed = readFileSync(join(destination, "SHA256SUMS"), "utf8").trim().split("\n")
      .map((line) => line.split(/ [ *]/)[1]).sort();
    expect(listed).toEqual(["./acquisition-jobs.tar", "./actualizaciones.tar", "./copiar.json", "./fotos.tar",
      "./manifest.json", "./real-jaen.sqlite", "./video-catalog.tar", "./video-service-state.tar"]);
    expect(JSON.parse(readFileSync(join(destination, "manifest.json"), "utf8"))).toMatchObject({
      formato_respaldo: 2,
      fotos: { incluidas: true, ruta: "/state/player-photos" },
      catalogo_video: { incluida: true },
    });
    expect(calls.join("\n")).not.toContain("acquisition-profiles:/profiles");
    expect(calls.join("\n")).not.toContain("/run/secrets/");
    expect(existsSync(join(destination, "video-catalog.json"))).toBe(false);
  });
});

// POSIX shell scripts: run on Linux (on Windows the script does not accept a Windows ENV_FILE path).
describe.skipIf(process.platform === "win32")("installation backup script: incomplete restore", () => {
  const MARKER = "/state/real-jaen.sqlite.restauracion-incompleta";
  const stateCheck = (calls: string[]) => index(calls, `os.lstat('${MARKER}')`);

  it("checks for the incomplete-restore marker read-only, before stopping anything, and backs up a normal installation", () => {
    const { result, calls, destination, output } = backup();

    expect(result.status, output).toBe(0);
    // The first container that mounts the state (read-only) is the check, before anything is stopped.
    const check = calls.findIndex((call) => call.startsWith("run ") && call.includes(":/state"));
    expect(calls[check]).toMatch(/^run --rm --network none -v rja-test-sports-state:\/state:ro -v rja-test-acquisition-jobs:\/jobs:ro -v rja-test-video-state:\/video:ro --entrypoint python /);
    expect(check).toBeLessThan(stateCheck(calls));
    expect(stateCheck(calls)).toBeLessThan(index(calls, "stop "));
    expect(existsSync(join(destination, "SHA256SUMS"))).toBe(true);
  });

  it("refuses an installation whose restore is incomplete: no service stopped, nothing written, marker untouched", () => {
    const { result, calls, destination, output } = backup({ STATE_CHECK: "marker" });

    expect(result.status).not.toBe(0);
    expect(output).toContain(`Restauración incompleta (${MARKER})`);
    expect(output).not.toContain("Respaldo completo");
    // Only read-only calls: resolution, inspections and the check itself. No stop, data write or restart.
    expect(invocations(calls).filter((c) => c.startsWith("run ")).map((c) => c.includes(":/state:ro")
      || c.includes("instalacion_compose.py"))).toEqual([true, true]);
    expect(stops(calls)).toEqual([]);
    expect(starts(calls)).toEqual([]);
    // The marker is only read (os.lstat, on a read-only mount), never removed or rewritten.
    expect(calls.filter((call) => call.includes(MARKER))).toEqual([`    os.lstat('${MARKER}')`]);
    expect(existsSync(destination)).toBe(false); // no backup folder, so nothing that looks like a backup
  });

  it.each([
    ["an active sports update", "sports_active", "Hay una actualización deportiva en curso"],
    ["an active Acquisition job", "acquisition_active", "Hay un trabajo de Acquisition en curso"],
    ["an active video job", "video_active", "Hay un trabajo de vídeo en curso"],
  ])("refuses %s before stopping anything", (_label, state, message) => {
    const { result, calls, destination, output } = backup({ STATE_CHECK: state });

    expect(result.status).not.toBe(0);
    expect(output).toContain(message);
    expect(stops(calls)).toEqual([]);
    expect(starts(calls)).toEqual([]);
    expect(existsSync(destination)).toBe(false);
  });

  it.each([
    ["the check fails", "fail"],
    ["the check answers nothing", "empty"],
  ])("does not go on as if there were no marker when %s", (_label, state) => {
    const { result, calls, destination, output } = backup({ STATE_CHECK: state });

    expect(result.status).not.toBe(0);
    expect(output).toContain("No se pudo comprobar el estado de la instalación");
    expect(stops(calls)).toEqual([]);
    expect(existsSync(destination)).toBe(false);
  });
});

// POSIX shell scripts: run on Linux (on Windows the script does not accept a Windows ENV_FILE path).
describe.skipIf(process.platform === "win32")("installation backup script: Sports Data image", () => {
  const imageCheck = (calls: string[]) => [index(calls, "inspect -f {{.Image}} id-sports-data"),
    index(calls, "image inspect -f {{.Id}} ")];

  it.each([
    ["the default project", {}],
    ["a custom project and prefix", { COMPOSE_FILE: "compose.dev.yaml", FAKE_PROJECT: "real-jaen-analytics-alvaro",
      FAKE_PREFIX: "rja-alvaro", FAKE_IMAGE: "local/real-jaen-analytics/sports-data:dev" }],
  ] as const)("compares image IDs before stopping anything and backs up when they match (%s)", (_label, env) => {
    const { result, calls, output } = backup(env);

    expect(result.status, output).toBe(0);
    const [running, configured] = imageCheck(calls);
    expect(running).toBeGreaterThan(-1);
    const image = (env as Record<string, string>).FAKE_IMAGE ?? "local/real-jaen-analytics/sports-data:prod";
    expect(calls[configured]).toBe(`image inspect -f {{.Id}} ${image}`); // the image Compose resolved, by ID
    expect(Math.max(running, configured)).toBeLessThan(index(calls, "stop "));
  });

  it.each([
    ["the default project", {}],
    ["a custom project and prefix", { COMPOSE_FILE: "compose.dev.yaml", FAKE_PROJECT: "real-jaen-analytics-alvaro",
      FAKE_PREFIX: "rja-alvaro" }],
  ] as const)("refuses when the running Sports Data image is another one: nothing stopped, mounted or written (%s)",
    (_label, env) => {
      const { result, calls, destination, output } = backup({ ...env, IMAGE_MISMATCH: "1" });

      expect(result.status).not.toBe(0);
      expect(output).toContain("La imagen de Sports Data en marcha (sha256:2222) no es la que Compose usaría");
      expect(output).toContain("= sha256:1111");
      expect(stops(calls)).toEqual([]);
      expect(starts(calls)).toEqual([]);
      expect(calls.filter((c) => c.startsWith("run ") && !c.includes("instalacion_compose.py"))).toEqual([]);
      expect(forbidden(calls)).toEqual([]);
      expect(existsSync(destination)).toBe(false);
    });
});
