"""Regressions for CI routing, reusable tools and measuring tested artifacts."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ci_changes import changed_paths, needs_workers, routing
from measure_size import measurement_config, measure
from tool_cache import reuse_or_build
import sources


def git(directory, *args):
    return (
        subprocess.check_output(
            ["git", "-C", str(directory), *args], stderr=subprocess.DEVNULL
        )
        .decode()
        .strip()
    )


def commit_fixture(directory):
    git(directory, "init", "--quiet")
    (directory / "input.rs").write_text("pinned source")
    git(directory, "add", "input.rs")
    git(
        directory,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "fixture",
    )
    return git(directory, "rev-parse", "HEAD")


class SourceFetchTests(unittest.TestCase):
    def test_shallow_pinned_fetch_and_preservation_of_existing_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upstream = root / "upstream"
            upstream.mkdir()
            commit_fixture(upstream)
            (upstream / "input.rs").write_text("second revision")
            git(upstream, "add", "input.rs")
            git(
                upstream,
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "second",
            )
            revision = git(upstream, "rev-parse", "HEAD")
            app = root / "app"
            app.mkdir()
            (app / "sources.lock.json").write_text(
                json.dumps({"fixture": {"url": upstream.as_uri(), "rev": revision}})
            )
            with patch("sources.ROOT", app), patch.object(sys, "argv", ["sources.py"]):
                sources.main()
                checkout = app / "vendor/fixture"
                self.assertEqual(git(checkout, "rev-parse", "HEAD"), revision)
                self.assertEqual(
                    git(checkout, "rev-parse", "--is-shallow-repository"), "true"
                )
                (checkout / "input.rs").write_text("preserve my edit")
                sources.main()
                self.assertEqual(
                    (checkout / "input.rs").read_text(), "preserve my edit"
                )


class RoutingTests(unittest.TestCase):
    def test_only_known_prose_can_skip_workers(self):
        self.assertFalse(needs_workers(["README.md", "docs/setup.md", "LICENSE"]))
        for path in (
            "src/main.rs",
            "docs/fixture.json",
            "scripts/README.md",
            "Cargo.lock",
            ".github/workflows/ci.yml",
            "new-file",
            "fixtures/wrangler.jsonc",
        ):
            self.assertTrue(needs_workers(["README.md", path]), path)

    def test_pr_merge_base_and_deleted_sources_are_included(self):
        event = {"pull_request": {"base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}}
        calls = []

        def diff(command, **kwargs):
            calls.append(command)
            return b"src/removed.rs\0docs/renamed.md\0"

        paths = changed_paths("pull_request", event, "c" * 40, run=diff)
        self.assertTrue(needs_workers(paths))
        self.assertIn("--no-renames", calls[0])
        self.assertIn("a" * 40 + "..." + "b" * 40, calls[0])

    def test_push_diff_and_unavailable_history(self):
        def missing(*args, **kwargs):
            raise subprocess.CalledProcessError(128, "git")

        self.assertIsNone(
            changed_paths("push", {"before": "a" * 40}, "b" * 40, run=missing)
        )
        self.assertIsNone(changed_paths("push", {"before": "0" * 40}, "b" * 40))

        def diff(command, **kwargs):
            self.assertIn("a" * 40 + ".." + "b" * 40, command)
            return b"README.md\0"

        self.assertEqual(
            changed_paths("push", {"before": "a" * 40}, "b" * 40, run=diff),
            ["README.md"],
        )

    def test_manual_fresh_runs_always_build_without_cache(self):
        for fresh in ("true", True):
            result = routing(
                "workflow_dispatch",
                {"inputs": {"fresh": fresh, "layout": "shared"}},
                "a" * 40,
            )
            self.assertEqual(result["workers"], "true")
            self.assertEqual(result["use_cache"], "false")
        self.assertEqual(json.loads(result["matrix"]), {"variant": ["all"]})
        normal = routing("workflow_dispatch", {}, "a" * 40)
        self.assertEqual(normal["use_cache"], "true")
        self.assertEqual(json.loads(normal["matrix"]), {"variant": ["all"]})
        parallel = routing(
            "workflow_dispatch", {"inputs": {"layout": "parallel"}}, "a" * 40
        )
        self.assertEqual(json.loads(parallel["matrix"]), {"variant": ["core", "full"]})

    def test_push_and_pr_default_to_shared_build(self):
        with patch("ci_changes.changed_paths", return_value=["src/main.rs"]):
            for event_name in ("push", "pull_request"):
                result = routing(event_name, {}, "a" * 40)
                self.assertEqual(result["workers"], "true")
                self.assertEqual(result["use_cache"], "true")
                self.assertEqual(json.loads(result["matrix"]), {"variant": ["all"]})


class ToolCacheTests(unittest.TestCase):
    def test_modified_minio_sources_are_rejected_before_binary_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".cache").mkdir()
            (root / "fixtures").mkdir()
            checkout = root / "vendor/minio"
            checkout.mkdir(parents=True)
            revision = commit_fixture(checkout)
            (root / "fixtures/minio.lock.json").write_text(
                json.dumps({"revision": revision})
            )
            (checkout / "input.rs").write_text("unrecorded edit")
            with self.assertRaisesRegex(RuntimeError, "Unrecorded vendor changes"):
                reuse_or_build("minio", ["must-not-execute"], root)

    def fixture(self, root):
        for name in (
            "tools.lock.json",
            "sources.lock.json",
            "rust-toolchain.toml",
            "scripts/tool_cache.py",
            "fixtures/duckdb.lock.json",
            "scripts/install-duckdb.py",
        ):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("pinned inputs")
        binary = root / ".tools/bin/duckdb"

        def install(*args, **kwargs):
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_bytes(b"pinned executable")
            binary.chmod(0o755)

        return binary, install

    def test_warm_hit_and_input_binary_or_platform_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary, install = self.fixture(root)
            with patch("tool_cache.subprocess.run", side_effect=install) as build:
                reuse_or_build("duckdb", ["install"], root)
                reuse_or_build("duckdb", ["install"], root)
                self.assertEqual(build.call_count, 1)
                binary.write_bytes(b"corrupt")
                reuse_or_build("duckdb", ["install"], root)
                self.assertEqual(build.call_count, 2)
                (root / "fixtures/duckdb.lock.json").write_text("updated pin")
                reuse_or_build("duckdb", ["install"], root)
                self.assertEqual(build.call_count, 3)
                with patch(
                    "tool_cache.platform.machine", return_value="different-architecture"
                ):
                    reuse_or_build("duckdb", ["install"], root)
                self.assertEqual(build.call_count, 4)

    def test_failed_install_does_not_stamp_or_accept_partial_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary, _ = self.fixture(root)
            with patch(
                "tool_cache.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, "install"),
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    reuse_or_build("duckdb", ["install"], root)
            self.assertFalse((root / ".tools/cache/duckdb.json").exists())


class SizeTests(unittest.TestCase):
    def test_jsonc_paths_preserved_build_omitted_and_temporary_config_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wrangler.jsonc"
            source.write_text(
                '// operator config\n{"main":"build/core/index.js","build":{"command":"must-not-run"},"assets":{"directory":"./assets"},}'
            )
            original = source.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "failed dry-run"):
                with measurement_config(source, "core", root) as temporary:
                    self.assertEqual(temporary.parent, source.parent.resolve())
                    value = json.loads(temporary.read_text())
                    self.assertNotIn("build", value)
                    self.assertEqual(value["assets"]["directory"], "./assets")
                    self.assertEqual(value["main"], "build/core/index.js")
                    raise RuntimeError("failed dry-run")
            self.assertFalse(temporary.exists())
            self.assertEqual(source.read_bytes(), original)
            with self.assertRaisesRegex(ValueError, "selected production"):
                with measurement_config(source, "full", root):
                    self.fail("Wrong variant must be rejected")

    def test_stale_artifacts_stop_before_wrangler(self):
        with patch(
            "measure_size.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "check-artifacts"),
        ) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                measure(Path("unread-config.jsonc"), "core")
            self.assertEqual(run.call_count, 1)
            self.assertTrue(run.call_args.args[0][1].endswith("check-artifacts.py"))


if __name__ == "__main__":
    unittest.main()
