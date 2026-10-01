"""Regression cases for publication secrets, JSONC and complete vendor verification."""

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devlib import parse_jsonc, prepare_file_config, read_secrets
from release_checks import SECRET_NAMES, secret_findings
from vendor_sources import compare_trees


class JsoncTests(unittest.TestCase):
    def test_comments_trailing_commas_and_escaped_strings(self):
        value = {
            "url": "https://example.com/a//b?x=/*text*/",
            "quote": 'a"b\\c',
            "array": [1, 2],
        }
        text = "// Operator config\n" + json.dumps(value)[:-1] + ", /* note */ }"
        self.assertEqual(parse_jsonc(text), value)
        self.assertEqual(parse_jsonc('{"x": [1, /* comment */ 2,],}'), {"x": [1, 2]})

    def test_invalid_jsonc_stays_invalid(self):
        for value in [
            '{"a":1 /* unfinished',
            "[,]",
            '{"a":1,,}',
            '{"a": "unterminated}',
        ]:
            with self.subTest(value=value), self.assertRaises(json.JSONDecodeError):
                parse_jsonc(value)

    def test_fixture_config_is_generated_without_editing_template_or_legacy_secrets(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            template = root / "fixtures/files/core-r2/wrangler.jsonc"
            template.parent.mkdir(parents=True)
            template.write_text(
                '// Keep this comment\n{"main":"../../../build/core/index.js",}'
            )
            original = template.read_bytes()
            legacy = template.parent / ".dev.vars"
            legacy.write_text("UNRELATED=preserved\n")
            with patch("devlib.ROOT", root):
                directory = prepare_file_config("core", "r2", {"CALLER": "test-value"})
            self.assertEqual(directory, root / ".cache/fixtures/files/core-r2")
            self.assertEqual(template.read_bytes(), original)
            self.assertEqual(legacy.read_text(), "UNRELATED=preserved\n")
            self.assertEqual(
                read_secrets(directory / ".dev.vars")["UNRELATED"], "preserved"
            )
            self.assertEqual(
                json.loads((directory / "wrangler.jsonc").read_text())["main"],
                str(root / "build/core/index.js"),
            )


class SecretTests(unittest.TestCase):
    def test_project_credentials_detected_without_disclosing_values(self):
        value = "synthetic-" + "a" * 48
        for name in SECRET_NAMES:
            for text, filename in [
                (f'{name}="{value}"', "example.sh"),
                (f"{name}={value}", ".env.example"),
                (json.dumps({"vars": {name: value}}), "wrangler.jsonc"),
                (f"config = {{'{name}': '{value}'}}", "example.py"),
            ]:
                with self.subTest(name=name, filename=filename):
                    findings = secret_findings(text, filename)
                    self.assertTrue(findings)
                    self.assertNotIn(value, str(findings))

    def test_blank_examples_placeholders_and_runtime_values(self):
        for name in SECRET_NAMES:
            for value in ["", "<supply-securely>", "${RUNTIME_SECRET}"]:
                self.assertFalse(
                    secret_findings(f'{name}="{value}"', ".dev.vars.example")
                )
            self.assertFalse(secret_findings(f"{name}=", ".dev.vars.example"))
            self.assertFalse(
                secret_findings(f'values = {{"{name}": secret()}}', "example.py")
            )
            self.assertFalse(
                secret_findings(f'out.write("{name}=" + token)', "example.py")
            )

    def test_yaml_and_numeric_json_credentials(self):
        name = SECRET_NAMES[0]
        value = int("1234" * 12)
        for text, filename in [
            (f"{name}: {value}", "workflow.yml"),
            (json.dumps({"vars": {name: value}}), "wrangler.jsonc"),
        ]:
            self.assertTrue(secret_findings(text, filename))
        for value in ["{" + "a" * 48, "$" + "9" * 48]:
            self.assertTrue(secret_findings(json.dumps({name: value}), "config.jsonc"))


class VendorTests(unittest.TestCase):
    def test_unpatched_changed_added_deleted_and_symlinked_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected, actual = root / "expected", root / "actual"
            (expected / "src").mkdir(parents=True)
            (expected / "src/untouched.rs").write_text("original")
            shutil.copytree(expected, actual)
            compare_trees(expected, actual)
            for mutation in ["changed", "added", "deleted", "symlink"]:
                with self.subTest(mutation=mutation):
                    shutil.rmtree(actual)
                    shutil.copytree(expected, actual)
                    source = actual / "src/untouched.rs"
                    if mutation == "changed":
                        source.write_text("unrecorded")
                    elif mutation == "added":
                        (actual / "src/extra.rs").write_text("unrecorded")
                    else:
                        source.unlink()
                        if mutation == "symlink":
                            source.symlink_to(expected / "src/untouched.rs")
                    with self.assertRaisesRegex(RuntimeError, "preserved"):
                        compare_trees(expected, actual)

    def test_generated_outputs_excluded_but_git_ignored_sources_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected, actual = root / "expected", root / "actual"
            expected.mkdir()
            (expected / ".gitignore").write_text("*.rs\n")
            shutil.copytree(expected, actual)
            (actual / "target").mkdir()
            (actual / "target/generated.rs").write_text("output")
            compare_trees(expected, actual)
            (actual / "unexpected.rs").write_text("unrecorded")
            with self.assertRaisesRegex(RuntimeError, "unexpected.rs"):
                compare_trees(expected, actual)


if __name__ == "__main__":
    unittest.main()
