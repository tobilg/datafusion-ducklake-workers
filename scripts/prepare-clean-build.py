#!/usr/bin/env python3
"""Export a clean application tree with no ignored files or credentials.
Caches are reused only with --reuse-caches; the default exports sources only.
"""

import argparse, pathlib, re, subprocess

root = pathlib.Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument("--name", default="clean-application")
p.add_argument("--reuse-caches", action="store_true")
a = p.parse_args()
assert re.fullmatch("clean-[a-z0-9-]+", a.name)
dest = root / ".cache" / a.name
assert (
    not dest.exists()
), "Preserve the existing clean build; use a new explicit directory for another run"
paths = (
    subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
    )
    .decode()
    .split("\0")
)
dest.mkdir()
for name in filter(None, paths):
    source = root / name
    if not source.is_file():
        continue
    assert (
        ".dev.vars" not in name or name.endswith(".dev.vars.example")
    ) and ".cache" not in source.parts
    assert not name.startswith(("vendor/", "node_modules/", ".secrets/"))
    target = dest / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    target.chmod(source.stat().st_mode)
subprocess.run(["git", "init", "--quiet", str(dest)], check=True)
if a.reuse_caches:
    for name in ("vendor", "node_modules", ".tools", "target"):
        (dest / name).symlink_to(root / name, target_is_directory=True)
print(f"Exported to .cache/{a.name}; reused project caches: {a.reuse_caches}")
print(f"Run: cd .cache/{a.name} && bash scripts/bootstrap.sh && bash scripts/build.sh")
