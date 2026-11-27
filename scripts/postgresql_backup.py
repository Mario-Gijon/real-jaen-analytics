#!/usr/bin/env python3
"""PostgreSQL v3 backup and guarded restore for Real Jaén Analytics Compose installs."""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/postgresql_backup_helper.py"
DEPLOY_CHECK = ROOT / "scripts/postgresql-deployment-check.py"
SERVICES = ("postgresql", "sports-data", "video-service", "acquisition", "dashboard")
APP_SERVICES = SERVICES[1:]
REQUIRED_VOLUMES = {
    "postgres-data": ("postgresql", "/var/lib/postgresql"),
    "sports-operational-state": ("sports-data", "/state"),
    "video-operational-state": ("video-service", "/state"),
    "acquisition-jobs": ("acquisition", "/acquisition-jobs"),
    "acquisition-profiles": ("acquisition", "/profiles"),
}
FORMAT_VERSION = 3
BACKUP_TOOL_VERSION = "1.1.0"
MANIFEST_NAME = "manifest.json"
PAYLOADS = ("sports_data.dump", "video_service.dump", "acquisition-history.tar")
ACTIVE_STATES = {"queued", "running", "cancelling"}


class OperationError(RuntimeError):
    pass


def fail(message: str):
    raise OperationError(message)


def _split_env(name: str, default: str | None = None) -> list[Path]:
    raw = os.environ.get(name, default or "")
    values = [item for item in raw.split(":") if item]
    if not values:
        fail(f"{name} debe indicar los archivos requeridos")
    paths = []
    for value in values:
        path = Path(value)
        if not path.is_absolute():
            path = ROOT / path
        try:
            info = path.lstat()
        except OSError:
            fail(f"{name} contiene un archivo ausente")
        if path.is_symlink() or not path.is_file():
            fail(f"{name} debe contener archivos regulares")
        paths.append(path.resolve())
    return paths


def _compose_files() -> list[Path]:
    raw = os.environ.get(
        "COMPOSE_FILES",
        "compose.prod.yaml:compose.postgresql.yaml:compose.postgresql.prod.yaml",
    )
    values = [item for item in raw.split(":") if item]
    if not values:
        fail("COMPOSE_FILES debe indicar los archivos Compose de la instalación")
    paths = []
    for value in values:
        path = Path(value)
        if not path.is_absolute():
            path = ROOT / path
        if path.is_symlink() or not path.is_file():
            fail("COMPOSE_FILES contiene un archivo ausente o inseguro")
        paths.append(path.resolve())
    names = {path.name for path in paths}
    if "compose.postgresql.yaml" not in names:
        fail("La configuración Compose debe incluir compose.postgresql.yaml")
    if not ({"compose.postgresql.dev.yaml", "compose.postgresql.prod.yaml"} & names):
        fail("La configuración Compose debe incluir un overlay PostgreSQL DEV o PROD")
    return paths


def _compose_base(profile: bool = False) -> list[str]:
    env_files = _split_env("ENV_FILES", ".env.prod:.env.postgresql.prod")
    compose_files = _compose_files()
    command = ["docker", "compose", "--project-directory", str(ROOT)]
    for path in env_files:
        command.extend(("--env-file", str(path)))
    for path in compose_files:
        command.extend(("-f", str(path)))
    if profile:
        command.extend(("--profile", "migrations"))
    return command


def _run(args: list[str], *, stdin=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
         cwd: Path | None = None, timeout: int = 3600, env=None):
    try:
        input_args = {"stdin": stdin} if hasattr(stdin, "read") else {"input": stdin}
        result = subprocess.run(args, cwd=cwd or ROOT, **input_args, stdout=stdout, stderr=stderr,
                                timeout=timeout, check=False, env=env)
    except (OSError, subprocess.TimeoutExpired):
        fail("No se pudo completar una operación local de Docker/PostgreSQL")
    if result.returncode != 0:
        fail("Falló una comprobación local de Docker/PostgreSQL; no se imprimieron sus detalles sensibles")
    return result


def _text(args: list[str], *, stdin=None, timeout: int = 120) -> str:
    result = _run(args, stdin=stdin, timeout=timeout)
    return result.stdout.decode("utf-8", errors="strict").strip()


def _config() -> dict:
    result = _run(_compose_base(profile=True) + ["config", "--format", "json"], timeout=120)
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError):
        fail("Docker Compose no produjo una configuración válida")


def _names(config: dict) -> dict:
    project = config.get("name")
    services = config.get("services", {})
    volumes = config.get("volumes", {})
    networks = config.get("networks", {})
    if not isinstance(project, str) or not project or not isinstance(services, dict):
        fail("No se pudo resolver la identidad de la instalación")
    for service in SERVICES:
        if service not in services:
            fail(f"Falta el servicio {service} en la configuración PostgreSQL")
    volume_names = {}
    for key, value in volumes.items():
        name = value.get("name")
        if not isinstance(name, str) or not name:
            fail(f"Compose no resuelve el volumen {key}")
        volume_names[key] = name
    if not set(REQUIRED_VOLUMES).issubset(volume_names):
        fail("Compose no declara los cinco volúmenes persistentes requeridos")
    network_names = {}
    for key, value in networks.items():
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            network_names[key] = value["name"]
    pg_network = network_names.get("postgresql-private")
    if not pg_network or networks["postgresql-private"].get("internal") is not True:
        fail("La red PostgreSQL debe ser privada e interna")
    return {"project": project, "services": services, "volumes": volume_names,
            "networks": network_names, "postgres_network": pg_network}


def _file_secret(config: dict, key: str) -> Path:
    value = config.get("secrets", {}).get(key, {}).get("file")
    if not isinstance(value, str):
        fail("Falta una credencial de PostgreSQL requerida en Compose")
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    if path.is_symlink() or not path.is_file():
        fail("Falta una credencial PostgreSQL regular en la configuración privada")
    return path.resolve()


def _service_image(service: dict) -> str:
    image = service.get("image")
    if not isinstance(image, str) or not image:
        fail("No se pudo resolver una imagen de servicio")
    return image


def _image_object_available(image_id: str) -> bool:
    try:
        _text(["docker", "image", "inspect", "--format", "{{.Id}}", image_id])
    except OperationError:
        return False
    return True


def _service_volume_map(service: dict) -> dict[str, str]:
    result = {}
    for mount in service.get("volumes", []):
        if isinstance(mount, dict) and mount.get("type") == "volume":
            source, target = mount.get("source"), mount.get("target")
            if isinstance(source, str) and isinstance(target, str):
                result[target] = source
    return result


def _service_network_names(service: dict, names: dict) -> set[str]:
    configured = service.get("networks", {})
    if isinstance(configured, list):
        logical_names = configured
    elif isinstance(configured, dict):
        logical_names = list(configured)
    else:
        logical_names = []
    return {names["networks"][key] for key in logical_names if key in names["networks"]}


def _inspect_container(container_id: str) -> dict:
    raw = _text(["docker", "inspect", "--format",
                 '{{.Id}}|{{.Config.Image}}|{{.Image}}|{{index .Config.Labels "com.docker.compose.project"}}|'
                 '{{index .Config.Labels "com.docker.compose.service"}}|{{.State.Status}}|'
                 '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{json .Mounts}}|'
                 '{{json .HostConfig.PortBindings}}|{{json .NetworkSettings.Networks}}', container_id])
    parts = raw.split("|", 9)
    if len(parts) != 10:
        fail("No se pudo comprobar un contenedor de la instalación")
    try:
        mounts = json.loads(parts[7])
        ports = json.loads(parts[8])
        networks = json.loads(parts[9])
    except json.JSONDecodeError:
        fail("No se pudo comprobar los montajes de un contenedor")
    return {"id": parts[0], "image": parts[1], "image_id": parts[2], "project": parts[3],
            "service": parts[4], "state": parts[5], "health": parts[6], "mounts": mounts, "ports": ports,
            "networks": networks}


def _service_containers(names: dict, *, required: bool) -> dict[str, list[dict]]:
    found = {}
    for service in SERVICES:
        output = _text(_compose_base() + ["ps", "--all", "--quiet", service], timeout=60)
        ids = [line.strip() for line in output.splitlines() if line.strip()]
        if len(ids) > 1 or (required and len(ids) != 1):
            fail(f"Se esperaba un único contenedor de {service} en {names['project']}")
        found[service] = [_inspect_container(container_id) for container_id in ids]
    return found


def _volume_check(volume: str, project: str) -> None:
    raw = _text(["docker", "volume", "inspect", "--format",
                 '{{.Name}}|{{index .Labels "com.docker.compose.project"}}|{{index .Labels "rj.installation"}}', volume])
    parts = raw.split("|", 2)
    if len(parts) != 3 or parts[0] != volume or project not in parts[1:]:
        fail(f"El volumen {volume} no existe o no pertenece al proyecto configurado")


def _network_check(network: str, project: str, *, must_be_internal: bool = False) -> None:
    raw = _text(["docker", "network", "inspect", "--format",
                 '{{.Name}}|{{index .Labels "com.docker.compose.project"}}|{{.Internal}}', network])
    parts = raw.split("|")
    if len(parts) != 3 or parts[0] != network or parts[1] != project:
        fail(f"La red {network} no pertenece al proyecto configurado")
    if must_be_internal and parts[2] != "true":
        fail("La red de PostgreSQL no es interna")


def _check_runtime_image_identity(service: str, expected_image: str, container: dict,
                                 allow_missing_image_object_for: set[str]) -> None:
    configured_id = _text(["docker", "image", "inspect", "--format", "{{.Id}}", expected_image])
    if configured_id == container["image_id"]:
        return
    if service not in allow_missing_image_object_for:
        fail(f"La imagen de {service} fue sustituida desde su arranque")
    if _image_object_available(container["image_id"]):
        fail(f"La imagen de {service} aún existe; la excepción solo admite el objeto ausente")


def _parse_admin_image_identity(raw: str, project: str, compose_service: str) -> tuple[str, str, str]:
    parts = raw.split("|", 2)
    if (len(parts) != 3 or not re.fullmatch(r"sha256:[0-9a-f]{64}", parts[0])
            or parts[1] != project or parts[2] != compose_service):
        fail("La imagen administrativa no pertenece al servicio esperado")
    return parts[0], parts[1], parts[2]


def _validate_compose_installation(config: dict, names: dict, containers: dict[str, list[dict]], *,
                                   require_running: bool, require_all: bool,
                                   require_networks: bool = True,
                                   allow_missing_image_object_for: set[str] | None = None) -> dict:
    allow_missing_image_object_for = allow_missing_image_object_for or set()
    for runtime, administrative in (("sports-data", "migrate-sports-data"),
                                    ("video-service", "migrate-video-service")):
        if _service_image(config["services"][runtime]) == _service_image(config["services"][administrative]):
            fail(f"La imagen administrativa de {runtime} comparte etiqueta con la imagen de ejecución")
    for key in REQUIRED_VOLUMES:
        _volume_check(names["volumes"][key], names["project"])
    for key, network in names["networks"].items():
        if require_networks or key == "postgresql-private":
            _network_check(network, names["project"], must_be_internal=(key == "postgresql-private"))

    for service in SERVICES:
        item = config["services"][service]
        expected_image = _service_image(item)
        selected = containers[service]
        if require_all and len(selected) != 1:
            fail(f"No existe exactamente un contenedor para {service}")
        if not selected:
            continue
        container = selected[0]
        if container["project"] != names["project"] or container["service"] != service:
            fail(f"El contenedor de {service} no pertenece al proyecto Compose resuelto")
        if container["image"] != expected_image:
            fail(f"El contenedor de {service} usa otra etiqueta de imagen")
        _check_runtime_image_identity(service, expected_image, container, allow_missing_image_object_for)
        if require_running and (container["state"] != "running" or container["health"] != "healthy"):
            fail(f"El servicio {service} no está saludable")
        if not require_running and service in APP_SERVICES and container["state"] == "running":
            fail("No se restaura sobre una instalación con servicios de aplicación activos")
        if container["state"] == "running":
            expected_networks = _service_network_names(item, names)
            actual_networks = set(container["networks"] or {})
            if expected_networks != actual_networks:
                fail(f"El contenedor {service} no está conectado a las redes Compose esperadas")
        actual_mounts = {(mount.get("Name"), mount.get("Destination")): mount
                         for mount in container["mounts"] if mount.get("Type") == "volume"}
        for target, logical in _service_volume_map(item).items():
            if logical not in names["volumes"]:
                continue
            volume = names["volumes"][logical]
            mount = actual_mounts.get((volume, target))
            if mount is None or (require_running and not mount.get("RW", False)):
                fail(f"El contenedor {service} no usa el volumen esperado en {target}")
        for expected in item.get("volumes", []):
            if not isinstance(expected, dict) or expected.get("type") != "bind":
                continue
            target, source = expected.get("target"), expected.get("source")
            if not isinstance(target, str) or not isinstance(source, str):
                fail("Compose contiene un montaje bind inválido")
            matched = next((mount for mount in container["mounts"]
                            if mount.get("Type") == "bind" and mount.get("Destination") == target), None)
            if matched is None or Path(matched.get("Source", "")).resolve() != Path(source).resolve():
                fail(f"El contenedor {service} no conserva el montaje bind esperado en {target}")
        if service == "postgresql":
            mount = actual_mounts.get((names["volumes"]["postgres-data"], "/var/lib/postgresql"))
            if mount is None or not mount.get("RW", False):
                fail("PostgreSQL no usa su volumen de datos persistente")
            if container["ports"] not in ({}, None):
                fail("PostgreSQL no debe publicar puertos en el host")

    import_dir = None
    for mount in config["services"]["sports-data"].get("volumes", []):
        if isinstance(mount, dict) and mount.get("target") == "/imports" and mount.get("type") == "bind":
            import_dir = Path(mount.get("source", ""))
            break
    if import_dir is None or not import_dir.is_absolute() or import_dir.is_symlink() or not import_dir.is_dir():
        fail("El directorio imports debe existir y ser un directorio real")
    return {"import_dir": import_dir.resolve()}


def _assert_no_foreign_volume_attachments(names: dict, containers: dict[str, list[dict]]) -> None:
    volume_set = set(names["volumes"].values())
    expected_ids = {item[0]["id"] for item in containers.values() if item}
    ids = _text(["docker", "ps", "--all", "--quiet"]).splitlines()
    for container_id in ids:
        full_id = _text(["docker", "inspect", "--format", "{{.Id}}", container_id])
        if full_id in expected_ids:
            continue
        mounts = _text(["docker", "inspect", "--format", '{{json .Mounts}}', container_id])
        try:
            actual = json.loads(mounts)
        except json.JSONDecodeError:
            fail("No se pudo descartar un montaje ajeno de los volúmenes PostgreSQL")
        if any(mount.get("Type") == "volume" and mount.get("Name") in volume_set for mount in actual):
            fail("Un contenedor ajeno tiene montado un volumen de la instalación")


def _pg_id(containers: dict[str, list[dict]]) -> str:
    if not containers.get("postgresql"):
        fail("No hay contenedor PostgreSQL")
    container = containers["postgresql"][0]
    if container["state"] != "running" or container["health"] != "healthy":
        fail("PostgreSQL debe estar en ejecución y saludable")
    return container["id"]


def _pg_json(pg_id: str) -> dict:
    query = r"""SELECT json_build_object(
      'version_num', current_setting('server_version_num')::integer,
      'databases', (SELECT json_agg(json_build_object('name',d.datname,'owner',pg_get_userbyid(d.datdba),
                    'bytes',pg_database_size(d.datname)) ORDER BY d.datname)
                    FROM pg_database d WHERE d.datname IN ('sports_data','video_service')),
      'roles', (SELECT json_agg(json_build_object('name',r.rolname,'superuser',r.rolsuper,
                    'createdb',r.rolcreatedb,'createrole',r.rolcreaterole,'login',r.rolcanlogin)
                    ORDER BY r.rolname) FROM pg_roles r WHERE r.rolname IN
                    ('sports_data_owner','sports_data_app','video_service_owner','video_service_app'))
    )::text"""
    output = _text(["docker", "exec", pg_id, "psql", "--no-psqlrc", "--no-password",
                    "--set", "ON_ERROR_STOP=1", "--username", "postgres", "--dbname", "postgres",
                    "--tuples-only", "--no-align", "--command", query], timeout=120)
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        fail("PostgreSQL devolvió un inventario administrativo no válido")


def _pg_sql(pg_id: str, database: str, query: str) -> str:
    return _text(["docker", "exec", pg_id, "psql", "--no-psqlrc", "--no-password",
                  "--set", "ON_ERROR_STOP=1", "--username", "postgres", "--dbname", database,
                  "--tuples-only", "--no-align", "--command", query], timeout=120)


def _db_check(pg: dict, *, empty: bool) -> None:
    if not 180000 <= pg.get("version_num", 0) < 190000:
        fail("PostgreSQL 18 es la única versión admitida para el formato v3")
    databases = {row["name"]: row for row in (pg.get("databases") or [])}
    roles = {row["name"]: row for row in (pg.get("roles") or [])}
    if set(databases) != {"sports_data", "video_service"}:
        fail("Faltan las dos bases de datos de la instalación")
    for database, owner in (("sports_data", "sports_data_owner"), ("video_service", "video_service_owner")):
        if databases[database]["owner"] != owner:
            fail("La base de datos no pertenece a su rol owner independiente")
    if set(roles) != {"sports_data_owner", "sports_data_app", "video_service_owner", "video_service_app"}:
        fail("Faltan roles PostgreSQL independientes para ambos servicios")
    for role_name, role in roles.items():
        if role["superuser"] or role["createdb"] or role["createrole"]:
            fail("Un rol de base de datos tiene privilegios administrativos inesperados")


def _db_check_source(pg: dict) -> None:
    _db_check(pg, empty=False)


def _check_marker(import_dir: Path, marker_name: str) -> Path:
    marker = import_dir / marker_name
    if marker.exists() or marker.is_symlink():
        fail(f"Existe el marcador {marker_name}; se requiere inspección administrativa")
    return marker


def _container_order() -> tuple[str, ...]:
    return ("sports-data", "video-service", "acquisition")


def _wait_container(container: dict, timeout: int = 360) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = _text(["docker", "inspect", "--format",
                     '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}',
                     container["id"]], timeout=30)
        state, health = raw.split("|", 1)
        if state == "running" and health == "healthy":
            return
        if state not in {"running", "created", "restarting"}:
            break
        time.sleep(2)
    fail(f"El contenedor {container['service']} no recuperó su estado saludable")


def _start_original(containers: dict[str, list[dict]], stopped: set[str], *,
                    acquisition_image: str, jobs_volume: str, fence_token: str | None):
    errors = []

    def start_and_wait(container: dict):
        state = _text(["docker", "inspect", "--format", "{{.State.Status}}", container["id"]])
        if state != "running":
            _run(["docker", "start", container["id"]], timeout=120)
        _wait_container(container)

    for service in _container_order():
        item = containers.get(service, [])
        if service not in stopped or not item:
            continue
        try:
            start_and_wait(item[0])
        except OperationError:
            errors.append(service)
    if errors:
        raise OperationError("No se pudo recuperar el estado de origen: " + ", ".join(errors))
    if fence_token:
        try:
            _fence(acquisition_image, jobs_volume, "clear", fence_token)
        except OperationError:
            errors.append("Acquisition fence")
    # Dashboard remains last so admission is not reopened before the other services are healthy.
    dashboard = containers.get("dashboard", [])
    if "dashboard" in stopped and dashboard:
        try:
            start_and_wait(dashboard[0])
        except OperationError:
            errors.append("dashboard")
    if errors:
        raise OperationError("No se pudo recuperar el estado de origen: " + ", ".join(errors))


def _fence(acquisition_image: str, volume: str, action: str, token: str):
    command = ["docker", "run", "--rm", "--network", "none", "--user", "10001:10001",
               "-v", f"{volume}:/acquisition-jobs", "-v", f"{HELPER}:/rja/postgresql_backup_helper.py:ro",
               "--entrypoint", "python", acquisition_image, "-B", "/rja/postgresql_backup_helper.py",
               "fence", action, "--root", "/acquisition-jobs", "--token", token]
    _run(command, timeout=120)


def _acquisition_check(acquisition_image: str, volume: str) -> dict:
    command = ["docker", "run", "--rm", "--network", "none", "--user", "10001:10001",
               "-v", f"{volume}:/acquisition-jobs:ro", "-v", f"{HELPER}:/rja/postgresql_backup_helper.py:ro",
               "--entrypoint", "python", acquisition_image, "-B", "/rja/postgresql_backup_helper.py",
               "acquisition-check", "--root", "/acquisition-jobs"]
    try:
        return json.loads(_text(command, timeout=120))
    except json.JSONDecodeError:
        fail("La SQLite operativa de Acquisition no se pudo verificar")


def _tree_fingerprint(root: Path) -> str:
    if root.is_symlink() or not root.is_dir():
        fail("No se pudo calcular la huella del código")
    digest = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file()
                   and "__pycache__" not in path.parts and path.suffix != ".pyc")
    for path in files:
        relative = path.relative_to(root).as_posix()
        _, file_hash = _digest_file(path)
        digest.update(f"{file_hash}  {relative}\n".encode("utf-8"))
    return digest.hexdigest()


def _sports_code_fingerprint(root: Path) -> str:
    carga = root / "sports-data/persistencia/carga.py"
    try:
        parsed = ast.parse(carga.read_text(encoding="utf-8"))
        constants = {}
        for node in parsed.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in {
                            "ARCHIVOS_ENRIQUECIMIENTO", "ARCHIVOS_IDENTIDAD"}:
                        constants[target.id] = ast.literal_eval(node.value)
        if set(constants) != {"ARCHIVOS_ENRIQUECIMIENTO", "ARCHIVOS_IDENTIDAD"}:
            fail("No se pudo reproducir la huella de código de Sports Data")
        base = root / "sports-data"
        paths = {"api.py", "queries.py", "importar.py", "alembic.ini", "requirements-sql.txt"}
        paths.update(constants["ARCHIVOS_ENRIQUECIMIENTO"])
        paths.update(constants["ARCHIVOS_IDENTIDAD"])
        paths.update(path.relative_to(base).as_posix()
                     for path in (base / "persistencia").rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts
                     and path.suffix in {".py", ".mako"})
        digest = hashlib.sha256()
        for relative in sorted(paths):
            _, file_hash = _digest_file(base / relative)
            digest.update(f"{file_hash}  {relative}\n".encode("utf-8"))
        return digest.hexdigest()
    except (OSError, SyntaxError, ValueError):
        fail("No se pudo reproducir la huella de código de Sports Data")


def _code_fingerprint_command(kind: str) -> str:
    if kind == "sports":
        return ("import sys;sys.path.insert(0,'/app');from persistencia.carga import referencia_del_codigo;"
                "print(referencia_del_codigo()['sha256_agregada'])")
    return ("import hashlib,pathlib;root=pathlib.Path('/app/src');"
            "files=sorted(p for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts "
            "and p.suffix!='.pyc');d=hashlib.sha256();"
            "[d.update((hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(root).as_posix()+'\\n').encode()) "
            "for p in files];print(d.hexdigest())")


def _container_code_fingerprint(container: dict, kind: str) -> str:
    command = ["docker", "exec", container["id"], "python", "-B", "-c", _code_fingerprint_command(kind)]
    value = _text(command, timeout=120)
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        fail(f"No se pudo identificar la huella del código activo de {container['service']}")
    return value


def _image_code_fingerprint(image_id: str, kind: str) -> str:
    command = ["docker", "run", "--rm", "--network", "none", "--entrypoint", "python", image_id,
               "-B", "-c", _code_fingerprint_command(kind)]
    value = _text(command, timeout=180)
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        fail("La imagen administrativa no expone una huella de código válida")
    return value


def _verified_admin_helpers(config: dict, names: dict, runtime_refs: dict[str, str]) -> dict:
    mapping = {"sports": ("migrate-sports-data", "sports-data", "migrate-sports-data"),
               "video": ("migrate-video-service", "video-service", "migrate-video-service")}
    expected_fingerprints = {"sports": _sports_code_fingerprint(ROOT),
                             "video": _tree_fingerprint(ROOT / "video-service/src")}
    result = {}
    for kind, (service_name, runtime_service, expected_compose_service) in mapping.items():
        image_ref = _service_image(config["services"][service_name])
        raw = _text(["docker", "image", "inspect", "--format",
                     '{{.Id}}|{{index .Config.Labels "com.docker.compose.project"}}|'
                     '{{index .Config.Labels "com.docker.compose.service"}}', image_ref])
        image_id, project, compose_service = _parse_admin_image_identity(
            raw, names["project"], expected_compose_service)
        if image_ref == runtime_refs[runtime_service]:
            fail(f"La imagen administrativa de {runtime_service} comparte etiqueta con su imagen de ejecución")
        fingerprint = _image_code_fingerprint(image_id, kind)
        if fingerprint != expected_fingerprints[kind]:
            fail(f"La imagen administrativa de {runtime_service} no contiene el código de este checkout")
        result[kind] = {"reference": image_ref, "image_id": image_id,
                        "compose_project": project, "compose_service": compose_service,
                        "code_fingerprint_algorithm": "sports-data-reference-v1" if kind == "sports"
                        else "video-service-src-tree-v1", "code_sha256": fingerprint}
    return result


def _source_fixture_marker(sports_image: str, volume: str) -> bool:
    command = ["docker", "run", "--rm", "--network", "none", "--user", "10001:10001",
               "-v", f"{volume}:/state:ro", "-v", f"{HELPER}:/rja/postgresql_backup_helper.py:ro",
               "--entrypoint", "python", sports_image, "-B", "/rja/postgresql_backup_helper.py",
               "marker-check", "--root", "/state", "--name", ".acquisition-test-installation"]
    value = _text(command, timeout=120)
    if value not in {"present", "absent"}:
        fail("No se pudo comprobar el marcador de instalación fixture")
    return value == "present"


def _app_check(config: dict, names: dict, kind: str, mode: str) -> None:
    if kind == "sports":
        service, credential_key, variable, image = (
            "sports-data", "sports_data_database_url", "SPORTS_DATA_DATABASE_URL_FILE",
            _service_image(config["services"]["sports-data"]))
    else:
        service, credential_key, variable, image = (
            "video-service", "video_service_database_url", "VIDEO_SERVICE_DATABASE_URL_FILE",
            _service_image(config["services"]["video-service"]))
    credential = _file_secret(config, credential_key)
    groups = config["services"][service].get("group_add", [])
    if len(groups) != 1 or not str(groups[0]).isdigit() or int(groups[0]) == 0:
        fail("La configuración de servicio no especifica el GID privado de secretos")
    command = ["docker", "run", "--rm", "--network", names["postgres_network"],
               "--user", "10001:10001", "--group-add", str(groups[0]),
               "-v", f"{credential}:/run/secrets/database-url:ro",
               "-v", f"{DEPLOY_CHECK}:/rja/postgresql-deployment-check.py:ro",
               "-e", f"{variable}=/run/secrets/database-url", "--entrypoint", "python", image, "-B",
               "/rja/postgresql-deployment-check.py", mode]
    if _text(command, timeout=180) != "ok":
        fail("Falló el preflight del rol de aplicación PostgreSQL")


def _migration_check(config: dict, names: dict, kind: str, mode: str) -> str:
    if kind == "sports":
        service, credential_key, variable = (
            "sports-data", "sports_data_migration_database_url", "SPORTS_DATA_MIGRATION_DATABASE_URL_FILE")
    else:
        service, credential_key, variable = (
            "video-service", "video_service_migration_database_url", "VIDEO_SERVICE_MIGRATION_DATABASE_URL_FILE")
    image = _service_image(config["services"][service])
    credential = _file_secret(config, credential_key)
    command = ["docker", "run", "--rm", "--network", names["postgres_network"], "--user", "0:0",
               "-v", f"{credential}:/run/secrets/database-url:ro",
               "-v", f"{DEPLOY_CHECK}:/rja/postgresql-deployment-check.py:ro",
               "-e", f"{variable}=/run/secrets/database-url", "--entrypoint", "python", image, "-B",
               "/rja/postgresql-deployment-check.py", mode]
    return _text(command, timeout=180)


def _snapshot(config: dict, names: dict, kind: str) -> dict:
    if kind == "sports":
        service, credential_key, variable = (
            "sports-data", "sports_data_migration_database_url", "SPORTS_DATA_MIGRATION_DATABASE_URL_FILE")
    else:
        service, credential_key, variable = (
            "video-service", "video_service_migration_database_url", "VIDEO_SERVICE_MIGRATION_DATABASE_URL_FILE")
    image = _service_image(config["services"][service])
    credential = _file_secret(config, credential_key)
    command = ["docker", "run", "--rm", "--network", names["postgres_network"], "--user", "0:0",
               "-v", f"{credential}:/run/secrets/database-url:ro",
               "-v", f"{HELPER}:/rja/postgresql_backup_helper.py:ro",
               "-e", f"{variable}=/run/secrets/database-url", "--entrypoint", "python", image, "-B",
               "/rja/postgresql_backup_helper.py", "snapshot", kind]
    try:
        return json.loads(_text(command, timeout=2400))
    except json.JSONDecodeError:
        fail("No se pudo crear el inventario lógico de la base de datos")


def _identity(config: dict, names: dict, kind: str) -> dict:
    if kind == "sports":
        service, credential_key, variable = (
            "sports-data", "sports_data_migration_database_url", "SPORTS_DATA_MIGRATION_DATABASE_URL_FILE")
    else:
        service, credential_key, variable = (
            "video-service", "video_service_migration_database_url", "VIDEO_SERVICE_MIGRATION_DATABASE_URL_FILE")
    image = _service_image(config["services"][service])
    credential = _file_secret(config, credential_key)
    command = ["docker", "run", "--rm", "--network", names["postgres_network"], "--user", "0:0",
               "-v", f"{credential}:/run/secrets/database-url:ro",
               "-v", f"{HELPER}:/rja/postgresql_backup_helper.py:ro",
               "-e", f"{variable}=/run/secrets/database-url", "--entrypoint", "python", image, "-B",
               "/rja/postgresql_backup_helper.py", "identity", kind]
    try:
        return json.loads(_text(command, timeout=180))
    except json.JSONDecodeError:
        fail("No se pudo autenticar el rol owner de destino")


def _digest_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _canonical_postgresql_definition(definition: str) -> str:
    """Normalize redundant PostgreSQL deparser casts in schema expressions.

    PostgreSQL 18 may render the same VARCHAR array comparison with a cast on
    the array or on each string literal after a dump/restore cycle. This keeps
    snapshot verification strict while ignoring only those equivalent forms.
    """
    definition = definition.replace("::character varying::text", "::character varying")
    definition = re.sub(r"\(('(?:[^']|'')*'::character varying)\)::text", r"\1", definition)
    definition = re.sub(r"\((ARRAY\[.*?\])\)::text\[\]", r"\1", definition)
    definition = re.sub(r"(ARRAY\[.*?\])::text\[\]", r"\1", definition)
    return definition


def _canonical_snapshot(snapshot: dict) -> dict:
    canonical = dict(snapshot)
    for key in ("constraints", "indexes"):
        canonical[key] = [
            row[:3] + [_canonical_postgresql_definition(row[3])] + row[4:]
            for row in snapshot[key]
        ]
    return canonical


def _write_json(path: Path, value: dict) -> None:
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _secure_parent(destination: Path) -> Path:
    absolute = Path(os.path.abspath(destination))
    parent = absolute.parent
    if absolute.name in {"", ".", ".."}:
        fail("El destino no es una carpeta válida")
    current = Path(parent.anchor)
    for part in parent.parts[1:]:
        current /= part
        if current.exists() or current.is_symlink():
            if current.is_symlink() or not current.is_dir():
                fail("El directorio padre del backup contiene un componente inseguro")
        else:
            current.mkdir(mode=0o700)
    if parent.is_symlink() or not parent.is_dir():
        fail("El directorio padre del backup no es seguro")
    os.chmod(parent, 0o700)
    return absolute


def _check_archive(pg_id: str, path: Path) -> None:
    with path.open("rb") as archive:
        result = _run(["docker", "exec", "-i", pg_id, "pg_restore", "--list"],
                      stdin=archive, timeout=600)
    if b"TABLE DATA" not in result.stdout or not result.stdout.strip():
        fail(f"pg_restore no puede inspeccionar {path.name}")


def _dump(pg_id: str, database: str, target: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            result = _run(["docker", "exec", pg_id, "pg_dump", "--no-password", "--username", "postgres",
                           "--format=custom", "--no-owner", "--dbname", database],
                          stdout=output, timeout=3600)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        try:
            target.unlink()
        except OSError:
            pass
        raise


def _verify_files(backup: Path, *, inspect_dump=None) -> dict:
    if backup.is_symlink() or not backup.is_dir():
        fail("La carpeta del backup no es regular")
    if backup.stat().st_mode & 0o077:
        fail("La carpeta del backup debe tener permisos privados")
    manifest_path = backup / MANIFEST_NAME
    sums_path = backup / "SHA256SUMS"
    if manifest_path.is_symlink() or sums_path.is_symlink() or not manifest_path.is_file() or not sums_path.is_file():
        fail("El backup no contiene manifiesto y SHA256SUMS regulares")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("El manifiesto del backup no se puede leer")
    if manifest.get("format") != "real-jaen-postgresql-backup" or manifest.get("format_version") != FORMAT_VERSION:
        fail("El formato del backup PostgreSQL no es compatible")
    checksums = {}
    try:
        lines = sums_path.read_text(encoding="ascii").splitlines()
        for line in lines:
            digest, name = line.split("  ", 1)
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                fail("SHA256SUMS contiene un hash no válido")
            if name in checksums or Path(name).name != name:
                fail("SHA256SUMS contiene rutas o nombres duplicados")
            checksums[name] = digest
    except (OSError, UnicodeDecodeError, ValueError):
        fail("SHA256SUMS no es válido")
    expected_names = set(PAYLOADS) | {MANIFEST_NAME}
    if set(checksums) != expected_names:
        fail("SHA256SUMS no coincide con el inventario esperado del formato v3")
    actual_names = {path.name for path in backup.iterdir()}
    if actual_names != expected_names | {"SHA256SUMS"}:
        fail("El directorio contiene archivos adicionales ajenos al formato v3")
    for name, digest in checksums.items():
        path = backup / name
        if path.is_symlink() or not path.is_file():
            fail("Falta un archivo regular del backup")
        if path.stat().st_mode & 0o077:
            fail("Los archivos del backup deben tener permisos privados")
        size, actual = _digest_file(path)
        if actual != digest:
            fail(f"El archivo {name} no coincide con SHA256SUMS")
        if name != MANIFEST_NAME:
            recorded = manifest.get("files", {}).get(name)
            if not isinstance(recorded, dict) or recorded.get("sha256") != actual or recorded.get("bytes") != size:
                fail(f"El manifiesto no coincide con {name}")
    if set(manifest.get("files", {})) != set(PAYLOADS):
        fail("El manifiesto no incluye exactamente los componentes v3")
    if inspect_dump:
        for name in ("sports_data.dump", "video_service.dump"):
            inspect_dump(backup / name)
    with (backup / "acquisition-history.tar").open("rb") as archive_input:
        acquisition = subprocess.run([sys.executable, str(HELPER), "acquisition-validate", "--root", "/tmp"],
                                     stdin=archive_input, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, check=False)
    if acquisition.returncode != 0:
        fail("El archivo de historial de Acquisition no supera la validación")
    try:
        archive_summary = json.loads(acquisition.stdout)
    except json.JSONDecodeError:
        fail("El historial de Acquisition tiene metadatos no válidos")
    expected_acq = manifest.get("acquisition", {})
    for key in ("jobs_total", "jobs_completed", "operation_ledgers", "package_files", "files", "total_bytes",
                "file_inventory"):
        if archive_summary.get(key) != expected_acq.get(key):
            fail("El historial de Acquisition no coincide con el manifiesto")
    _verify_backup_provenance(manifest)
    return manifest


def _verify_backup_provenance(manifest: dict) -> None:
    tool = manifest.get("backup_tool")
    source = manifest.get("source", {})
    runtime = source.get("runtime_images")
    administrative = source.get("administrative_images")
    if tool is None and runtime is None and administrative is None:
        return  # Compatible with earlier v3 manifests that predate explicit image provenance.
    if not isinstance(tool, dict) or not isinstance(runtime, dict) or not isinstance(administrative, dict):
        fail("La procedencia de imágenes del backup está incompleta")
    if (tool.get("version") != BACKUP_TOOL_VERSION
            or not isinstance(tool.get("commit"), str) or not re.fullmatch(r"[0-9a-f]{40,64}", tool["commit"])
            or type(tool.get("working_tree_dirty")) is not bool
            or any(not re.fullmatch(r"[0-9a-f]{64}", tool.get(key, ""))
                   for key in ("script_sha256", "helper_sha256"))):
        fail("La procedencia de la herramienta de backup no es válida")
    if set(runtime) != set(SERVICES):
        fail("El manifiesto no identifica todas las imágenes de ejecución")
    for service, item in runtime.items():
        if (not isinstance(item, dict)
                or not re.fullmatch(r"[0-9a-f]{64}", item.get("container_id", ""))
                or not isinstance(item.get("configured_reference"), str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("image_id", ""))
                or type(item.get("image_object_available")) is not bool):
            fail(f"La procedencia de la imagen de {service} no es válida")
        fingerprint = item.get("code_sha256")
        algorithm = item.get("code_fingerprint_algorithm")
        if (fingerprint is None) != (algorithm is None) or (fingerprint is not None and
                (not re.fullmatch(r"[0-9a-f]{64}", fingerprint) or not isinstance(algorithm, str))):
            fail(f"La huella de código de {service} no es válida")
    if administrative:
        if set(administrative) != {"sports", "video"}:
            fail("El manifiesto no identifica ambas imágenes administrativas")
        for kind, expected_service in (("sports", "migrate-sports-data"),
                                       ("video", "migrate-video-service")):
            item = administrative[kind]
            if (not isinstance(item, dict)
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("image_id", ""))
                    or item.get("compose_project") != source.get("project")
                    or item.get("compose_service") != expected_service
                    or not isinstance(item.get("reference"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", item.get("code_sha256", ""))
                    or item.get("code_fingerprint_algorithm") != (
                        "sports-data-reference-v1" if kind == "sports" else "video-service-src-tree-v1")):
                fail(f"La procedencia de la imagen administrativa {kind} no es válida")
        for service in ("sports-data", "video-service"):
            expected_algorithm = "sports-data-reference-v1" if service == "sports-data" else "video-service-src-tree-v1"
            if (runtime[service].get("image_object_available")
                    or runtime[service].get("code_fingerprint_algorithm") != expected_algorithm
                    or not re.fullmatch(r"[0-9a-f]{64}", runtime[service].get("code_sha256", ""))):
                fail("La excepción de recuperación no registró un objeto de imagen ausente")


def _restore_dump(pg_id: str, database: str, owner: str, path: Path) -> None:
    _run(["docker", "exec", "-i", pg_id, "pg_restore", "--exit-on-error", "--single-transaction",
          "--username", "postgres", "--no-owner", "--role", owner, "--dbname", database],
         stdin=path.open("rb"), timeout=7200)


def _restore_marker(import_dir: Path, project: str, volumes: dict, backup_hash: str) -> tuple[Path, str]:
    marker = import_dir / ".real-jaen-restore-incomplete"
    if marker.exists() or marker.is_symlink():
        fail("Ya existe un marcador de restauración incompleta")
    token = uuid.uuid4().hex
    contents = (f"format=3\nproject={project}\nsource_manifest_sha256={backup_hash}\n"
                f"volumes={','.join(volumes.values())}\nrestore_id={token}\n").encode("ascii")
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(contents)
        output.flush()
        os.fsync(output.fileno())
    _sync_dir(import_dir)
    return marker, token


def _clear_restore_marker(marker: Path, token: str) -> None:
    if marker.is_symlink() or not marker.is_file():
        fail("El marcador de restauración desapareció o fue sustituido")
    contents = marker.read_text(encoding="ascii")
    if f"restore_id={token}\n" not in contents:
        fail("El marcador de restauración no pertenece a esta operación")
    marker.unlink()
    _sync_dir(marker.parent)


def _restore_target_volumes_empty(config: dict, names: dict, containers: dict):
    acquisition_image = _service_image(config["services"]["acquisition"])
    for logical in ("sports-operational-state", "video-operational-state", "acquisition-jobs", "acquisition-profiles"):
        volume = names["volumes"][logical]
        command = ["docker", "run", "--rm", "--network", "none", "--user", "10001:10001",
                   "-v", f"{volume}:/target:ro", "-v", f"{HELPER}:/rja/postgresql_backup_helper.py:ro",
                   "--entrypoint", "python", acquisition_image, "-B", "/rja/postgresql_backup_helper.py",
                   "empty-check", "--root", "/target"]
        if _text(command, timeout=120) != "empty":
            fail(f"El volumen de destino {logical} no está vacío o no puede leerse")
        writable = ["docker", "run", "--rm", "--network", "none", "--user", "10001:10001",
                    "-v", f"{volume}:/target", "-v", f"{HELPER}:/rja/postgresql_backup_helper.py:ro",
                    "--entrypoint", "python", acquisition_image, "-B", "/rja/postgresql_backup_helper.py",
                    "empty-check", "--root", "/target", "--writable"]
        if _text(writable, timeout=120) != "empty":
            fail(f"El volumen de destino {logical} no es escribible por UID 10001")


def _pg_target_empty(pg_id: str) -> dict:
    pg = _pg_json(pg_id)
    _db_check(pg, empty=True)
    for database in ("sports_data", "video_service"):
        empty = _pg_sql(pg_id, database,
                        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')")
        if empty != "0":
            fail("La restauración solo se admite sobre bases de destino vacías")
    return pg


def _verify_manifest_identity(manifest: dict, names: dict):
    source = manifest.get("source", {})
    if names["project"] == source.get("project"):
        fail("La restauración exige un proyecto Compose distinto del origen")
    source_volumes = set(source.get("volumes", {}).values())
    if source_volumes.intersection(names["volumes"].values()):
        fail("El destino comparte un volumen con la instalación de origen")
    source_networks = set(source.get("networks", {}).values())
    if source_networks.intersection(names["networks"].values()):
        fail("El destino comparte una red con la instalación de origen")


def backup(destination: Path, *, recovery_unavailable_runtime_images: bool = False):
    config = _config()
    names = _names(config)
    containers = _service_containers(names, required=True)
    unavailable_image_services = {"sports-data", "video-service"} if recovery_unavailable_runtime_images else set()
    mount_info = _validate_compose_installation(
        config, names, containers, require_running=True, require_all=True,
        allow_missing_image_object_for=unavailable_image_services)
    _assert_no_foreign_volume_attachments(names, containers)
    pg_id = _pg_id(containers)
    pg = _pg_json(pg_id)
    _db_check_source(pg)
    marker = _check_marker(mount_info["import_dir"], ".real-jaen-restore-incomplete")
    acq_env = config["services"]["acquisition"].get("environment", {})
    sports_env = config["services"]["sports-data"].get("environment", {})
    if (acq_env.get("ACQ_PROVIDER") != "sofascore" or acq_env.get("ACQ_ENABLE_REAL") != "1"
            or sports_env.get("RJ_ACQUISITION_TEST") in {"1", 1, True}):
        fail("El backup v3 de origen requiere Acquisition en modo real y sin overlay de fixtures")
    runtime_refs = {service: _service_image(config["services"][service])
                    for service in ("sports-data", "video-service")}
    helper_config = config
    administrative_images = {}
    runtime_code = {}
    if recovery_unavailable_runtime_images:
        # This opt-in recovery path is limited to two containers whose exact image objects are absent.
        # All recovery helpers run from separately tagged, project-labeled images whose source fingerprints
        # must match this checkout. The live containers and their code fingerprints remain recorded as-is.
        administrative_images = _verified_admin_helpers(config, names, runtime_refs)
        runtime_code = {
            "sports-data": _container_code_fingerprint(containers["sports-data"][0], "sports"),
            "video-service": _container_code_fingerprint(containers["video-service"][0], "video"),
        }
        stored_code = _pg_sql(pg_id, "sports_data", """
            SELECT coalesce(code_sha256, '') FROM sports_data_publication_state WHERE singleton_id=1
        """)
        if not stored_code or stored_code != runtime_code["sports-data"]:
            fail("La huella deportiva activa no coincide con la generación publicada; no se suspende el servicio")
        helper_config = dict(config)
        helper_config["services"] = dict(config["services"])
        helper_config["services"]["sports-data"] = {
            **config["services"]["sports-data"], "image": administrative_images["sports"]["image_id"]}
        helper_config["services"]["video-service"] = {
            **config["services"]["video-service"], "image": administrative_images["video"]["image_id"]}
    sports_helper_image = (_service_image(config["services"]["sports-data"])
                           if not recovery_unavailable_runtime_images
                           else administrative_images["sports"]["image_id"])
    if _source_fixture_marker(sports_helper_image,
                              names["volumes"]["sports-operational-state"]):
        fail("El origen contiene el marcador de seguridad de instalación fixture")

    destination = _secure_parent(destination)
    if destination.exists() or destination.is_symlink():
        fail("El destino ya existe; los backups no se sobrescriben")
    free_bytes = shutil.disk_usage(destination.parent).free
    database_bytes = sum(row["bytes"] for row in pg["databases"])
    if free_bytes < database_bytes * 2 + 64 * 1024 * 1024:
        fail("No hay espacio suficiente para los dumps y el historial operativo")

    token = uuid.uuid4().hex
    work = Path(tempfile.mkdtemp(prefix=f".{destination.name}.partial-", dir=destination.parent))
    os.chmod(work, 0o700)
    was_running = {service for service, items in containers.items() if items and items[0]["state"] == "running"}
    stopped = set()
    fenced = False
    completed = False
    acq_image = _service_image(config["services"]["acquisition"])
    jobs_volume = names["volumes"]["acquisition-jobs"]
    dashboard = containers["dashboard"][0]
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def handle_sigterm(_signum, _frame):
        raise OperationError("El backup recibió una señal de terminación; se recuperará el estado de origen")

    signal.signal(signal.SIGTERM, handle_sigterm)
    try:
        # This mirrors deployment admission closure: stop the sole producer, drain for five consecutive seconds,
        # create the same durable Acquisition fence used by SINBAD², then recheck before stopping workers.
        stopped.add("dashboard")
        _run(["docker", "stop", "--time", "30", dashboard["id"]], timeout=120)
        idle_since = None
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            sports_active = int(_migration_check(helper_config, names, "sports", "sports-active"))
            video_active = int(_migration_check(helper_config, names, "video", "video-active"))
            acq = _acquisition_check(acq_image, jobs_volume)
            if sports_active == 0 and video_active == 0 and acq["active_jobs"] == 0:
                idle_since = idle_since or time.monotonic()
                if time.monotonic() - idle_since >= 5:
                    break
            else:
                idle_since = None
            time.sleep(1)
        else:
            fail("Los trabajos activos no se drenaron; no se generó el backup")
        fenced = True
        _fence(acq_image, jobs_volume, "create", token)
        if int(_migration_check(helper_config, names, "sports", "sports-active")) != 0 \
                or int(_migration_check(helper_config, names, "video", "video-active")) != 0 \
                or _acquisition_check(acq_image, jobs_volume)["active_jobs"] != 0:
            fail("Apareció un trabajo tras cerrar admisión; se cancela el backup")

        for service in ("sports-data", "video-service", "acquisition"):
            item = containers[service][0]
            if service in was_running:
                stopped.add(service)
                _run(["docker", "stop", "--time", "30", item["id"]], timeout=120)
        if containers["postgresql"][0]["id"] != pg_id:
            fail("Cambió el contenedor PostgreSQL durante la operación")

        # All workers are stopped, so the two databases and local operational history describe one coherent point.
        sports = _snapshot(helper_config, names, "sports")
        video = _snapshot(helper_config, names, "video")
        if sports["uncertain_publication_jobs"] or sports["dangling_publication_state"]:
            fail("El estado de publicación deportiva es incierto; no se genera el backup")
        _dump(pg_id, "sports_data", work / "sports_data.dump")
        _dump(pg_id, "video_service", work / "video_service.dump")
        for name in ("sports_data.dump", "video_service.dump"):
            _check_archive(pg_id, work / name)

        # The helper takes a SQLite backup-API snapshot and only carries terminal job metadata, sealed packages,
        # and the durable send-budget ledger. It never includes profile, cookie, transport, log, or staging files.
        export = ["docker", "run", "--rm", "--network", "none", "--user", "10001:10001",
                  "-v", f"{jobs_volume}:/acquisition-jobs:ro",
                  "-v", f"{HELPER}:/rja/postgresql_backup_helper.py:ro",
                  "--entrypoint", "python", acq_image, "-B", "/rja/postgresql_backup_helper.py",
                  "acquisition-export", "--root", "/acquisition-jobs", "--sqlite-copy", "/tmp/jobs-copy.sqlite"]
        archive_file = work / "acquisition-history.tar"
        fd = os.open(archive_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as archive_out:
            try:
                result = subprocess.run(export, cwd=ROOT, stdout=archive_out, stderr=subprocess.PIPE,
                                        timeout=1800, check=False)
            except (OSError, subprocess.TimeoutExpired):
                fail("No se pudo exportar el historial local de Acquisition")
            if result.returncode != 0:
                fail("El historial local de Acquisition no se pudo exportar")
            archive_out.flush()
            os.fsync(archive_out.fileno())
        try:
            acquisition_export = json.loads(result.stderr)
        except json.JSONDecodeError:
            fail("El helper de Acquisition no produjo un inventario válido")
        with archive_file.open("rb") as archive_input:
            archive_check = subprocess.run([sys.executable, str(HELPER), "acquisition-validate", "--root", "/tmp"],
                                           stdin=archive_input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           check=False)
        if archive_check.returncode != 0:
            fail("El historial operativo de Acquisition no superó la validación")
        archive_summary = json.loads(archive_check.stdout)
        acquisition = {**acquisition_export,
                       "files": archive_summary["files"],
                       "total_bytes": archive_summary["total_bytes"],
                       "file_inventory": archive_summary["file_inventory"]}
        acq_full = _acquisition_check(acq_image, jobs_volume)
        if acq_full["active_jobs"] != 0 or acquisition["jobs_total"] != acq_full["jobs_total"]:
            fail("El journal de Acquisition cambió durante la copia")

        _start_original(containers, stopped, acquisition_image=acq_image, jobs_volume=jobs_volume,
                        fence_token=token)
        fenced = False
        stopped.clear()

        # Source recovery must be complete before publication of the archive as a finished backup.
        recovered = _service_containers(names, required=True)
        _validate_compose_installation(
            config, names, recovered, require_running=True, require_all=True,
            allow_missing_image_object_for=unavailable_image_services)
        if {service: recovered[service][0]["id"] for service in SERVICES} != {
                service: containers[service][0]["id"] for service in SERVICES}:
            fail("La recuperación sustituyó uno o más contenedores originales")
        if recovery_unavailable_runtime_images:
            recovered_code = {
                "sports-data": _container_code_fingerprint(recovered["sports-data"][0], "sports"),
                "video-service": _container_code_fingerprint(recovered["video-service"][0], "video"),
            }
            if recovered_code != runtime_code:
                fail("El código observado tras reiniciar no coincide con el de los contenedores originales")
        if _check_marker(mount_info["import_dir"], ".real-jaen-restore-incomplete") != marker:
            fail("Cambió el marcador de restauración del origen")

        tool_commit = _text(["git", "rev-parse", "HEAD"])
        tool_dirty = bool(_text(["git", "status", "--porcelain", "--",
                                 "scripts/postgresql_backup.py", "scripts/postgresql_backup_helper.py"]))
        runtime_images = {}
        for service in SERVICES:
            container = containers[service][0]
            runtime_images[service] = {
                "container_id": container["id"], "configured_reference": container["image"],
                "image_id": container["image_id"],
                "image_object_available": _image_object_available(container["image_id"]),
            }
        for service, fingerprint in runtime_code.items():
            runtime_images[service]["code_fingerprint_algorithm"] = (
                "sports-data-reference-v1" if service == "sports-data" else "video-service-src-tree-v1")
            runtime_images[service]["code_sha256"] = fingerprint
        tool_info = {"version": BACKUP_TOOL_VERSION, "commit": tool_commit,
                     "working_tree_dirty": tool_dirty,
                     "script_sha256": _digest_file(Path(__file__))[1],
                     "helper_sha256": _digest_file(HELPER)[1]}
        manifest = {
            "format": "real-jaen-postgresql-backup",
            "format_version": FORMAT_VERSION,
            "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "backup_tool": tool_info,
            "postgresql_version": "18",
            "schema_revisions": {"sports_data": "pg0005", "video_service": "vg0002"},
            "source": {"project": names["project"], "networks": names["networks"],
                       "volumes": names["volumes"], "runtime_images": runtime_images,
                       "administrative_images": administrative_images},
            "databases": {"sports_data": sports, "video_service": video},
            "acquisition": acquisition,
            "operational_state": {
                "included": ["Acquisition jobs.sqlite snapshot", "completed sealed job packages",
                             "Acquisition send-budget ledgers"],
                "excluded": ["Sports Data local SQLite journal (non-authoritative in PostgreSQL mode)",
                             "Video Service local SQLite journal (non-authoritative in PostgreSQL mode)",
                             "active jobs", "Acquisition profile and cookies", "transport and staging",
                             "logs, caches, temp files, photos outside PostgreSQL, original videos"],
                "authority": "PostgreSQL is authoritative for sports updates, publications, video jobs, associations and calibrations.",
            },
            "files": {},
        }
        for name in PAYLOADS:
            size, digest = _digest_file(work / name)
            manifest["files"][name] = {"bytes": size, "sha256": digest}
        _write_json(work / MANIFEST_NAME, manifest)
        entries = []
        for name in (*PAYLOADS, MANIFEST_NAME):
            _, digest = _digest_file(work / name)
            entries.append(f"{digest}  {name}\n")
        sums = work / "SHA256SUMS"
        fd = os.open(sums, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as output:
            output.writelines(entries)
            output.flush()
            os.fsync(output.fileno())
        _sync_dir(work)
        _verify_files(work, inspect_dump=lambda path: _check_archive(pg_id, path))
        if destination.exists() or destination.is_symlink():
            fail("El destino apareció durante la copia; no se sobrescribe")
        os.rename(work, destination)
        _sync_dir(destination.parent)
        completed = True
        print(json.dumps({"backup": str(destination), "bytes": sum(path.stat().st_size for path in destination.iterdir()),
                          "created_at_utc": manifest["created_at_utc"],
                          "generation": sports["generation"],
                          "sports_counts": sports["table_counts"],
                          "video_counts": video["table_counts"],
                          "acquisition": {key: acquisition[key] for key in
                                          ("jobs_total", "jobs_completed", "operation_ledgers", "total_bytes")}},
                         ensure_ascii=False, sort_keys=True))
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            if stopped or fenced:
                try:
                    _start_original(containers, stopped, acquisition_image=acq_image, jobs_volume=jobs_volume,
                                    fence_token=token if fenced else None)
                    stopped.clear()
                    fenced = False
                except OperationError:
                    print("Aviso: no se recuperó el estado original completo; revisa los contenedores y la barrera de Acquisition.",
                          file=sys.stderr)
                    raise
            if not completed and work.exists():
                shutil.rmtree(work)
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)


def restore(backup_dir: Path):
    config = _config()
    names = _names(config)
    containers = _service_containers(names, required=False)
    _validate_compose_installation(config, names, containers, require_running=False, require_all=False,
                                   require_networks=False)
    manifest = _verify_files(backup_dir, inspect_dump=lambda path: _check_archive(_pg_id(containers), path))
    _verify_manifest_identity(manifest, names)
    _assert_no_foreign_volume_attachments(names, containers)
    pg_id = _pg_id(containers)
    pg = _pg_target_empty(pg_id)
    for kind in ("sports", "video"):
        identity = _identity(config, names, kind)
        expected = "sports_data" if kind == "sports" else "video_service"
        if identity["database"] != expected or identity["server_version_num"] != pg["version_num"]:
            fail("La credencial owner de destino no corresponde a su base PostgreSQL 18")
    _restore_target_volumes_empty(config, names, containers)
    import_dir = _check_marker(
        next(Path(mount["source"]) for mount in config["services"]["sports-data"]["volumes"]
             if isinstance(mount, dict) and mount.get("type") == "bind" and mount.get("target") == "/imports"),
        ".real-jaen-restore-incomplete").parent

    # A fresh durable marker is the first persistent write. Any failure after this point intentionally leaves it.
    backup_hash = _digest_file(backup_dir / MANIFEST_NAME)[1]
    marker, token = _restore_marker(import_dir, names["project"], names["volumes"], backup_hash)
    try:
        _restore_dump(pg_id, "sports_data", "sports_data_owner", backup_dir / "sports_data.dump")
        _restore_dump(pg_id, "video_service", "video_service_owner", backup_dir / "video_service.dump")
        acq_image = _service_image(config["services"]["acquisition"])
        jobs_volume = names["volumes"]["acquisition-jobs"]
        command = ["docker", "run", "--rm", "--network", "none", "--user", "10001:10001",
                   "-i", "-v", f"{jobs_volume}:/acquisition-jobs",
                   "-v", f"{HELPER}:/rja/postgresql_backup_helper.py:ro",
                   "--entrypoint", "python", acq_image, "-B", "/rja/postgresql_backup_helper.py",
                   "acquisition-restore", "--root", "/acquisition-jobs"]
        with (backup_dir / "acquisition-history.tar").open("rb") as archive:
            result = _run(command, stdin=archive, timeout=1800)
        summary = json.loads(result.stdout)
        for key in ("jobs_total", "jobs_completed", "operation_ledgers", "package_files", "files", "total_bytes",
                    "file_inventory"):
            if summary.get(key) != manifest["acquisition"].get(key):
                fail("El historial de Acquisition restaurado no coincide con el backup")

        sports = _snapshot(config, names, "sports")
        video = _snapshot(config, names, "video")
        if _canonical_snapshot(sports) != _canonical_snapshot(manifest["databases"]["sports_data"]) \
                or _canonical_snapshot(video) != _canonical_snapshot(manifest["databases"]["video_service"]):
            fail("La restauración PostgreSQL no coincide exactamente con los inventarios lógicos del backup")
        if sports["uncertain_publication_jobs"] or sports["dangling_publication_state"]:
            fail("La restauración contiene un estado deportivo de publicación incierto")
        if _migration_check(config, names, "sports", "sports-schema") != "ok" \
                or _migration_check(config, names, "video", "video-schema") != "ok":
            fail("Las revisiones PostgreSQL restauradas no superaron el preflight")
        _app_check(config, names, "sports", "sports-app")
        _app_check(config, names, "video", "video-app")
        acq = _acquisition_check(acq_image, jobs_volume)
        if acq["active_jobs"] or acq["jobs_total"] != manifest["acquisition"]["jobs_total"]:
            fail("El journal SQLite de Acquisition restaurado tiene trabajos activos o recuentos distintos")

        _clear_restore_marker(marker, token)
    except Exception:
        print("Restauración incompleta: el marcador durable se conserva para inspección administrativa.",
              file=sys.stderr)
        raise
    print(json.dumps({"restore": "verified", "project": names["project"],
                      "sports_tables": len(sports["all_tables"]), "video_tables": len(video["all_tables"]),
                      "acquisition_jobs": acq["jobs_total"], "marker": "cleared"}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    backup_cmd = sub.add_parser("backup")
    backup_cmd.add_argument("destination", type=Path)
    backup_cmd.add_argument(
        "--recovery-unavailable-runtime-images", action="store_true",
        help="Exceptional recovery: use verified admin images only when sports-data and video-service image objects are absent")
    verify_cmd = sub.add_parser("verify")
    verify_cmd.add_argument("backup", type=Path)
    restore_cmd = sub.add_parser("restore")
    restore_cmd.add_argument("backup", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "backup":
            backup(args.destination,
                   recovery_unavailable_runtime_images=args.recovery_unavailable_runtime_images)
        elif args.action == "verify":
            config = _config()
            names = _names(config)
            containers = _service_containers(names, required=False)
            manifest = _verify_files(args.backup, inspect_dump=lambda path: _check_archive(_pg_id(containers), path))
            print(json.dumps({"format_version": FORMAT_VERSION, "files": manifest["files"],
                              "generation": manifest["databases"]["sports_data"]["generation"]},
                             ensure_ascii=False, sort_keys=True))
        else:
            restore(args.backup)
    except OperationError as error:
        print(f"postgresql backup: {error}", file=sys.stderr)
        raise SystemExit(1)
    except Exception:
        print("postgresql backup: operación rechazada por una comprobación local", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
