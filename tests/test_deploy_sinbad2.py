"""Local, isolated deployment tests. SSH and Docker are replaced by test shims."""

from __future__ import annotations

import hashlib
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
SECRETS = {
    "APP_ACCESS_PASSWORD": secrets.token_urlsafe(18),
    "APP_UPDATE_PASSWORD": secrets.token_urlsafe(18),
    "APP_SESSION_SECRET": secrets.token_urlsafe(32),
    "APP_CONTROL_TOKEN": secrets.token_urlsafe(32),
    "APP_ACQUISITION_TOKEN": secrets.token_urlsafe(32),
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
case "$target" in *@*) ;; *) exit 92 ;; esac
export HOME="$RJA_TEST_REMOTE_HOME"
if [ "$1" = sh ]; then
  shift
  exec /bin/sh "$@"
fi
exec /bin/sh -c "$1"
"""

FAKE_DOCKER = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
log = Path(os.environ["RJA_TEST_DOCKER_LOG"])
with log.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\n")
state_path = Path(os.environ["RJA_TEST_DOCKER_STATE"])
if state_path.exists():
    state = json.loads(state_path.read_text(encoding="utf-8"))
else:
    state = {"volumes": os.environ.get("RJA_TEST_VOLUMES", "").split(), "up_count": 0}

def save():
    state_path.write_text(json.dumps(state), encoding="utf-8")

if args[:3] == ["compose", "version", "--short"]:
    print("5.6.1")
elif args[:2] == ["volume", "inspect"]:
    raise SystemExit(0 if args[2] in state["volumes"] else 1)
elif args[:2] == ["volume", "create"]:
    name = args[-1]
    if name not in state["volumes"]:
        state["volumes"].append(name)
    save()
    print(name)
elif args and args[0] == "compose":
    subcommands = {"config", "build", "up", "ps", "run"}
    index = next((i for i, arg in enumerate(args[1:], 1) if arg in subcommands), None)
    command = args[index] if index is not None else ""
    if command == "build":
        raise SystemExit(1 if os.environ.get("RJA_TEST_FAIL_BUILD") == "1" else 0)
    elif command == "up":
        state["up_count"] += 1
        save()
        fail_at = int(os.environ.get("RJA_TEST_FAIL_UP_AT", "0"))
        raise SystemExit(1 if state["up_count"] == fail_at else 0)
    elif command == "ps":
        print("container-" + args[-1])
    elif command == "run":
        raise SystemExit(1 if os.environ.get("RJA_TEST_FAIL_RESTORE") == "1" else 0)
    elif command == "config":
        pass
    else:
        print("unexpected fake compose invocation", file=sys.stderr)
        raise SystemExit(93)
elif args[:2] == ["inspect", "--format"]:
    template = args[2]
    print("healthy" if ".State.Health.Status" in template else "running")
elif args and args[0] in ("info", "run", "image"):
    pass
else:
    print("unexpected fake docker invocation", file=sys.stderr)
    raise SystemExit(93)
save()
"""


class DeploySinbad2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="rja-deploy-test-")
        self.base = Path(self.temp.name)
        self.bin_dir = self.base / "bin"
        self.bin_dir.mkdir()
        self.remote_home = self.base / "remote-home"
        self.remote_home.mkdir()
        self.files = self.base / "file-vars"
        self.files.mkdir()
        self.logs = self.base / "docker.jsonl"
        self.state = self.base / "docker-state.json"
        self.write_file(self.bin_dir / "git", FAKE_GIT, 0o755)
        self.write_file(self.bin_dir / "ssh", FAKE_SSH, 0o755)
        self.write_file(self.bin_dir / "docker", FAKE_DOCKER, 0o755)
        self.make_file_variables()
        self.key = self.files / "deploy_key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)],
            check=True,
        )
        public_key = self.key.with_suffix(".pub").read_text(encoding="utf-8").split()
        self.write_file(self.files / "known_hosts", f"sinbad2.test {public_key[0]} {public_key[1]}\n")

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def write_file(path: Path, content: str, mode: int = 0o600) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(mode)

    def make_file_variables(self) -> None:
        env_text = (REPOSITORY / ".env.prod.example").read_text(encoding="utf-8")
        for name, value in {"ENV_PROD": env_text, **SECRETS}.items():
            self.write_file(self.files / name, value + ("\n" if not value.endswith("\n") else ""))

    def environment(self, pipeline_id: int = 101, job_id: int = 501) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bin_dir}{os.pathsep}{env['PATH']}",
                "TMPDIR": str(self.base),
                "RJA_TEST_SOURCE": str(REPOSITORY),
                "RJA_REAL_GIT": shutil.which("git") or "/usr/bin/git",
                "RJA_TEST_REMOTE_HOME": str(self.remote_home),
                "RJA_TEST_DOCKER_LOG": str(self.logs),
                "RJA_TEST_DOCKER_STATE": str(self.state),
                "RJA_TEST_VOLUMES": "rja-sinbad2-sports-state rja-sinbad2-acquisition-profiles rja-sinbad2-acquisition-jobs rja-sinbad2-video-state",
                "SSH_HOST": "sinbad2.test",
                "REMOTE_USER": "deploy",
                "CI_COMMIT_SHA": COMMIT,
                "CI_PIPELINE_ID": str(pipeline_id),
                "CI_JOB_ID": str(job_id),
            }
        )
        for name in ("ENV_PROD", "SSH_PRIVATE_KEY", "SSH_KNOWN_HOSTS", *SECRETS):
            if name == "SSH_PRIVATE_KEY":
                path = self.key
            elif name == "SSH_KNOWN_HOSTS":
                path = self.files / "known_hosts"
            else:
                path = self.files / name
            env[name] = str(path)
        return env

    def invoke(
        self,
        mode: str = "deploy",
        env: dict[str, str] | None = None,
        pipeline_id: int = 101,
        job_id: int = 501,
    ) -> subprocess.CompletedProcess[str]:
        result_env = self.environment(pipeline_id=pipeline_id, job_id=job_id) if env is None else env
        return subprocess.run(
            ["bash", str(REPOSITORY / "scripts/deploy-sinbad2.sh"), mode],
            cwd=REPOSITORY,
            env=result_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def docker_calls(self) -> list[list[str]]:
        if not self.logs.exists():
            return []
        return [json.loads(line) for line in self.logs.read_text(encoding="utf-8").splitlines()]

    @staticmethod
    def compose_call(call: list[str], subcommand: str) -> bool:
        if not call or call[0] != "compose":
            return False
        return subcommand in call[1:]

    def release_contains(self, release: Path, needle: str) -> bool:
        target = needle.encode("utf-8")
        for path in release.rglob("*"):
            if not path.is_file():
                continue
            carry = b""
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    data = carry + chunk
                    if target in data:
                        return True
                    carry = data[-(len(target) - 1) :] if len(target) > 1 else b""
        return False

    def last_good(self) -> str:
        return (self.remote_home / "deploy_real-jaen-analytics/shared/deploy-state/last-good").read_text(
            encoding="utf-8"
        )

    def add_verified_backup(self) -> Path:
        backup = self.remote_home / "deploy_real-jaen-analytics/shared/imports/initial-backup"
        backup.mkdir(parents=True)
        names = (
            "real-jaen.sqlite",
            "actualizaciones.tar",
            "acquisition-jobs.tar",
            "video-service-state.tar",
            "manifest.json",
        )
        for name in names:
            (backup / name).write_text(f"mock backup payload: {name}\n", encoding="utf-8")
        entries = []
        for name in names:
            digest = hashlib.sha256((backup / name).read_bytes()).hexdigest()
            entries.append(f"{digest}  {name}")
        (backup / "SHA256SUMS").write_text("\n".join(entries) + "\n", encoding="utf-8")
        return backup

    def test_file_variables_deploy_and_secret_permissions(self) -> None:
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        for secret in SECRETS.values():
            self.assertNotIn(secret, result.stdout + result.stderr)

        root = self.remote_home / "deploy_real-jaen-analytics"
        good = (root / "shared/deploy-state/last-good").read_text(encoding="utf-8")
        self.assertIn(f"commit_sha={COMMIT}\n", good)
        self.assertTrue((root / "shared/secrets/current/access-password").is_file())
        self.assertEqual(
            (root / "shared/secrets/current/access-password").read_text(encoding="utf-8").strip(),
            SECRETS["APP_ACCESS_PASSWORD"],
        )
        self.assertEqual((root / "shared/secrets/current/access-password").stat().st_mode & 0o777, 0o444)
        self.assertEqual((root / "shared/secrets/current/session").stat().st_mode & 0o777, 0o555)
        self.assertEqual((root / "shared/config/current/.env.prod").stat().st_mode & 0o777, 0o600)
        release = next((root / "releases").iterdir())
        for secret in SECRETS.values():
            self.assertFalse(self.release_contains(release, secret), "private value found in archived release")

        calls = self.docker_calls()
        dashboard_check = next(call for call in calls if "--user" in call and "node" in call)
        mounted = {
            argument.split(":/run/secrets/", 1)[1].split(":", 1)[0]
            for argument in dashboard_check
            if ":/run/secrets/" in argument
        }
        self.assertEqual(mounted, {"access-password", "update-password", "control-token", "session"})
        self.assertTrue(any(call[:2] == ["image", "tag"] and call[-1].endswith(":prod") for call in calls))
        logged_arguments = self.logs.read_text(encoding="utf-8")
        for secret in SECRETS.values():
            self.assertNotIn(secret, logged_arguments, "private value appeared in a Docker command")

    def test_missing_file_variable_fails_before_ssh(self) -> None:
        env = self.environment()
        secret = SECRETS["APP_CONTROL_TOKEN"]
        env.pop("APP_CONTROL_TOKEN")
        result = self.invoke(env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("APP_CONTROL_TOKEN must be a non-empty GitLab File variable", result.stderr)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertFalse(self.logs.exists())

    def test_pipeline_rules_are_scoped_to_main_push_and_manual_bootstrap(self) -> None:
        config = (REPOSITORY / ".gitlab-ci.yml").read_text(encoding="utf-8")
        self.assertIn('$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_BRANCH == "main"', config)
        self.assertIn('$CI_PIPELINE_SOURCE == "web" && $CI_COMMIT_BRANCH == "main"', config)
        self.assertIn("resource_group: sinbad2-production", config)
        self.assertIn("name: production", config)
        self.assertIn("when: manual", config)
        self.assertIn("when: on_success", config)
        self.assertNotIn("tags:", config)
        self.assertNotIn("master", config)

    def test_missing_volumes_fail_without_build_or_creation(self) -> None:
        env = self.environment()
        env["RJA_TEST_VOLUMES"] = ""
        result = self.invoke(env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("installation is not initialized", result.stderr)
        calls = self.docker_calls()
        self.assertFalse(any(call[:2] == ["volume", "create"] for call in calls))
        self.assertFalse(any(self.compose_call(call, "build") for call in calls))
        self.assertFalse(any(self.compose_call(call, "up") for call in calls))

    def test_bootstrap_without_backup_fails_before_build_or_volume_creation(self) -> None:
        env = self.environment()
        env["RJA_TEST_VOLUMES"] = ""
        result = self.invoke("bootstrap", env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bootstrap backup is missing", result.stderr)
        calls = self.docker_calls()
        self.assertFalse(any(call[:2] == ["volume", "create"] for call in calls))
        self.assertFalse(any(self.compose_call(call, "build") for call in calls))

    def test_bootstrap_refuses_existing_volume_before_build(self) -> None:
        env = self.environment()
        self.add_verified_backup()
        result = self.invoke("bootstrap", env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bootstrap refused", result.stderr)
        calls = self.docker_calls()
        self.assertFalse(any(call[:2] == ["volume", "create"] for call in calls))
        self.assertFalse(any(self.compose_call(call, "build") for call in calls))

    def test_partial_installation_fails_without_creating_other_volumes(self) -> None:
        env = self.environment()
        env["RJA_TEST_VOLUMES"] = "rja-sinbad2-sports-state"
        result = self.invoke(env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only some persistent volumes exist", result.stderr)
        calls = self.docker_calls()
        self.assertFalse(any(call[:2] == ["volume", "create"] for call in calls))
        self.assertFalse(any(self.compose_call(call, "build") for call in calls))

    def test_restore_failure_keeps_created_volumes_for_inspection(self) -> None:
        env = self.environment()
        env["RJA_TEST_VOLUMES"] = ""
        env["RJA_TEST_FAIL_RESTORE"] = "1"
        self.add_verified_backup()
        result = self.invoke("bootstrap", env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bootstrap restore failed", result.stderr)
        calls = self.docker_calls()
        self.assertEqual(len([call for call in calls if call[:2] == ["volume", "create"]]), 4)
        self.assertFalse(any(call[:2] == ["volume", "rm"] for call in calls))
        self.assertFalse(any("down" in call for call in calls))

    def test_bootstrap_with_verified_backup_creates_only_named_volumes(self) -> None:
        env = self.environment()
        env["RJA_TEST_VOLUMES"] = ""
        self.add_verified_backup()
        result = self.invoke("bootstrap", env)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        calls = self.docker_calls()
        created = [call[-1] for call in calls if call[:2] == ["volume", "create"]]
        self.assertEqual(
            created,
            [
                "rja-sinbad2-sports-state",
                "rja-sinbad2-acquisition-jobs",
                "rja-sinbad2-acquisition-profiles",
                "rja-sinbad2-video-state",
            ],
        )
        self.assertFalse(any("down" in call for call in calls))
        self.assertFalse(any(call[:2] == ["volume", "rm"] for call in calls))
        self.assertTrue((self.remote_home / "deploy_real-jaen-analytics/shared/deploy-state/last-good").is_file())

    def test_build_failure_keeps_previous_healthy_release(self) -> None:
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        marker = self.last_good()
        before = len([call for call in self.docker_calls() if self.compose_call(call, "up")])
        env = self.environment(pipeline_id=102, job_id=502)
        env["RJA_TEST_FAIL_BUILD"] = "1"
        second = self.invoke(env=env)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("image build failed", second.stderr)
        self.assertEqual(self.last_good(), marker)
        after = len([call for call in self.docker_calls() if self.compose_call(call, "up")])
        self.assertEqual(after, before)
        for secret in SECRETS.values():
            self.assertNotIn(secret, second.stdout + second.stderr)

    def test_up_failure_restarts_previous_release_without_volume_removal(self) -> None:
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        marker = self.last_good()
        env = self.environment(pipeline_id=102, job_id=502)
        env["RJA_TEST_FAIL_UP_AT"] = "2"
        second = self.invoke(env=env)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("previous code/config was restarted", second.stderr)
        self.assertIn("Data compatibility depends on schema changes", second.stderr)
        self.assertEqual(self.last_good(), marker)
        calls = self.docker_calls()
        self.assertEqual(len([call for call in calls if self.compose_call(call, "up")]), 3)
        self.assertFalse(any(call[:2] == ["volume", "create"] for call in calls))
        self.assertFalse(any(call[:2] == ["volume", "rm"] for call in calls))
        self.assertFalse(any("down" in call for call in calls))
        for secret in SECRETS.values():
            self.assertNotIn(secret, second.stdout + second.stderr)

    def test_older_pipeline_is_skipped(self) -> None:
        first = self.invoke(pipeline_id=120)
        self.assertEqual(first.returncode, 0, first.stderr)
        marker = self.last_good()
        calls_before = len(self.docker_calls())
        old = self.invoke(env=self.environment(pipeline_id=119, job_id=599))
        self.assertEqual(old.returncode, 0, old.stderr)
        self.assertIn("Skipping stale pipeline 119", old.stdout)
        self.assertEqual(self.last_good(), marker)
        calls_after = self.docker_calls()[calls_before:]
        self.assertFalse(any(self.compose_call(call, "build") for call in calls_after))
        self.assertFalse(any(self.compose_call(call, "up") for call in calls_after))


if __name__ == "__main__":
    unittest.main()
