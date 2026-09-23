"""Mocked integration checks for the Pi deploy controller's rollback contract."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "scripts" / "pi-deploy-controller.sh"
GIT = Path("/Library/Developer/CommandLineTools/usr/bin/git")
if not GIT.exists():
    GIT = Path(shutil.which("git") or "git")


class PiDeployControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rss-kindle-deploy-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.repo = self.home / "rss-to-kindle"
        self.seed = self.root / "seed"
        self.bare = self.root / "origin.git"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self._install_shims()
        self._create_repository()
        self._add_candidate_commit()
        self.old_sha = self._git(self.repo, "rev-parse", "HEAD")
        self.new_sha = self._git(self.seed, "rev-parse", "HEAD")
        self.pin_file = self.repo / ".env"
        self.runtime_file = self.repo / ".env.runtime"
        self.pin_file.write_text(f"IMAGE_TAG={self.old_sha}\n")
        self.pin_file.chmod(0o600)
        self.runtime_file.write_text(
            "SENDER_EMAIL=sender@example.com\nSENDER_PASSWORD=do-not-log-this\n"
            "KINDLE_EMAIL=reader@kindle.com\n"
        )
        self.runtime_file.chmod(0o600)
        (self.repo / "data").mkdir()
        (self.repo / "data" / "local-wip").write_text("keep")
        (self.repo / "ideas.md").write_text("keep local notes")
        self.state = self.home / ".local/share/rss-to-kindle-deploy"
        self.dagu = self.home / ".config/dagu"
        self.state.mkdir(parents=True)
        self.dagu.mkdir(parents=True)
        self.failed_file = self.dagu / ".rss-to-kindle-deploy-failed-sha"
        self.deployed_file = self.dagu / ".rss-to-kindle-deployed-sha"
        self.deployed_file.write_text(f"{self.old_sha}\n")
        self.command_log = self.root / "commands.log"

    def _git(self, cwd, *args):
        completed = subprocess.run(
            [str(GIT), "-C", str(cwd), *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def _create_repository(self):
        self.seed.mkdir()
        self._git(self.seed, "init", "-b", "main")
        self._git(self.seed, "config", "user.name", "Deploy Test")
        self._git(self.seed, "config", "user.email", "deploy-test@example.com")
        (self.seed / ".gitignore").write_text(".env\n.env.runtime\n/data/\n")
        (self.seed / "docker-compose.yml").write_text("compose version: old\n")
        (self.seed / "tracked.txt").write_text("old source\n")
        self._git(self.seed, "add", ".gitignore", "docker-compose.yml", "tracked.txt")
        self._git(self.seed, "commit", "-m", "known good")
        self._git(self.seed, "clone", "--bare", str(self.seed), str(self.bare))
        self._git(self.seed, "remote", "add", "origin", str(self.bare))
        self._git(self.seed, "clone", str(self.bare), str(self.repo))

    def _add_candidate_commit(self):
        (self.seed / "docker-compose.yml").write_text("compose version: candidate\n")
        (self.seed / "tracked.txt").write_text("candidate source\n")
        self._git(self.seed, "add", "docker-compose.yml", "tracked.txt")
        self._git(self.seed, "commit", "-m", "candidate")
        self._git(self.seed, "push", "origin", "main")

    def _install_shims(self):
        docker = self.bin / "docker"
        docker.write_text(
            """#!/usr/bin/env bash
set -u
printf 'docker|%s|%s\\n' "$*" "${IMAGE_TAG:-none}" >> "$FAKE_COMMAND_LOG"
if [[ "$1" == image && "$2" == inspect ]]; then
  [[ "${FAKE_MISSING_OLD_IMAGE:-}" == "$3" ]] && exit 1
  exit 0
fi
if [[ "$1" == image && "$2" == tag ]]; then exit 0; fi
if [[ "$1" == compose ]]; then
  shift
  case "$1" in
    build) [[ "${FAKE_BUILD_FAIL:-0}" != 1 ]] || exit 42 ;;
    stop) [[ "${FAKE_STOP_FAIL:-0}" != 1 ]] || exit 42 ;;
    up)
      if [[ -n "${FAKE_UP_FAIL_TAG:-}" && "${IMAGE_TAG:-}" == "${FAKE_UP_FAIL_TAG:-}" ]]; then
        exit 43
      fi
      ;;
  esac
fi
exit 0
"""
        )
        curl = self.bin / "curl"
        curl.write_text(
            """#!/usr/bin/env bash
for arg in "$@"; do url="$arg"; done
if [[ "${url:-}" == http://127.0.0.1:18788/api/health ]]; then
  [[ "${FAKE_HEALTH_FAIL:-0}" != 1 ]] || exit 22
  printf '{"status":"ok","database":"ok","worker":"running"}\\n'
fi
exit 0
"""
        )
        (self.bin / "sleep").write_text("#!/bin/sh\nexit 0\n")
        (self.bin / "flock").write_text("#!/bin/sh\nexit 0\n")
        for path in (docker, curl, self.bin / "sleep", self.bin / "flock"):
            path.chmod(0o755)

    def _env(self, **overrides):
        env = os.environ.copy()
        env.pop("NTFY_TOPIC", None)
        env.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.bin}{os.pathsep}{env['PATH']}",
                "GIT_BIN": str(GIT),
                "FAKE_COMMAND_LOG": str(self.command_log),
                "RSS_TO_KINDLE_REPO_DIR": str(self.repo),
                "RSS_TO_KINDLE_DEPLOY_STATE_DIR": str(self.state),
                "RSS_TO_KINDLE_FAILED_SHA_FILE": str(self.failed_file),
                "RSS_TO_KINDLE_DEPLOYED_SHA_FILE": str(self.deployed_file),
                "RSS_TO_KINDLE_LOCK_FILE": str(self.dagu / ".rss-to-kindle-deploy.lock"),
                "RSS_TO_KINDLE_LOG_FILE": str(self.root / "deploy.log"),
                "RSS_TO_KINDLE_REQUIRE_EXT4": "0",
            }
        )
        env.update({key: str(value) for key, value in overrides.items()})
        return env

    def _run(self, *args, **overrides):
        return subprocess.run(
            ["/bin/bash", str(CONTROLLER), *args],
            env=self._env(**overrides),
            capture_output=True,
            text=True,
            timeout=20,
        )

    def _commands(self):
        return self.command_log.read_text().splitlines() if self.command_log.exists() else []

    def _assert_restored(self):
        self.assertEqual(self._git(self.repo, "rev-parse", "HEAD"), self.old_sha)
        self.assertEqual(self.pin_file.read_text(), f"IMAGE_TAG={self.old_sha}\n")
        self.assertEqual(self.deployed_file.read_text(), f"{self.old_sha}\n")
        self.assertEqual((self.repo / "docker-compose.yml").read_text(), "compose version: old\n")
        self.assertEqual((self.repo / "data" / "local-wip").read_text(), "keep")
        self.assertEqual((self.repo / "ideas.md").read_text(), "keep local notes")

    def test_success_builds_before_stop_then_pins_healthy_candidate(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self._commands()
        build = next(i for i, line in enumerate(commands) if "compose build" in line)
        stop = next(i for i, line in enumerate(commands) if "compose stop" in line)
        candidate_up = next(
            i for i, line in enumerate(commands) if "compose up" in line and self.new_sha in line
        )
        self.assertLess(build, stop)
        self.assertLess(stop, candidate_up)
        self.assertEqual(self._git(self.repo, "rev-parse", "HEAD"), self.new_sha)
        self.assertEqual(self.pin_file.read_text(), f"IMAGE_TAG={self.new_sha}\n")
        self.assertEqual(self.deployed_file.read_text(), f"{self.new_sha}\n")
        self.assertFalse(self.failed_file.exists())
        self.assertEqual((self.repo / "data" / "local-wip").read_text(), "keep")
        self.assertEqual((self.repo / "ideas.md").read_text(), "keep local notes")
        self.assertNotIn("do-not-log-this", result.stdout + result.stderr)
        self.assertNotIn("do-not-log-this", self.command_log.read_text())

    def test_build_failure_keeps_old_release_and_holds_bad_sha(self):
        result = self._run(FAKE_BUILD_FAIL=1)
        self.assertNotEqual(result.returncode, 0)
        self._assert_restored()
        self.assertEqual(self.failed_file.read_text(), f"{self.new_sha}\n")
        self.assertFalse(any("compose stop" in line for line in self._commands()))
        prior_count = len([line for line in self._commands() if "compose build" in line])
        retry = self._run()
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(
            len([line for line in self._commands() if "compose build" in line]), prior_count
        )

    def test_candidate_start_failure_immediately_restores_old_image_and_compose(self):
        result = self._run(FAKE_UP_FAIL_TAG=self.new_sha)
        self.assertNotEqual(result.returncode, 0)
        self._assert_restored()
        self.assertEqual(self.failed_file.read_text(), f"{self.new_sha}\n")
        lines = self._commands()
        self.assertTrue(any("compose stop --timeout 240 web" in line for line in lines))
        self.assertTrue(any("compose up" in line and self.old_sha in line for line in lines))

    def test_unhealthy_candidate_is_rolled_back_before_controller_exits(self):
        result = self._run(FAKE_HEALTH_FAIL=1)
        self.assertNotEqual(result.returncode, 0)
        self._assert_restored()
        self.assertEqual(self.failed_file.read_text(), f"{self.new_sha}\n")
        lines = self._commands()
        self.assertTrue(any("compose up" in line and self.new_sha in line for line in lines))
        self.assertTrue(any("compose up" in line and self.old_sha in line for line in lines))

    def test_tracked_local_work_is_refused_without_overwrite(self):
        local_work = self.repo / "tracked.txt"
        local_work.write_text("private unfinished work\n")
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(local_work.read_text(), "private unfinished work\n")
        self.assertFalse(self.command_log.exists())

    def test_candidate_that_claims_untracked_path_is_refused(self):
        (self.seed / "ideas.md").write_text("upstream file\n")
        self._git(self.seed, "add", "ideas.md")
        self._git(self.seed, "commit", "-m", "colliding path")
        self._git(self.seed, "push", "origin", "main")
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.repo / "ideas.md").read_text(), "keep local notes")
        self.assertEqual(self._git(self.repo, "rev-parse", "HEAD"), self.old_sha)
        self.assertFalse(self.command_log.exists())

    def test_recover_only_restores_snapshot_when_candidate_removed_compose_file(self):
        self._git(self.seed, "rm", "docker-compose.yml")
        self._git(self.seed, "commit", "-m", "remove candidate compose")
        self._git(self.seed, "push", "origin", "main")
        deleted_compose_sha = self._git(self.seed, "rev-parse", "HEAD")
        self._git(self.repo, "fetch", "origin", "main")
        self._git(self.repo, "reset", "--hard", deleted_compose_sha)
        self.assertFalse((self.repo / "docker-compose.yml").exists())

        backup = self.state / "docker-compose.yml.previous"
        backup.write_text("compose version: old\n")
        backup.chmod(0o600)
        (self.state / "transaction").write_text(
            f"OLD_SHA={self.old_sha}\nCANDIDATE_SHA={deleted_compose_sha}\n"
        )
        (self.state / "transaction").chmod(0o600)

        result = self._run("--recover-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self._assert_restored()
        self.assertEqual(self.failed_file.read_text(), f"{deleted_compose_sha}\n")
        self.assertFalse((self.state / "transaction").exists())


if __name__ == "__main__":
    unittest.main()
