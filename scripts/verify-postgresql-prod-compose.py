#!/usr/bin/env python3
"""Validate the effective production Compose network and secret boundary."""

from __future__ import annotations

import json
import sys


def fail(message: str) -> None:
    raise SystemExit(message)


def names(values: object) -> set[str]:
    result: set[str] = set()
    if isinstance(values, dict):
        return {str(value) for value in values}
    for value in values if isinstance(values, list) else []:
        result.add(value if isinstance(value, str) else str(value.get("source", "")))
    return result


def main() -> None:
    config = json.load(sys.stdin)
    services = config.get("services", {})
    if config.get("name") != "real-jaen-analytics-sinbad2-prod":
        fail("production Compose project identity is invalid")
    expected = {
        "postgresql", "sports-data", "acquisition", "video-service", "dashboard",
        "migrate-sports-data", "migrate-video-service",
    }
    if not expected.issubset(services):
        fail("production Compose is missing a required service")

    db = services["postgresql"]
    if db.get("image") != "postgres:18-bookworm" or db.get("ports"):
        fail("PostgreSQL must use v18 without a published host port")
    ports = services["dashboard"].get("ports", [])
    if len(ports) != 1:
        fail("Dashboard must be the only service with a published port")
    port = ports[0]
    if (port.get("host_ip"), str(port.get("published")), str(port.get("target"))) != (
        "127.0.0.1", "8090", "3000"
    ):
        fail("Dashboard must publish 127.0.0.1:8090 to container port 3000")
    for name in ("sports-data", "acquisition", "video-service"):
        if services[name].get("ports"):
            fail("internal services must not publish host ports")

    acquisition_env = services["acquisition"].get("environment", {})
    if acquisition_env.get("ACQ_PROVIDER") != "sofascore" or acquisition_env.get("ACQ_ENABLE_REAL") != "1":
        fail("production Acquisition must explicitly use the real SofaScore provider")
    if any(acquisition_env.get(key) != expected for key, expected in {
        "ACQ_MAX_ATTEMPTS": "48",
        "ACQ_MAX_SENDS": "52",
        "ACQ_UPDATE_RECENT_ROUNDS": "3",
        "ACQ_UPDATE_MAX_TOTAL_SENDS": "50",
    }.items()):
        fail("production Acquisition query and send limits must remain at their approved values")

    dashboard_env = services["dashboard"].get("environment", {})
    if dashboard_env.get("NEXT_PUBLIC_BASE_PATH") != "/real-jaen":
        fail("Dashboard must use basePath /real-jaen")
    networks = config.get("networks", {})
    pg_network = networks.get("postgresql-private", {})
    if pg_network.get("internal") is not True or pg_network.get("name") != "rja-sinbad2-prod-postgresql-private":
        fail("PostgreSQL network must be internal")
    for name in ("sports-data", "video-service"):
        service_networks = names(services[name].get("networks", []))
        if "postgresql-private" not in service_networks:
            fail("each database consumer must join the private PostgreSQL network")
    expected_networks = {
        "postgresql": {"postgresql-private"},
        "sports-data": {"sports-data-internal", "acquisition-control", "postgresql-private"},
        "acquisition": {"acquisition-control", "acquisition-egress"},
        "video-service": {"video-internal", "video-egress", "postgresql-private"},
        "dashboard": {"dashboard-edge", "sports-data-internal", "video-internal"},
    }
    for service, expected_network_set in expected_networks.items():
        if names(services[service].get("networks", [])) != expected_network_set:
            fail(f"{service} has an unexpected production network attachment")

    for service, own_secret, forbidden in (
        ("sports-data", "sports_data_database_url", {"video_service_database_url",
                                                       "video_service_migration_database_url",
                                                       "postgres_admin_password"}),
        ("video-service", "video_service_database_url", {"sports_data_database_url",
                                                          "sports_data_migration_database_url",
                                                          "postgres_admin_password"}),
    ):
        supplied = names(services[service].get("secrets", []))
        if own_secret not in supplied or supplied.intersection(forbidden):
            fail(f"{service} must receive only its runtime database URL")
    for service, own_secret in (
        ("migrate-sports-data", "sports_data_migration_database_url"),
        ("migrate-video-service", "video_service_migration_database_url"),
    ):
        if names(services[service].get("secrets", [])) != {own_secret}:
            fail(f"{service} must receive only its own migration URL")
    postgres_secrets = names(db.get("secrets", []))
    if postgres_secrets != {
        "postgres_admin_password", "sports_data_owner_password", "sports_data_app_password",
        "video_service_owner_password", "video_service_app_password",
    }:
        fail("PostgreSQL startup must receive only its administrator and role password files")
    for service in ("dashboard", "acquisition"):
        if names(services[service].get("secrets", [])).intersection({
            "sports_data_database_url", "sports_data_migration_database_url",
            "video_service_database_url", "video_service_migration_database_url",
            "postgres_admin_password", "sports_data_owner_password", "sports_data_app_password",
            "video_service_owner_password", "video_service_app_password",
        }):
            fail(f"{service} must not receive PostgreSQL credentials")

    volumes = config.get("volumes", {})
    expected_volumes = {
        "postgres-data": "rja-sinbad2-postgresql-data",
        "sports-operational-state": "rja-sinbad2-sports-operational-state",
        "video-operational-state": "rja-sinbad2-video-operational-state",
    }
    for key, expected_name in expected_volumes.items():
        if volumes.get(key, {}).get("name") != expected_name:
            fail("production PostgreSQL and operational volumes must use their prepared identities")

    def volume_sources(service: str) -> set[str]:
        return {str(item.get("source", "")) for item in services[service].get("volumes", [])
                if isinstance(item, dict)}

    if "postgres-data" not in volume_sources("postgresql"):
        fail("PostgreSQL must mount its persistent data volume")
    if "sports-operational-state" not in volume_sources("sports-data"):
        fail("Sports Data must mount its dedicated operational volume")
    if "video-operational-state" not in volume_sources("video-service"):
        fail("Video Service must mount its dedicated operational volume")
    if not {"acquisition-profiles", "acquisition-jobs"}.issubset(volume_sources("acquisition")):
        fail("Acquisition must retain its browser profile and SQLite jobs volumes")
    if services["sports-data"].get("environment", {}).get("SPORTS_DATA_STORAGE") != "postgresql":
        fail("Sports Data must use PostgreSQL in production")
    if services["sports-data"].get("environment", {}).get("SPORTS_DATA_DB") not in (None, ""):
        fail("Sports Data must not retain the SQLite database path in PostgreSQL mode")
    if services["video-service"].get("environment", {}).get("VIDEO_SERVICE_STORAGE") != "postgresql":
        fail("Video Service must use PostgreSQL in production")
    video_env = services["video-service"].get("environment", {})
    if video_env.get("VIDEO_SERVICE_DATA_DIR") not in (None, "") or video_env.get("VIDEO_SERVICE_MANIFEST") not in (None, ""):
        fail("Video Service must not retain SQLite catalog paths in PostgreSQL mode")
    secret_groups = []
    for service in ("sports-data", "video-service"):
        groups = services[service].get("group_add", [])
        if len(groups) != 1 or not str(groups[0]).isdigit() or int(groups[0]) == 0:
            fail("application secrets require the administrator-provisioned numeric GID")
        secret_groups.append(str(groups[0]))
    if services["acquisition"].get("group_add"):
        fail("Acquisition must not join the PostgreSQL runtime secret group")
    if len(set(secret_groups)) != 1:
        fail("application services must use the same administrator-provisioned secret GID")
    print("ok")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, TypeError, ValueError):
        fail("production Compose structure is invalid")
