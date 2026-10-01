"""Shared local development helpers. No Cloudflare control-plane operations."""

import contextlib
import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / ".cache/reports"
REPORTS.mkdir(parents=True, exist_ok=True)


def parse_jsonc(text):
    """Read JSON with comments/trailing commas, preserving strings and error offsets."""
    chars = list(text)
    index = 0
    while index < len(chars):
        if chars[index] == '"':
            index += 1
            while index < len(chars):
                if chars[index] == "\\":
                    index += 2
                elif chars[index] == '"':
                    index += 1
                    break
                else:
                    index += 1
        elif text.startswith("//", index) or text.startswith("/*", index):
            start = index
            if text.startswith("//", index):
                end = text.find("\n", index)
                end = len(text) if end == -1 else end
            else:
                end = text.find("*/", index + 2)
                if end == -1:
                    raise json.JSONDecodeError("Unterminated comment", text, start)
                end += 2
            for position in range(start, end):
                if chars[position] not in "\r\n":
                    chars[position] = " "
            index = end
        else:
            index += 1
    quoted = False
    escaped = False
    for index, char in enumerate(chars):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == ",":
            end = index + 1
            while end < len(chars) and chars[end].isspace():
                end += 1
            start = index - 1
            while start >= 0 and chars[start].isspace():
                start -= 1
            if (
                end < len(chars)
                and chars[end] in "]}"
                and start >= 0
                and chars[start] not in "[{:,"
            ):
                chars[index] = " "
    return json.loads("".join(chars))


def read_jsonc(path):
    return parse_jsonc(Path(path).read_text())


def file_fixture_dir(variant, backend):
    if variant not in ("core", "full") or backend not in ("r2", "s3", "unsigned"):
        raise ValueError("Unknown file fixture")
    return ROOT / ".cache/fixtures/files" / f"{variant}-{backend}"


def prepare_file_config(variant, backend, values):
    """Copy the checked-in template to local state, preserving legacy secrets."""
    template = ROOT / f"fixtures/files/{variant}-{backend}/wrangler.jsonc"
    config = read_jsonc(template)
    config["main"] = str(ROOT / f"build/{variant}/index.js")
    config.pop("$schema", None)
    directory = file_fixture_dir(variant, backend)
    directory.mkdir(parents=True, exist_ok=True)
    secret = directory / ".dev.vars"
    existing = read_secrets(
        secret if secret.exists() else template.parent / ".dev.vars"
    )
    if backend in ("r2", "unsigned") and any(key.startswith("S3_") for key in existing):
        raise RuntimeError(
            f"Unexpected S3 secrets in {variant}-{backend}; preserve and reconcile the local fixture before testing"
        )
    existing.update(values)
    update_secrets(secret, existing)
    (directory / "wrangler.jsonc").write_text(json.dumps(config, indent=2) + "\n")
    return directory


def read_secrets(path):
    """Read generated single-line fixture values; never print their contents."""
    if not Path(path).exists():
        return {}
    return dict(
        line.split("=", 1)
        for line in Path(path).read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    )


def patch_series():
    return [
        (p["file"], p["source"])
        for p in json.loads((ROOT / "patches/series.json").read_text())
    ]


def api_key():
    path = ROOT / ".cache/query-api-token"
    if not path.exists():
        with os.fdopen(
            os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
        ) as out:
            out.write(secrets.token_urlsafe(48))
    return path.read_text().strip()


def update_secrets(path, values):
    """Preserve unrelated local values; never print credentials."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_secrets(path)
    existing.update(values)
    for key, value in existing.items():
        if "\n" in key or "\n" in value:
            raise ValueError("Local secret values must be single-line")
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
    ) as out:
        os.fchmod(out.fileno(), 0o600)
        for key, value in existing.items():
            out.write(f"{key}={value}\n")


def run(*args):
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True)


def local_s3_put(bucket, key, body, query=""):
    """SigV4 uploader restricted to the repository's loopback S3 fixture."""
    creds = json.loads((ROOT / ".cache/minio-credentials.json").read_text())
    now = datetime.datetime.now(datetime.timezone.utc)
    date, stamp = now.strftime("%Y%m%d"), now.strftime("%Y%m%dT%H%M%SZ")
    path = quote("/" + bucket + ("/" + key if key else ""), safe="/-_.~")
    digest = hashlib.sha256(body).hexdigest()
    signed = "host;x-amz-content-sha256;x-amz-date"
    canonical = (
        f"PUT\n{path}\n{query}\nhost:127.0.0.1:9000\n"
        f"x-amz-content-sha256:{digest}\nx-amz-date:{stamp}\n\n{signed}\n{digest}"
    )
    scope = f"{date}/us-east-1/s3/aws4_request"
    message = f"AWS4-HMAC-SHA256\n{stamp}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    signing = ("AWS4" + creds["secret_key"]).encode()
    for part in (date, "us-east-1", "s3", "aws4_request"):
        signing = hmac.new(signing, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(signing, message.encode(), hashlib.sha256).hexdigest()
    auth = f"AWS4-HMAC-SHA256 Credential={creds['access_key']}/{scope}, SignedHeaders={signed}, Signature={signature}"
    request = Request(
        "http://127.0.0.1:9000" + path + ("?" + query if query else ""),
        data=body,
        method="PUT",
        headers={
            "Authorization": auth,
            "x-amz-content-sha256": digest,
            "x-amz-date": stamp,
        },
    )
    try:
        with urlopen(request, timeout=90) as response:
            if response.status not in (200, 204):
                raise RuntimeError("Unexpected local S3 response")
    except HTTPError as error:
        if not key and not query and error.code == 409:
            return
        raise RuntimeError(f"Local S3 fixture returned HTTP {error.code}") from None


@contextlib.contextmanager
def service(
    command,
    port,
    log_name,
    health="/healthz",
    statuses=(200,),
    reuse=False,
    extra_ports=(),
):
    """Own and reap service process groups; never stop explicitly reused services."""

    def occupied(number):
        with socket.socket() as sock:
            return sock.connect_ex(("127.0.0.1", number)) == 0

    def healthy():
        try:
            with urlopen(f"http://127.0.0.1:{port}{health}", timeout=1) as response:
                return response.status in statuses
        except HTTPError as error:
            return error.code in statuses
        except (URLError, TimeoutError):
            return False

    if occupied(port):
        if not reuse or not healthy():
            raise RuntimeError(
                f"Port {port} is occupied. Stop that service or explicitly reuse your local fixture."
            )
        print(f"Reusing requested loopback fixture on port {port}", flush=True)
        yield
        return
    for number in extra_ports:
        if occupied(number):
            raise RuntimeError(f"Inspector port {number} is occupied")
    env = dict(
        os.environ,
        WRANGLER_SEND_METRICS="false",
        WRANGLER_LOG_PATH=str(ROOT / ".cache/wrangler"),
    )
    with (ROOT / ".cache" / log_name).open("w") as log:
        process = subprocess.Popen(
            [str(c) for c in command],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            until = time.monotonic() + 45
            while time.monotonic() < until:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"Local service exited; inspect .cache/{log_name}"
                    )
                if healthy():
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError(
                    f"Local service did not become ready; inspect .cache/{log_name}"
                )
            yield
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
