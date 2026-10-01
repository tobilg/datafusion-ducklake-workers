#!/usr/bin/env python3
"""Negative tests confined to loopback resources created by this repository.
Temporarily removes/restores only the local fixture's own policy in a finally block.
"""

from devlib import REPORTS, service, read_jsonc
import base64, copy, hashlib, hmac, http.server, json, os, pathlib, socket, threading, time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

root = pathlib.Path(__file__).resolve().parents[1]
with socket.socket() as port_check:
    try:
        port_check.bind(("127.0.0.1", 18893))
    except OSError:
        raise SystemExit(
            "Port 18893 is occupied; stop the conflicting local fixture before testing"
        )
state = json.loads((root / ".cache/local-catalog.json").read_text())
secret = dict(
    l.split("=", 1)
    for l in (root / "fixtures/quacklake/.dev.vars").read_text().splitlines()
)
caller = (root / ".cache/query-api-token").read_text()
directory = root / ".cache/failure-worker"
directory.mkdir(exist_ok=True)


def admin(method, path, body=None):
    req = Request(
        "http://127.0.0.1:8792" + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": "Bearer " + secret["ADMIN_TOKEN"],
            "Content-Type": "application/json",
        },
    )
    with urlopen(req, timeout=10) as r:
        return json.load(r)


def token_change(**values):
    parts = state["reader_jwt"].split(".")
    payload = json.loads(base64.urlsafe_b64decode(parts[1] + "==="))
    payload.update(values)
    enc = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
    body = parts[0] + "." + enc(json.dumps(payload, separators=(",", ":")).encode())
    return (
        body
        + "."
        + enc(
            hmac.new(
                secret["QUACKLAKE_JWT_SECRET"].encode(), body.encode(), hashlib.sha256
            ).digest()
        )
    )


class Faults(http.server.BaseHTTPRequestHandler):
    mode = "malformed"

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        if self.mode == "oversized":
            self.send_header("Content-Length", str(2 * 1024 * 1024 + 1))
            self.end_headers()
            return
        if self.mode == "chunked":
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for _ in range(34):
                    self.wfile.write(b"10000\r\n" + b"x" * 65536 + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if self.mode == "counts":
            # A real binary connection response, then a malicious logical-type vector count.
            data = (
                bytes.fromhex("01000202000178ffff030001ffff")
                if body[2] == 1
                else bytes.fromhex("01000402000178ffff0100ffffffff0f")
            )
        else:
            data = b"not a Quack message"
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


server = http.server.ThreadingHTTPServer(("127.0.0.1", 18894), Faults)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = read_jsonc(root / "fixtures/wrangler.catalog-probe.jsonc")
base.pop("$schema", None)
base.update(
    name="datafusion-local-failure-test", main=str(root / "build/full/index.js")
)
report = []


def case(
    name,
    *,
    jwt=state["reader_jwt"],
    vars=None,
    no_binding=False,
    query=False,
    s3=False,
    expected=503,
):
    config = copy.deepcopy(base)
    if vars:
        config["vars"].update(vars)
    if no_binding:
        config.pop("r2_buckets", None)
    if s3:
        config.pop("r2_buckets", None)
        config["vars"].update(
            STORAGE_BACKEND="s3",
            S3_ENDPOINT="http://127.0.0.1:9000",
            S3_BUCKET="quacklake-fixture",
            S3_REGION="us-east-1",
            S3_ADDRESSING_STYLE="path",
        )
    (directory / "wrangler.json").write_text(json.dumps(config))
    fd = os.open(directory / ".dev.vars", os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"API_KEY={caller}\nQUACKLAKE_JWT={jwt}\n")
        if s3:
            f.write(
                "S3_ACCESS_KEY_ID=invalid-local-test\nS3_SECRET_ACCESS_KEY=invalid-local-test\n"
            )
    with service(
        [
            root / "node_modules/.bin/wrangler",
            "dev",
            "--config",
            directory / "wrangler.json",
            "--local",
            "--ip",
            "127.0.0.1",
            "--port",
            "18893",
            "--inspector-port",
            "9235",
            "--persist-to",
            root / ".cache/local-state",
        ],
        18893,
        "failure-worker/server.log",
        extra_ports=(9235,),
    ):
        path = "/query" if query else "/readyz"
        req = Request(
            "http://127.0.0.1:18893" + path,
            data=(
                json.dumps({"sql": "SELECT sum(id) FROM sales"}).encode()
                if query
                else None
            ),
            headers={
                "Authorization": "Bearer " + caller,
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=15) as r:
                code = r.status
                body = r.read()
        except HTTPError as e:
            code = e.code
            body = e.read()
        assert code == expected, (name, code, body)
        assert (
            caller.encode() not in body and jwt.encode() not in body
            if jwt
            else caller.encode() not in body
        )
        report.append({"test": name, "status": code})
        print("PASS", name, flush=True)


try:
    case("missing binding", no_binding=True)
    case("canonical bucket mismatch", vars={"CATALOG_BUCKET": "wrong-bucket"})
    case(
        "canonical prefix mismatch",
        vars={"CATALOG_DATA_PATH": "r2://quacklake-fixture/catalogs/elsewhere/"},
    )
    case("bad service JWT", jwt="invalid")
    case("expired service JWT", jwt=token_change(exp=int(time.time()) - 30))
    case("wrong catalog JWT", jwt=token_change(catalog_id="wrong-catalog"))
    fresh = admin(
        "POST",
        "/admin/catalogs/fixture/credentials",
        {"scopes": ["query.read"], "expiresInSeconds": 3600},
    )
    replacement = admin(
        "POST",
        "/admin/catalogs/fixture/credentials",
        {"scopes": ["query.read"], "expiresInSeconds": 3600},
    )
    case("new credential fresh Worker connection", jwt=fresh["jwt"], expected=200)
    case(
        "replacement credential fresh Worker query",
        jwt=replacement["jwt"],
        query=True,
        expected=200,
    )
    admin("DELETE", "/admin/catalogs/fixture/credentials/" + fresh["credentialId"])
    case("revoked service JWT", jwt=fresh["jwt"])
    try:
        case(
            "replacement works after old revocation",
            jwt=replacement["jwt"],
            query=True,
            expected=200,
        )
    finally:
        admin(
            "DELETE",
            "/admin/catalogs/fixture/credentials/" + replacement["credentialId"],
        )
    policy = admin("GET", "/admin/catalogs/fixture/auth-policy")["policy"]
    try:
        admin("DELETE", "/admin/catalogs/fixture/auth-policy")
        case("valid JWT without policy")
    finally:
        admin("PUT", "/admin/catalogs/fixture/auth-policy", policy)
    case("unavailable QuackLake", vars={"QUACK_URI": "quack:127.0.0.1:18895"})
    for mode in ("malformed", "oversized", "chunked", "counts"):
        Faults.mode = mode
        case("metadata " + mode, vars={"QUACK_URI": "quack:127.0.0.1:18894"})
    case("invalid S3 credentials without fallback", s3=True, query=True)
finally:
    server.shutdown()
    server.server_close()
(root / ".cache/reports/failures.json").write_text(json.dumps(report, indent=2) + "\n")
