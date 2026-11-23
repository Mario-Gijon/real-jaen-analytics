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
if [ -n "${RJA_TEST_SSH_LOG:-}" ]; then
  for argument do printf '%s\n' "$argument" >> "$RJA_TEST_SSH_LOG"; done
fi
ssh_key=
known_hosts=
while [ "$#" -gt 0 ]; do
  case "$1" in
    -i) ssh_key=$2; shift 2 ;;
    -o)
      case "$2" in UserKnownHostsFile=*) known_hosts=${2#*=} ;; esac
      shift 2
      ;;
    *) break ;;
  esac
done
[ "$#" -ge 2 ] || exit 91
target=$1
shift
case "$target" in *@*) ;; *) exit 92 ;; esac
python3 - "$ssh_key" "$known_hosts" "$RJA_TEST_SSH_MATERIAL_LOG" <<'PY'
import json
import os
import sys
from pathlib import Path
key, hosts, log = map(Path, sys.argv[1:])
with log.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({"key_mode": key.stat().st_mode & 0o777,
                             "known_hosts_mode": hosts.stat().st_mode & 0o777,
                             "private_key_env_present": "SSH_PRIVATE_KEY" in os.environ}) + "\n")
PY
export HOME="$RJA_TEST_REMOTE_HOME"
if [ "$1" = sh ]; then
  shift
  exec /bin/sh "$@"
fi
exec /bin/sh -c "$1"
"""

FAKE_SH = r"""#!/bin/sh
set -eu
case " $* " in
  *"dashboard/scripts/restaurar-instalacion.sh"*)
    python3 - <<'PY'
import json
import os
from pathlib import Path
state = json.loads(Path(os.environ["RJA_TEST_DOCKER_STATE"]).read_text(encoding="utf-8"))
Path(os.environ["RJA_TEST_RESTORE_PRESTATE"]).write_text(
    json.dumps({"volumes": state["volumes"]}), encoding="utf-8"
)
PY
    ;;
esac
exec "$RJA_REAL_SH" "$@"
"""

FAKE_MV = r"""#!/bin/sh
set -eu
target=
for argument do target=$argument; done
case "${RJA_TEST_FAIL_LAST_GOOD:-0}:$target" in
  1:*/shared/deploy-state/last-good) exit 1 ;;
esac
exec "$RJA_REAL_MV" "$@"
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
    state = {"volumes": os.environ.get("RJA_TEST_VOLUMES", "").split(), "up_count": 0,
             "deployed": False, "network_detached": False}

state.setdefault("implicit_volume_creations", [])

def save():
    state_path.write_text(json.dumps(state), encoding="utf-8")

def add_implicit_volume(name, trigger):
    if name and name not in state["volumes"]:
        state["volumes"].append(name)
        state["implicit_volume_creations"].append({"name": name, "trigger": trigger})
        save()

def implicit_volumes_for_compose(command, command_args):
    managed = os.environ.get("RJA_TEST_MANAGED_VOLUMES", "").split()
    if command == "up":
        for name in managed:
            add_implicit_volume(name, "compose up")
    elif command == "run":
        services = {"sports-data", "acquisition", "video-service", "dashboard"}
        service = next((arg for arg in command_args if arg in services), None)
        if service and len(managed) == 4:
            indexes = {"sports-data": (0,), "acquisition": (1, 2),
                       "video-service": (3,), "dashboard": ()}[service]
            for index in indexes:
                add_implicit_volume(managed[index], "compose run " + service)

def uid_10001_can_access(path, *, read=False, execute=False):
    # Evaluate actual host mode bits using the application's numeric UID/GID.
    try:
        info = Path(path).stat()
    except OSError:
        return False
    if info.st_uid == 10001:
        bits = (info.st_mode >> 6) & 7
    elif info.st_gid == 10001:
        bits = (info.st_mode >> 3) & 7
    else:
        bits = info.st_mode & 7
    return (not read or bool(bits & 4)) and (not execute or bool(bits & 1))

def check_host_mounts_for_uid_10001():
    mounts = []
    for index, argument in enumerate(args[:-1]):
        if argument == "-v":
            source, target, *_ = args[index + 1].split(":", 2)
            if source.startswith("/"):
                mounts.append((Path(source), target))
    command_text = " ".join(args)
    for source, target in mounts:
        if target == "/imports":
            if not uid_10001_can_access(source, read=True, execute=True):
                return False
            if "initial-backup" in command_text:
                backup = source / "initial-backup"
                if not uid_10001_can_access(backup, read=True, execute=True):
                    return False
                names = ["real-jaen.sqlite", "actualizaciones.tar", "acquisition-jobs.tar",
                         "video-service-state.tar", "manifest.json", "SHA256SUMS"]
                if (backup / "procedencia.tar").is_file():
                    names.append("procedencia.tar")
                if any(not uid_10001_can_access(backup / name, read=True) for name in names):
                    return False
        elif target == "/app/data":
            if not uid_10001_can_access(source, read=True, execute=True):
                return False
            if "VideoCatalog.load" in command_text:
                manifest = source / "videos.json"
                if not uid_10001_can_access(manifest, read=True):
                    return False
                try:
                    json.loads(manifest.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    return False
    return True

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
    command_args = args[index + 1:] if index is not None else []
    if command in ("up", "run"):
        implicit_volumes_for_compose(command, command_args)
    if command == "build":
        raise SystemExit(1 if os.environ.get("RJA_TEST_FAIL_BUILD") == "1" else 0)
    elif command == "up":
        state["up_count"] += 1
        fail_at = int(os.environ.get("RJA_TEST_FAIL_UP_AT", "0"))
        if state["up_count"] == fail_at:
            save()
            raise SystemExit(1)
        state["deployed"] = True
        if "sports-data" in args:
            state["network_detached"] = False
        save()
    elif command == "ps":
        if state["deployed"]:
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
    if ".NetworkSettings.Networks" in template:
        if state["network_detached"]:
            print("{}")
        else:
            print('{"rja-sinbad2-prod-acquisition-control":{}}')
    else:
        print("healthy" if ".State.Health.Status" in template else "running")
elif args[:2] == ["network", "disconnect"]:
    state["network_detached"] = True
    save()
elif args[:2] == ["network", "connect"]:
    state["network_detached"] = False
    save()
elif args and args[0] in ("info", "run", "image"):
    if args[0] == "run":
        for index, argument in enumerate(args[:-1]):
            if argument == "-v":
                source = args[index + 1].split(":", 1)[0]
                if source and not source.startswith("/") and source not in (".", "..") \
                        and not source.startswith(("./", "../")):
                    add_implicit_volume(source, "docker run -v")
        if not check_host_mounts_for_uid_10001():
            raise SystemExit(1)
        if os.environ.get("RJA_TEST_FAIL_CATALOG") == "1" and any("VideoCatalog.load" in x for x in args):
            raise SystemExit(1)
        if any("SELECT COUNT(*) FROM job" in x for x in args):
            print(os.environ.get("RJA_TEST_ACTIVE_JOBS", "0"))
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
        self.ssh_log = self.base / "ssh-args.log"
        self.ssh_material_log = self.base / "ssh-material.jsonl"
        self.state = self.base / "docker-state.json"
        self.write_file(self.bin_dir / "git", FAKE_GIT, 0o755)
        self.write_file(self.bin_dir / "ssh", FAKE_SSH, 0o755)
        self.write_file(self.bin_dir / "sh", FAKE_SH, 0o755)
        self.write_file(self.bin_dir / "docker", FAKE_DOCKER, 0o755)
        self.write_file(self.bin_dir / "mv", FAKE_MV, 0o755)
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
                "RJA_REAL_MV": shutil.which("mv") or "/bin/mv",
                "RJA_TEST_REMOTE_HOME": str(self.remote_home),
                "RJA_TEST_DOCKER_LOG": str(self.logs),
                "RJA_TEST_DOCKER_STATE": str(self.state),
                "RJA_TEST_SSH_LOG": str(self.ssh_log),
                "RJA_TEST_SSH_MATERIAL_LOG": str(self.ssh_material_log),
                "RJA_TEST_RESTORE_PRESTATE": str(self.base / "restore-prestate.json"),
                "RJA_TEST_VOLUMES": "rja-sinbad2-sports-state rja-sinbad2-acquisition-profiles rja-sinbad2-acquisition-jobs rja-sinbad2-video-state",
                "RJA_TEST_MANAGED_VOLUMES": "rja-sinbad2-sports-state rja-sinbad2-acquisition-profiles rja-sinbad2-acquisition-jobs rja-sinbad2-video-state",
                "RJA_REAL_SH": shutil.which("sh") or "/bin/sh",
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

    def assert_ssh_material_is_private(self, result: subprocess.CompletedProcess[str], key_text: str) -> None:
        logged = result.stdout + result.stderr
        if self.ssh_log.exists():
            logged += self.ssh_log.read_text(encoding="utf-8")
        if self.logs.exists():
            logged += self.logs.read_text(encoding="utf-8")
        self.assertNotIn(key_text.strip(), logged)
        material = [json.loads(line) for line in self.ssh_material_log.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(material)
        self.assertTrue(all(item["key_mode"] == 0o600 for item in material))
        self.assertTrue(all(item["known_hosts_mode"] == 0o600 for item in material))
        self.assertTrue(all(not item["private_key_env_present"] for item in material))

    def test_fake_docker_creates_implicit_named_volumes(self) -> None:
        env = self.environment()
        env["RJA_TEST_VOLUMES"] = ""
        direct = subprocess.run(
            [str(self.bin_dir / "docker"), "run", "--rm", "-v", "implicit-run-volume:/state", "alpine", "true"],
            env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(direct.returncode, 0, direct.stderr)
        compose_run = subprocess.run(
            [str(self.bin_dir / "docker"), "compose", "run", "--rm", "sports-data", "true"],
            env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(compose_run.returncode, 0, compose_run.stderr)
        compose_up = subprocess.run(
            [str(self.bin_dir / "docker"), "compose", "up", "-d"],
            env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(compose_up.returncode, 0, compose_up.stderr)

        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertIn("implicit-run-volume", state["volumes"])
        self.assertCountEqual(state["volumes"], env["RJA_TEST_MANAGED_VOLUMES"].split() + ["implicit-run-volume"])
        self.assertTrue(any(item["trigger"] == "docker run -v" for item in state["implicit_volume_creations"]))
        self.assertTrue(any(item["trigger"].startswith("compose ") for item in state["implicit_volume_creations"]))

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
        self.assert_ssh_material_is_private(result, self.key.read_text(encoding="utf-8"))
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

    def test_ssh_private_key_variable_text_is_supported_without_logging_it(self) -> None:
        env = self.environment()
        private_key = self.key.read_text(encoding="utf-8")
        env["SSH_PRIVATE_KEY"] = private_key

        result = self.invoke(env=env)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assert_ssh_material_is_private(result, private_key)

    def test_ssh_private_key_crlf_is_normalized(self) -> None:
        env = self.environment()
        private_key = self.key.read_text(encoding="utf-8").replace("\n", "\r\n")
        env["SSH_PRIVATE_KEY"] = private_key

        result = self.invoke(env=env)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assert_ssh_material_is_private(result, private_key)

    def test_missing_or_invalid_ssh_private_key_fails_before_ssh(self) -> None:
        cases = (None, str(self.base / "missing-deploy-key"), "this is not an SSH private key")
        for index, value in enumerate(cases):
            with self.subTest(value=value):
                env = self.environment(pipeline_id=101 + index, job_id=501 + index)
                if value is None:
                    env.pop("SSH_PRIVATE_KEY")
                else:
                    env["SSH_PRIVATE_KEY"] = value
                result = self.invoke(env=env)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.ssh_log.exists())
                self.assertNotIn("this is not an SSH private key", result.stdout + result.stderr)

    def test_ssh_known_hosts_variable_text_is_supported(self) -> None:
        env = self.environment()
        env["SSH_KNOWN_HOSTS"] = (self.files / "known_hosts").read_text(encoding="utf-8")

        result = self.invoke(env=env)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assert_ssh_material_is_private(result, self.key.read_text(encoding="utf-8"))
        self.assertIn("StrictHostKeyChecking=yes", self.ssh_log.read_text(encoding="utf-8"))

    def test_runner_known_hosts_file_is_used_when_variable_is_missing(self) -> None:
        env = self.environment()
        env.pop("SSH_KNOWN_HOSTS")
        runner_home = self.base / "runner-home"
        runner_known_hosts = runner_home / ".ssh/known_hosts"
        runner_known_hosts.parent.mkdir(parents=True)
        shutil.copyfile(self.files / "known_hosts", runner_known_hosts)
        env["HOME"] = str(runner_home)

        result = self.invoke(env=env)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assert_ssh_material_is_private(result, self.key.read_text(encoding="utf-8"))

    def test_absent_trusted_host_key_fails_before_ssh(self) -> None:
        env = self.environment()
        env.pop("SSH_KNOWN_HOSTS")
        env["HOME"] = str(self.base / "runner-home-without-known-hosts")

        result = self.invoke(env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No trusted SSH host key is configured", result.stderr)
        self.assertIn("verifying its fingerprint out of band", result.stderr)
        self.assertFalse(self.ssh_log.exists())

        mismatch = self.environment(pipeline_id=102, job_id=502)
        mismatch["SSH_KNOWN_HOSTS"] = (self.files / "known_hosts").read_text(encoding="utf-8").replace(
            "sinbad2.test", "other-host.test"
        )
        mismatch_result = self.invoke(env=mismatch)
        self.assertNotEqual(mismatch_result.returncode, 0)
        self.assertIn("No trusted SSH host key matches SSH_HOST", mismatch_result.stderr)
        self.assertFalse(self.ssh_log.exists())

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

    def test_new_bootstrap_directories_and_backup_are_readable_by_uid_10001(self) -> None:
        env = self.environment()
        env["RJA_TEST_VOLUMES"] = ""
        first = self.invoke("bootstrap", env)
        self.assertNotEqual(first.returncode, 0)
        self.assertIn("bootstrap backup is missing", first.stderr)

        root = self.remote_home / "deploy_real-jaen-analytics"
        imports = root / "shared/imports"
        catalog = root / "shared/video-catalog"
        for directory in (imports, catalog):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o755)
            self.assertTrue(directory.stat().st_mode & 0o005 == 0o005)

        backup = self.add_verified_backup()
        second = self.invoke("bootstrap", self.environment(pipeline_id=102, job_id=502))
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o755)
        self.assertEqual((catalog / "videos.json").stat().st_mode & 0o777, 0o444)
        for path in backup.iterdir():
            self.assertTrue(path.stat().st_mode & 0o004, f"UID 10001 cannot read {path.name}")
        self.assertTrue(any("initial-backup" in argument for call in self.docker_calls() for argument in call))

    def test_existing_shared_directories_without_uid_access_fail_without_chmod_or_up(self) -> None:
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        root = self.remote_home / "deploy_real-jaen-analytics"
        imports = root / "shared/imports"
        catalog = root / "shared/video-catalog"
        before_ups = len([call for call in self.docker_calls() if self.compose_call(call, "up")])

        for index, directory in enumerate((imports, catalog)):
            directory.chmod(0o700)
            env_file = self.files / "ENV_PROD"
            env_file.write_text(env_file.read_text(encoding="utf-8") + "# preflight retry\n", encoding="utf-8")
            failed = self.invoke(pipeline_id=102 + index, job_id=502 + index)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("not readable and traversable by UID 10001", failed.stderr)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            after_ups = len([call for call in self.docker_calls() if self.compose_call(call, "up")])
            self.assertEqual(after_ups, before_ups)
            directory.chmod(0o755)

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
        pre_restore = json.loads((self.base / "restore-prestate.json").read_text(encoding="utf-8"))
        self.assertEqual(pre_restore["volumes"], [], "persistent volumes existed immediately before restore")
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
        tar_restores = [call for call in calls if call and call[0] == "run" and "--entrypoint" in call
                        and call[call.index("--entrypoint") + 1] == "tar"]
        self.assertEqual(len(tar_restores), 2)
        self.assertTrue(any(any("acquisition-jobs.tar" in arg for arg in call) for call in tar_restores))
        self.assertTrue(any(any("video-service-state.tar" in arg for arg in call) for call in tar_restores))
        self.assertTrue(any(self.compose_call(call, "run") for call in calls))
        self.assertFalse(any("down" in call for call in calls))
        self.assertFalse(any(call[:2] == ["volume", "rm"] for call in calls))
        final_state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(final_state["implicit_volume_creations"], [])
        self.assertTrue((self.remote_home / "deploy_real-jaen-analytics/shared/deploy-state/last-good").is_file())

        later = self.invoke("deploy", self.environment(pipeline_id=102, job_id=502))
        self.assertEqual(later.returncode, 0, later.stderr + later.stdout)

    def test_build_failure_keeps_previous_healthy_release(self) -> None:
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        marker = self.last_good()
        before = len([call for call in self.docker_calls() if self.compose_call(call, "up")])
        env = self.environment(pipeline_id=102, job_id=502)
        env["CI_COMMIT_SHA"] = "b" * 40
        env["RJA_TEST_FAIL_BUILD"] = "1"
        second = self.invoke(env=env)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("image build failed", second.stderr)
        self.assertEqual(self.last_good(), marker)
        after = len([call for call in self.docker_calls() if self.compose_call(call, "up")])
        self.assertEqual(after, before)
        for secret in SECRETS.values():
            self.assertNotIn(secret, second.stdout + second.stderr)

    def test_same_sha_secret_rotation_force_recreates_consumer(self) -> None:
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        root = self.remote_home / "deploy_real-jaen-analytics"
        old_target = (root / "shared/secrets/current").resolve()
        new_password = secrets.token_urlsafe(20)
        self.write_file(self.files / "APP_ACCESS_PASSWORD", new_password + "\n")

        rotated = self.invoke(pipeline_id=102, job_id=502)
        self.assertEqual(rotated.returncode, 0, rotated.stderr + rotated.stdout)
        new_target = (root / "shared/secrets/current").resolve()
        self.assertNotEqual(new_target, old_target)
        self.assertEqual((new_target / "access-password").read_text(encoding="utf-8").strip(), new_password)
        calls = [call for call in self.docker_calls() if self.compose_call(call, "up")]
        update = calls[-1]
        self.assertIn("--force-recreate", update)
        self.assertIn("dashboard", update)
        self.assertNotIn("sports-data", update)
        self.assertNotIn("acquisition", update)

    def test_same_sha_without_changes_does_not_recreate_running_services(self) -> None:
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        calls_before = len(self.docker_calls())

        repeated = self.invoke(pipeline_id=102, job_id=502)
        self.assertEqual(repeated.returncode, 0, repeated.stderr + repeated.stdout)
        calls = self.docker_calls()[calls_before:]
        self.assertFalse(any(self.compose_call(call, "up") for call in calls))
        self.assertFalse(any(self.compose_call(call, "build") for call in calls))
        self.assertIn("pipeline_id=102\n", self.last_good())

    def test_same_sha_env_rotation_force_recreates_services(self) -> None:
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        env_file = self.files / "ENV_PROD"
        env_file.write_text(env_file.read_text(encoding="utf-8") + "RJ_TRUSTED_CLIENT_IP_HEADER=X-Forwarded-For\n",
                            encoding="utf-8")

        rotated = self.invoke(pipeline_id=102, job_id=502)
        self.assertEqual(rotated.returncode, 0, rotated.stderr + rotated.stdout)
        calls = [call for call in self.docker_calls() if self.compose_call(call, "up")]
        updates = calls[1:]
        self.assertTrue(all("--force-recreate" in update for update in updates))
        updated_services = {service for update in updates for service in update if service in {
            "dashboard", "sports-data", "acquisition", "video-service"
        }}
        for service in ("dashboard", "sports-data", "acquisition", "video-service"):
            self.assertIn(service, updated_services)

    def test_acquisition_secret_rotation_drains_without_profile_lock_collision(self) -> None:
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        calls_before = len(self.docker_calls())
        new_token = secrets.token_urlsafe(40)
        self.write_file(self.files / "APP_ACQUISITION_TOKEN", new_token + "\n")

        rotated = self.invoke(pipeline_id=102, job_id=502)
        self.assertEqual(rotated.returncode, 0, rotated.stderr + rotated.stdout)
        calls = self.docker_calls()
        ups = [call for call in calls if self.compose_call(call, "up")]
        self.assertGreaterEqual(len(ups), 3)  # Acquisition, Sports Data, then health/metadata completion.
        self.assertTrue(any(call[:2] == ["network", "disconnect"] for call in calls))
        self.assertTrue(any(call[:2] == ["network", "connect"] for call in calls) is False)
        self.assertTrue(any("deployment-maintenance" in argument for call in calls for argument in call))
        second_calls = calls[calls_before:]
        self.assertFalse(any("verify-profile" in argument for call in second_calls for argument in call))
        self.assertEqual((self.remote_home / "deploy_real-jaen-analytics/shared/secrets/current/acquisition-token")
                         .read_text(encoding="utf-8").strip(), new_token)

    def test_readonly_existing_video_catalog_permissions_are_preserved(self) -> None:
        catalog = self.remote_home / "deploy_real-jaen-analytics/shared/video-catalog"
        catalog.mkdir(parents=True)
        manifest = catalog / "videos.json"
        manifest.write_text('{"schema_version":1,"videos":[]}\n', encoding="utf-8")
        manifest.chmod(0o444)
        catalog.chmod(0o555)
        before_manifest = manifest.stat()
        before_catalog = catalog.stat()

        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        after_manifest = manifest.stat()
        after_catalog = catalog.stat()
        self.assertEqual(after_manifest.st_mode & 0o777, before_manifest.st_mode & 0o777)
        self.assertEqual((after_manifest.st_uid, after_manifest.st_gid), (before_manifest.st_uid, before_manifest.st_gid))
        self.assertEqual(after_catalog.st_mode & 0o777, before_catalog.st_mode & 0o777)
        self.assertEqual((after_catalog.st_uid, after_catalog.st_gid), (before_catalog.st_uid, before_catalog.st_gid))
        catalog.chmod(0o755)

    def test_env_prod_rejects_duplicate_critical_volume_identity(self) -> None:
        env_file = self.files / "ENV_PROD"
        env_file.write_text(
            env_file.read_text(encoding="utf-8") +
            "RJ_PROD_ACQUISITION_JOBS_VOLUME=other-acquisition-jobs\n",
            encoding="utf-8",
        )
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("declares critical key RJ_PROD_ACQUISITION_JOBS_VOLUME more than once", result.stderr)
        calls = self.docker_calls()
        self.assertFalse(any(call[:2] == ["volume", "inspect"] for call in calls))
        self.assertFalse(any(self.compose_call(call, "up") for call in calls))

    def test_env_prod_rejects_altered_project_identity(self) -> None:
        env_file = self.files / "ENV_PROD"
        content = env_file.read_text(encoding="utf-8")
        content = content.replace(
            "COMPOSE_PROJECT_NAME=real-jaen-analytics-sinbad2-prod",
            "COMPOSE_PROJECT_NAME=attacker-selected-project",
        )
        env_file.write_text(content, encoding="utf-8")
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("critical setting COMPOSE_PROJECT_NAME does not match", result.stderr)
        calls = self.docker_calls()
        self.assertFalse(any(call[:2] == ["volume", "inspect"] for call in calls))
        self.assertFalse(any(self.compose_call(call, "up") for call in calls))

    def test_env_prod_rejects_altered_volume_identity(self) -> None:
        env_file = self.files / "ENV_PROD"
        content = env_file.read_text(encoding="utf-8").replace(
            "RJ_PROD_ACQUISITION_JOBS_VOLUME=rja-sinbad2-acquisition-jobs",
            "RJ_PROD_ACQUISITION_JOBS_VOLUME=other-acquisition-jobs",
        )
        env_file.write_text(content, encoding="utf-8")
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("critical setting RJ_PROD_ACQUISITION_JOBS_VOLUME does not match", result.stderr)
        calls = self.docker_calls()
        self.assertFalse(any(call[:2] == ["volume", "inspect"] for call in calls))
        self.assertFalse(any(self.compose_call(call, "up") for call in calls))

    def test_preflight_failure_leaves_running_stack_and_current_links_untouched(self) -> None:
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        root = self.remote_home / "deploy_real-jaen-analytics"
        old_config = (root / "shared/config/current").resolve()
        old_secret = (root / "shared/secrets/current").resolve()
        state_before = json.loads(self.state.read_text(encoding="utf-8"))
        env_file = self.files / "ENV_PROD"
        env_file.write_text(env_file.read_text(encoding="utf-8") + "# rotated config\n", encoding="utf-8")
        env = self.environment(pipeline_id=102, job_id=502)
        env["RJA_TEST_FAIL_CATALOG"] = "1"

        failed = self.invoke(env=env)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("video catalog is invalid or unreadable by UID 10001", failed.stderr)
        self.assertEqual((root / "shared/config/current").resolve(), old_config)
        self.assertEqual((root / "shared/secrets/current").resolve(), old_secret)
        state_after = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state_after["up_count"], state_before["up_count"])
        self.assertEqual(state_after["deployed"], state_before["deployed"])
        self.assertFalse(any(call[:2] == ["network", "disconnect"] for call in self.docker_calls()))

    def test_last_good_rename_failure_rolls_back_services_and_links(self) -> None:
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        root = self.remote_home / "deploy_real-jaen-analytics"
        old_good = self.last_good()
        old_secret = (root / "shared/secrets/current").resolve()
        self.write_file(self.files / "APP_ACCESS_PASSWORD", secrets.token_urlsafe(20) + "\n")
        env = self.environment(pipeline_id=102, job_id=502)
        env["RJA_TEST_FAIL_LAST_GOOD"] = "1"

        failed = self.invoke(env=env)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("previous code/config and services were restored", failed.stderr)
        self.assertEqual(self.last_good(), old_good)
        self.assertEqual((root / "shared/secrets/current").resolve(), old_secret)
        self.assertEqual(len([call for call in self.docker_calls() if self.compose_call(call, "up")]), 3)
        self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))["deployed"], True)

    def test_up_failure_restarts_previous_release_without_volume_removal(self) -> None:
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        marker = self.last_good()
        env = self.environment(pipeline_id=102, job_id=502)
        env["CI_COMMIT_SHA"] = "b" * 40
        env["RJA_TEST_FAIL_UP_AT"] = "2"
        second = self.invoke(env=env)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("previous code/config was restarted", second.stderr)
        self.assertIn("Data compatibility depends on schema changes", second.stderr)
        self.assertEqual(self.last_good(), marker)
        calls = self.docker_calls()
        self.assertEqual(len([call for call in calls if self.compose_call(call, "up")]), 4)
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
