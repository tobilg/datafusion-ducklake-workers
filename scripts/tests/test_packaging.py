"""Release archives must retain matching glue, runtime bytes and license texts."""

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from package_release import copy_modules, package_licenses, selected_packages


class PackagingTests(unittest.TestCase):
    def fixture(self, root):
        source, destination = root / "source", root / "package"
        destination.mkdir()
        records = []
        for name in ("index.js", "index_bg.wasm", "worker/shim.mjs", "package.json"):
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
            records.append(
                {
                    "path": "build/core/" + name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        return source, destination, {"variant": "core", "modules": records}

    def test_all_javascript_companions_and_wasm_are_packaged_without_local_secrets(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            source, dest, manifest = self.fixture(Path(tmp))
            (source / ".dev.vars").write_text("must not be distributed")
            names = copy_modules(source, dest, manifest)
            self.assertEqual(
                names, {"index.js", "index_bg.wasm", "worker/shim.mjs", "package.json"}
            )
            self.assertFalse((dest / ".dev.vars").exists())
            self.assertEqual(
                (source / "worker/shim.mjs").read_bytes(),
                (dest / "worker/shim.mjs").read_bytes(),
            )

    def test_missing_or_mismatched_javascript_is_rejected(self):
        for missing in (True, False):
            with tempfile.TemporaryDirectory() as tmp:
                source, dest, manifest = self.fixture(Path(tmp))
                shim = source / "worker/shim.mjs"
                if missing:
                    shim.unlink()
                else:
                    shim.write_text("another build's glue")
                with self.assertRaisesRegex(RuntimeError, "companion module"):
                    copy_modules(source, dest, manifest)

    def test_incomplete_manifest_and_traversal_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, dest, manifest = self.fixture(Path(tmp))
            manifest["modules"] = manifest["modules"][:2]
            with self.assertRaisesRegex(RuntimeError, "Incomplete Worker"):
                copy_modules(source, dest, manifest)
            manifest["modules"][0]["path"] = "build/core/../../secret"
            with self.assertRaisesRegex(RuntimeError, "Unsafe module"):
                copy_modules(source, dest, manifest)

    def test_workspace_license_is_found_and_application_license_is_not_substituted(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upstream = root / "vendor/provider"
            crate = upstream / "crates/client"
            crate.mkdir(parents=True)
            (upstream / "LICENSE").write_text("upstream license")
            (root / "LICENSE").write_text("application license")
            package = {"manifest_path": str(crate / "Cargo.toml")}
            with patch("package_release.ROOT", root):
                self.assertEqual(
                    package_licenses(package),
                    [(Path("workspace/LICENSE"), upstream / "LICENSE")],
                )
                self.assertEqual(
                    package_licenses({"manifest_path": str(root / "Cargo.toml")}),
                    [(Path("LICENSE"), root / "LICENSE")],
                )

    def test_development_only_dependencies_are_excluded(self):
        metadata = {
            "resolve": {
                "root": "app",
                "nodes": [
                    {
                        "id": "app",
                        "deps": [
                            {"pkg": "runtime", "dep_kinds": [{"kind": None}]},
                            {"pkg": "fixture", "dep_kinds": [{"kind": "dev"}]},
                        ],
                    },
                    {"id": "runtime", "deps": []},
                    {"id": "fixture", "deps": []},
                ],
            },
            "packages": [
                {"id": name, "name": name, "version": "1"}
                for name in ("app", "runtime", "fixture")
            ],
        }
        self.assertEqual(
            [p["name"] for p in selected_packages(metadata)], ["app", "runtime"]
        )


if __name__ == "__main__":
    unittest.main()
