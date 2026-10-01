#!/usr/bin/env python3
"""Create local-only QuackLake fixture secrets, without replacing existing values."""

from pathlib import Path
import os
import secrets

root = Path(__file__).resolve().parents[1]
path = root / "fixtures/quacklake/.dev.vars"
try:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print("Existing local fixture secrets preserved.")
else:
    with os.fdopen(fd, "w") as output:
        for name in (
            "ADMIN_TOKEN",
            "QUACKLAKE_JWT_SECRET",
            "CONNECTION_SIGNING_SECRET",
        ):
            output.write(f"{name}={secrets.token_urlsafe(48)}\n")
    print(
        "Created local fixture secrets in ignored fixtures/quacklake/.dev.vars (0600)."
    )
