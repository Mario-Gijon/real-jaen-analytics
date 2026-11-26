"""Focused, isolated SINBAD² PostgreSQL deployment checks; SSH and Docker are shims."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
COMMIT = "a" * 40
APP_SECRETS = {
    "APP_ACCESS_PASSWORD": secrets.token_urlsafe(18),
    "APP_UPDATE_PASSWORD": secrets.token_urlsafe(18),
    "APP_SESSION_SECRET": secrets.token_urlsafe(32),
    "APP_CONTROL_TOKEN": secrets.token_urlsafe(32),
    "APP_ACQUISITION_TOKEN": secrets.token_urlsafe(32),
}
VOLUMES = {
    "rja-sinbad2-postgresql-data",
    "rja-sinbad2-sports-operational-state",
    "rja-sinbad2-video-operational-state",
    "rja-sinbad2-acquisition-profiles",
    "rja-sinbad2-acquisition-jobs",
}

FAKE_GIT = r"""#!/bin/sh
set -eu
[ "$1" = archive ] || exit 90
"$RJA_REAL_GIT" -C "$RJA_TEST_SOURCE" ls-files -co --exclude-standard -z |
  tar -cf - -C "$RJA_TEST_SOURCE" --exclude='__pycache__' --exclude='*.pyc' --null -T -
"""

FAKE_SSH = r"""#!/bin/sh
set -eu
while [ "$#" -gt 0 ]; do
  case "$1" in
    -i|-o) shift 2 ;;
    *) break ;;
  esac
done
[ "$#" -ge 2 ] || exit 91
target=$1
shift
case "$target" in deploy@sinbad2.test) ;; *) exit 92 ;; esac
export HOME="$RJA_TEST_REMOTE_HOME"
if [ "$1" = sh ]; then
  shift
  exec /bin/sh "$@"
fi
exec /bin/sh -c "$1"
"""

FAKE_DOCKER = r"""#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path

args = sys.argv[1:]
log = Path(os.environ["RJA_TEST_DOCKER_LOG"])
with log.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\n")
state_path = Path(os.environ["RJA_TEST_DOCKER_STATE"])
state = json.loads(state_path.read_text()) if state_path.exists() else {
    "volumes": sorted(filter(None, os.environ.get("RJA_TEST_VOLUMES", "").split())),
    "deployed": True, "dashboard_stopped": False, "up_services": [], "up_count": 0,
    "active_sports_polls": 0, "active_video_polls": 0,
    "active_acquisition_polls": 0, "maintenance": False,
}

def save():
    state_path.write_text(json.dumps(state), encoding="utf-8")

def real_compose():
    result = subprocess.run([os.environ["RJA_REAL_DOCKER"], *args], check=False)
    raise SystemExit(result.returncode)

def command_index(commands):
    return next((index for index, value in enumerate(args) if value in commands), None)

if args[:3] == ["compose", "version", "--short"]:
    print(os.environ.get("RJA_TEST_COMPOSE_VERSION", "5.6.1"))
elif args[:2] == ["volume", "inspect"]:
    raise SystemExit(0 if args[2] in state["volumes"] else 1)
elif args[:2] == ["network", "inspect"]:
    raise SystemExit(0)
elif args[:1] == ["compose"]:
    index = command_index({"config", "build", "up", "ps", "stop", "start"})
    command = args[index] if index is not None else ""
    command_args = args[index + 1:] if index is not None else []
    if command == "config":
        real_compose()
    elif command == "build":
        raise SystemExit(0)
    elif command == "up":
        state["up_count"] += 1
        selected = [item for item in command_args if item in {
            "dashboard", "sports-data", "acquisition", "video-service", "postgresql"
        }]
        state["up_services"].append(selected)
        fail_at = int(os.environ.get("RJA_TEST_FAIL_UP_AT", "0"))
        if fail_at and state["up_count"] == fail_at:
            save()
            raise SystemExit(1)
        state["deployed"] = True
        if "dashboard" in selected:
            state["dashboard_stopped"] = False
        save()
    elif command == "ps":
        service = command_args[-1]
        if service == "postgresql" or (state["deployed"] and not
                (service == "dashboard" and state["dashboard_stopped"])):
            print("container-" + service)
    elif command == "stop":
        if "dashboard" in command_args:
            state["dashboard_stopped"] = True
            save()
    elif command == "start":
        if "dashboard" in command_args:
            state["dashboard_stopped"] = False
            save()
    else:
        raise SystemExit(93)
elif args[:2] == ["inspect", "--format"]:
    template = args[2]
    if ".State.Health.Status" in template:
        print("healthy")
    elif ".State.Status" in template:
        print("running")
    elif ".Config.Env" in template:
        service = args[-1].removeprefix("container-")
        if service == "sports-data":
            print("SPORTS_DATA_STORAGE=" + os.environ.get("RJA_TEST_SPORTS_STORAGE", "postgresql"))
        elif service == "video-service":
            print("VIDEO_SERVICE_STORAGE=" + os.environ.get("RJA_TEST_VIDEO_STORAGE", "postgresql"))
    elif ".Mounts" in template:
        if '"/var/lib/postgresql"' in template:
            print("rja-sinbad2-postgresql-data:true")
        else:
            print(os.environ.get("RJA_TEST_CONTAINER_BIND_MOUNT", ""))
    elif ".HostConfig.PortBindings" in template:
        print("{}")
    else:
        print("{}")
elif args[:2] == ["exec", "container-postgresql"]:
    print("t")
elif args[:2] == ["ps", "-q"]:
    pass
elif args[:2] == ["ps", "-aq"]:
    if os.environ.get("RJA_TEST_CONTAINER_BIND_MOUNT"):
        print("container-with-pinned-release")
elif args[:1] == ["run"]:
    command = " ".join(args)
    if os.environ.get("RJA_TEST_FAIL_SCHEMA") == "1" and any(
            value in args for value in ("sports-schema", "video-schema", "sports-app", "video-app")):
        raise SystemExit(1)
    if any(value in args for value in ("sports-schema", "sports-app", "video-schema", "video-app")):
        print("ok")
    elif "sports-active" in args:
        state["active_sports_polls"] += 1
        delay = int(os.environ.get("RJA_TEST_ACTIVE_SPORTS_POLLS", "0"))
        print("1" if state["active_sports_polls"] <= delay else "0")
    elif "video-active" in args:
        state["active_video_polls"] += 1
        delay = int(os.environ.get("RJA_TEST_ACTIVE_VIDEO_POLLS", "0"))
        print("1" if state["active_video_polls"] <= delay else "0")
    elif "print('present' if" in command:
        print("present" if state["maintenance"] else "absent")
    elif "os.O_EXCL" in command and "deployment-maintenance" in command:
        state["maintenance"] = True
        save()
    elif "p.unlink()" in command and "deployment-maintenance" in command:
        state["maintenance"] = False
        save()
    elif "SELECT COUNT(*) FROM job" in command:
        state["active_acquisition_polls"] += 1
        delay = int(os.environ.get("RJA_TEST_ACTIVE_ACQUISITION_POLLS", "0"))
        print("1" if state["active_acquisition_polls"] <= delay else "0")
    else:
        pass
elif args and args[0] in ("info", "image"):
    pass
else:
    print("unexpected fake Docker invocation", file=sys.stderr)
    raise SystemExit(94)
save()
"""


class SinbadPostgreSQLDeploymentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="rja-sinbad-pg-test-")
        self.base = Path(self.temp.name)
        self.bin_dir = self.base / "bin"
        self.bin_dir.mkdir()
        self.remote_home = self.base / "remote-home"
        self.remote_home.mkdir()
        self.file_vars = self.base / "file-vars"
        self.file_vars.mkdir()
        self.log = self.base / "docker.jsonl"
        self.state = self.base / "docker-state.json"
        self.secret_gid = str(os.getgid())
        self.write(self.bin_dir / "git", FAKE_GIT, 0o755)
        self.write(self.bin_dir / "ssh", FAKE_SSH, 0o755)
        self.write(self.bin_dir / "docker", FAKE_DOCKER, 0o755)
        self.git_key = self.file_vars / "deploy_key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.git_key)], check=True)
        key_type, key_value, *_ = self.git_key.with_suffix(".pub").read_text().split()
        self.write(self.file_vars / "known_hosts", f"sinbad2.test {key_type} {key_value}\n")
        self.write(self.file_vars / "ENV_PROD", (REPOSITORY / ".env.prod.example").read_text())
        postgres_env = (REPOSITORY / ".env.postgresql.prod.example").read_text().replace(
            "REPLACE_WITH_ADMIN_PROVISIONED_GID", self.secret_gid
        )
        self.write(self.file_vars / "ENV_POSTGRESQL_PROD", postgres_env)
        for name, value in APP_SECRETS.items():
            self.write(self.file_vars / name, value + "\n")

        shared = self.remote_home / "deploy_real-jaen-analytics" / "shared"
        imports = shared / "imports"
        pg_secrets = shared / "postgresql" / "secrets"
        imports.mkdir(parents=True)
        pg_secrets.mkdir(parents=True)
        imports.chmod(0o755)
        pg_secrets.chmod(0o700)
        for name in (
            "postgres_admin_password", "sports_data_owner_password", "sports_data_app_password",
            "video_service_owner_password", "video_service_app_password",
            "sports_data_migration_database_url", "video_service_migration_database_url",
        ):
            self.write(pg_secrets / name, "synthetic-test-credential\n", 0o600)
        for name in ("sports_data_database_url", "video_service_database_url"):
            self.write(pg_secrets / name, "postgresql://synthetic.invalid\n", 0o640)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def write(path: Path, value: str, mode: int = 0o600) -> None:
        path.write_text(value, encoding="utf-8")
        path.chmod(mode)

    def environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update({
            "PATH": f"{self.bin_dir}{os.pathsep}{env['PATH']}",
            "TMPDIR": str(self.base),
            "RJA_TEST_SOURCE": str(REPOSITORY),
            "RJA_REAL_GIT": shutil.which("git") or "/usr/bin/git",
            "RJA_REAL_DOCKER": shutil.which("docker") or "/usr/bin/docker",
            "RJA_TEST_REMOTE_HOME": str(self.remote_home),
            "RJA_TEST_DOCKER_LOG": str(self.log),
            "RJA_TEST_DOCKER_STATE": str(self.state),
            "RJA_TEST_VOLUMES": " ".join(sorted(VOLUMES)),
            "SSH_HOST": "sinbad2.test",
            "REMOTE_USER": "deploy",
            "CI_COMMIT_SHA": COMMIT,
            "CI_PIPELINE_ID": "101",
            "CI_JOB_ID": "501",
        })
        for name in ("ENV_PROD", "ENV_POSTGRESQL_PROD", *APP_SECRETS):
            env[name] = str(self.file_vars / name)
        env["SSH_PRIVATE_KEY"] = str(self.git_key)
        env["SSH_KNOWN_HOSTS"] = str(self.file_vars / "known_hosts")
        return env

    def invoke(self, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(REPOSITORY / "scripts/deploy-sinbad2.sh"), "deploy"],
            cwd=REPOSITORY,
            env=self.environment() if env is None else env,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def docker_calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def test_main_push_is_the_only_remote_deployment_rule(self) -> None:
        pipeline = (REPOSITORY / ".gitlab-ci.yml").read_text(encoding="utf-8")
        self.assertIn('$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_BRANCH == "main"', pipeline)
        self.assertIn("resource_group: sinbad2-production", pipeline)
        self.assertIn("interruptible: false", pipeline)
        self.assertNotIn("bootstrap", pipeline)

    def test_compose_older_than_override_tags_is_rejected(self) -> None:
        env = self.environment()
        env["RJA_TEST_COMPOSE_VERSION"] = "2.24.3"

        result = self.invoke(env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker Compose 2.24.4 or newer", result.stderr)
        self.assertFalse(any("up" in call for call in self.docker_calls()))

    def test_simulated_pipeline_deploys_only_application_services(self) -> None:
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("persistent data volumes were preserved", result.stdout)
        self.assertNotIn("synthetic-test-credential", result.stdout + result.stderr)
        self.assertNotIn("postgresql://", result.stdout + result.stderr)
        last_good = self.remote_home / "deploy_real-jaen-analytics/shared/deploy-state/last-good"
        metadata = last_good.read_text(encoding="utf-8")
        self.assertIn("database_family=postgresql\n", metadata)
        self.assertIn("sports_schema_revision=pg0005\n", metadata)
        self.assertIn("video_schema_revision=vg0002\n", metadata)

        calls = self.docker_calls()
        ups = [call for call in calls if call[:1] == ["compose"] and "up" in call]
        self.assertEqual(len(ups), 2)
        self.assertTrue(any({"acquisition", "video-service"}.issubset(set(call)) for call in ups))
        self.assertTrue(any({"dashboard", "sports-data"}.issubset(set(call)) for call in ups))
        self.assertTrue(all("postgresql" not in call for call in ups))
        builds = [call for call in calls if call[:1] == ["compose"] and "build" in call]
        self.assertEqual(len(builds), 1)
        self.assertNotIn("postgresql", builds[0])
        self.assertFalse(any(call[:2] == ["volume", "create"] for call in calls))
        self.assertFalse(any("down" in call for call in calls))

    def test_stale_pipeline_is_skipped_before_docker_mutation(self) -> None:
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.log.write_text("", encoding="utf-8")
        env = self.environment()
        env["CI_PIPELINE_ID"] = "100"
        env["CI_JOB_ID"] = "499"

        result = self.invoke(env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Skipping stale pipeline 100", result.stdout)
        self.assertEqual(self.docker_calls(), [])

    def test_release_cleanup_preserves_a_container_bind_mount(self) -> None:
        releases = self.remote_home / "deploy_real-jaen-analytics/releases"
        old_releases = [releases / f"{'b' * 40}-{pipeline}-1" for pipeline in range(1, 7)]
        for release in old_releases:
            release.mkdir(parents=True)
            for name in ("compose.prod.yaml", "compose.postgresql.yaml", "compose.postgresql.prod.yaml"):
                self.write(release / name, "# PostgreSQL release\n")
        env = self.environment()
        env["RJA_TEST_CONTAINER_BIND_MOUNT"] = str(old_releases[0])

        result = self.invoke(env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(old_releases[0].is_dir(), "release with a mounted PostgreSQL init script was removed")
        self.assertFalse(old_releases[1].exists(), "ordinary obsolete code release was not pruned")

    def test_missing_prepared_volume_fails_before_build_or_compose_up(self) -> None:
        env = self.environment()
        env["RJA_TEST_VOLUMES"] = " ".join(sorted(VOLUMES - {"rja-sinbad2-video-operational-state"}))
        result = self.invoke(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PostgreSQL installation is incomplete", result.stderr)
        calls = self.docker_calls()
        self.assertFalse(any("build" in call for call in calls))
        self.assertFalse(any("up" in call for call in calls))
        self.assertFalse(any(call[:2] == ["volume", "create"] for call in calls))

    def test_existing_sqlite_installation_is_refused_without_stopping_it(self) -> None:
        env = self.environment()
        env["RJA_TEST_SPORTS_STORAGE"] = "sql"

        result = self.invoke(env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("legacy SQLite application", result.stderr)
        calls = self.docker_calls()
        self.assertFalse(any("stop" in call for call in calls))
        self.assertFalse(any("up" in call for call in calls))

    def test_schema_check_failure_stops_before_admission_or_service_changes(self) -> None:
        env = self.environment()
        env["RJA_TEST_FAIL_SCHEMA"] = "1"
        result = self.invoke(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pg0005 schema preflight failed", result.stderr)
        calls = self.docker_calls()
        self.assertFalse(any("stop" in call for call in calls))
        self.assertFalse(any("up" in call for call in calls))

    def test_active_sports_job_finishes_before_any_service_is_replaced(self) -> None:
        env = self.environment()
        env["RJA_TEST_ACTIVE_SPORTS_POLLS"] = "1"
        env["RJA_TEST_ACTIVE_VIDEO_POLLS"] = "1"
        env["RJA_TEST_ACTIVE_ACQUISITION_POLLS"] = "1"
        result = self.invoke(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.docker_calls()
        stop_index = next(i for i, call in enumerate(calls) if call[:1] == ["compose"] and "stop" in call)
        first_update = next(i for i, call in enumerate(calls) if call[:1] == ["compose"] and "up" in call)
        active_reads = [i for i, call in enumerate(calls) if "sports-active" in call]
        video_reads = [i for i, call in enumerate(calls) if "video-active" in call]
        acquisition_reads = [i for i, call in enumerate(calls)
                             if "SELECT COUNT(*) FROM job" in " ".join(call)]
        self.assertGreaterEqual(len(active_reads), 6)
        self.assertGreaterEqual(len(video_reads), 6)
        self.assertGreaterEqual(len(acquisition_reads), 6)
        self.assertLess(stop_index, active_reads[0])
        self.assertLess(stop_index, video_reads[0])
        self.assertLess(stop_index, acquisition_reads[0])
        self.assertLess(active_reads[-1], first_update)
        self.assertLess(video_reads[-1], first_update)
        self.assertLess(acquisition_reads[-1], first_update)

    def test_incompatible_previous_release_is_not_used_for_rollback(self) -> None:
        root = self.remote_home / "deploy_real-jaen-analytics"
        releases = root / "releases"
        config_versions = root / "shared/config/versions"
        secret_versions = root / "shared/secrets/versions"
        old_release = releases / "old-sqlite-release"
        old_config = config_versions / "old-sqlite-config"
        old_secrets = secret_versions / "old-secrets"
        old_release.mkdir(parents=True)
        old_config.mkdir(parents=True)
        old_secrets.mkdir(parents=True)
        self.write(old_release / "compose.prod.yaml", "# SQLite release\n")
        self.write(old_config / ".env.prod", (self.file_vars / "ENV_PROD").read_text())
        for name, source in (
            ("access-password", "APP_ACCESS_PASSWORD"),
            ("update-password", "APP_UPDATE_PASSWORD"),
            ("control-token", "APP_CONTROL_TOKEN"),
            ("acquisition-token", "APP_ACQUISITION_TOKEN"),
        ):
            shutil.copyfile(self.file_vars / source, old_secrets / name)
            (old_secrets / name).chmod(0o444)
        session = old_secrets / "session"
        session.mkdir()
        shutil.copyfile(self.file_vars / "APP_SESSION_SECRET", session / "session-secret")
        (session / "session-secret").chmod(0o444)
        session.chmod(0o555)
        (old_secrets).chmod(0o700)
        (root / "shared/config/current").symlink_to("versions/old-sqlite-config")
        (root / "shared/secrets/current").symlink_to("versions/old-secrets")
        last_good = root / "shared/deploy-state/last-good"
        last_good.parent.mkdir(parents=True)
        last_good.write_text(
            "pipeline_id=99\nrelease_id=old-sqlite-release\n"
            f"commit_sha={'b' * 40}\nconfig_target=versions/old-sqlite-config\n"
            "secret_target=versions/old-secrets\n",
            encoding="utf-8",
        )
        last_good.chmod(0o600)
        env = self.environment()
        env["RJA_TEST_FAIL_UP_AT"] = "1"

        result = self.invoke(env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no PostgreSQL-compatible previous release", result.stderr)
        calls = self.docker_calls()
        ups = [call for call in calls if call[:1] == ["compose"] and "up" in call]
        self.assertEqual(len(ups), 1)
        self.assertFalse(any("old-sqlite-release" in call and "up" in call for call in calls))
        self.assertTrue(json.loads(self.state.read_text(encoding="utf-8"))["dashboard_stopped"])

    def test_failed_deploy_rolls_back_to_matching_postgresql_release(self) -> None:
        root = self.remote_home / "deploy_real-jaen-analytics"
        release_id = f"{'b' * 40}-99-199"
        old_release = root / "releases" / release_id
        old_config = root / "shared/config/versions/old-postgresql-config"
        old_secrets = root / "shared/secrets/versions/old-secrets"
        old_release.mkdir(parents=True)
        old_config.mkdir(parents=True)
        old_secrets.mkdir(parents=True)
        for name in ("compose.prod.yaml", "compose.postgresql.yaml", "compose.postgresql.prod.yaml"):
            self.write(old_release / name, "# PostgreSQL-compatible release\n")
        self.write(old_config / ".env.prod", (self.file_vars / "ENV_PROD").read_text())
        self.write(old_config / ".env.postgresql.prod", (self.file_vars / "ENV_POSTGRESQL_PROD").read_text())
        for name, source in (
            ("access-password", "APP_ACCESS_PASSWORD"),
            ("update-password", "APP_UPDATE_PASSWORD"),
            ("control-token", "APP_CONTROL_TOKEN"),
            ("acquisition-token", "APP_ACQUISITION_TOKEN"),
        ):
            shutil.copyfile(self.file_vars / source, old_secrets / name)
            (old_secrets / name).chmod(0o444)
        session = old_secrets / "session"
        session.mkdir()
        shutil.copyfile(self.file_vars / "APP_SESSION_SECRET", session / "session-secret")
        (session / "session-secret").chmod(0o444)
        session.chmod(0o555)
        old_secrets.chmod(0o700)
        (root / "shared/config/current").symlink_to("versions/old-postgresql-config")
        (root / "shared/secrets/current").symlink_to("versions/old-secrets")
        last_good = root / "shared/deploy-state/last-good"
        last_good.parent.mkdir(parents=True)
        last_good.write_text(
            f"pipeline_id=99\nrelease_id={release_id}\ncommit_sha={'b' * 40}\n"
            "database_family=postgresql\nsports_schema_revision=pg0005\n"
            "video_schema_revision=vg0002\nconfig_target=versions/old-postgresql-config\n"
            "secret_target=versions/old-secrets\n",
            encoding="utf-8",
        )
        last_good.chmod(0o600)
        env = self.environment()
        env["RJA_TEST_FAIL_UP_AT"] = "1"

        result = self.invoke(env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("previous PostgreSQL-compatible code/config was restarted", result.stderr)
        calls = self.docker_calls()
        ups = [call for call in calls if call[:1] == ["compose"] and "up" in call]
        self.assertEqual(len(ups), 3)
        self.assertIn("old-postgresql-config", " ".join(ups[1]))
        self.assertIn("old-postgresql-config", " ".join(ups[2]))
        self.assertFalse(json.loads(self.state.read_text(encoding="utf-8"))["dashboard_stopped"])
        self.assertIn(f"commit_sha={'b' * 40}\n", last_good.read_text(encoding="utf-8"))

    def test_rollback_requires_matching_postgresql_schema_metadata(self) -> None:
        remote_script = (REPOSITORY / "scripts/deploy-sinbad2-remote.sh").read_text(encoding="utf-8")
        for marker in (
            "database_family=postgresql",
            "sports_schema_revision=pg0005",
            "video_schema_revision=vg0002",
            "previous_rollback_compatible",
        ):
            self.assertIn(marker, remote_script)
        self.assertNotIn("persistencia verificar --base /state/real-jaen.sqlite", remote_script)
        self.assertNotIn("/state/video-service.sqlite", remote_script)


if __name__ == "__main__":
    unittest.main()
