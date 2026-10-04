#!/usr/bin/env python3
"""Loopback-only range server with real Parquet and adversarial HTTP responses."""

import http.server, json, pathlib, re, urllib.parse

root = pathlib.Path(__file__).resolve().parents[1]


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self.serve(False)

    def do_GET(self):
        self.serve(True)

    def serve(self, body):
        parts = urllib.parse.urlsplit(self.path)
        path = parts.path
        if path == "/signed.parquet" and (
            not body or parts.query != "fixture_token=not-a-real-secret"
        ):
            self.send_error(403)
            return
        with (root / ".cache/file-http-requests.jsonl").open("a") as log:
            log.write(
                json.dumps(
                    {
                        "method": self.command,
                        "path": path,
                        "range": self.headers.get("Range"),
                        "authorization_present": bool(
                            self.headers.get("Authorization")
                        ),
                    }
                )
                + "\n"
            )
        if path in [
            "/oversized-list",
            "/oversized-list/",
            "/streamed-list",
            "/streamed-list/",
        ]:
            self.send_response(200)
            self.send_header("Content-Type", "application/xml")
            if path.startswith("/oversized-list"):
                self.send_header("Content-Length", str(2 * 1024 * 1024 + 1))
                self.end_headers()
                return
            self.end_headers()
            try:
                for _ in range(33):
                    self.wfile.write(b"x" * 65536)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if path.rstrip("/") in ("/namespace-list", "/attribute-list", "/normal-list"):
            # Well below the 2 MiB response cap. The namespace fixture must be
            # rejected by quick-xml before unbounded resolver allocation. The
            # ordinary-attribute fixture exercises its linear duplicate check.
            if path.startswith("/namespace-list"):
                attributes = " ".join(f'xmlns:n{i}="urn:n{i}"' for i in range(300))
            elif path.startswith("/attribute-list"):
                attributes = " ".join(f'a{i}="x"' for i in range(100000))
            else:
                attributes = 'xmlns:n="urn:fixture"'
            size = (
                (root / ".cache/file-fixtures/files-a/events/part0.parquet")
                .stat()
                .st_size
            )
            data = (
                f"<ListBucketResult {attributes}><IsTruncated>false</IsTruncated>"
                f"<Contents><Key>events/part0.parquet</Key><Size>{size}</Size>"
                '<LastModified>2026-09-29T12:00:00Z</LastModified><ETag>"fixture-v1"</ETag>'
                "</Contents></ListBucketResult>"
            ).encode()
            assert len(data) < 2 * 1024 * 1024
            self.send_response(200)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if body:
                self.wfile.write(data)
            return
        if path in [
            f"/{bucket}/events/part0.parquet"
            for bucket in ("namespace-list", "attribute-list", "normal-list")
        ]:
            path = "/data.parquet"
        if path == "/redirect.parquet":
            self.send_response(302)
            self.send_header("Location", "/data.parquet")
            self.end_headers()
            return
        if path == "/loop.parquet":
            self.send_response(302)
            self.send_header("Location", "/loop.parquet")
            self.end_headers()
            return
        if path == "/private-redirect.parquet":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/data.parquet")
            self.end_headers()
            return
        if path == "/nohead.parquet" and not body:
            self.send_error(405)
            return
        if path == "/missing.parquet":
            self.send_error(404)
            return
        if path not in [
            "/data.parquet",
            "/signed.parquet",
            "/large.parquet",
            "/nohead.parquet",
            "/ignore.parquet",
            "/bad-range.parquet",
            "/changed.parquet",
            "/truncated.parquet",
        ]:
            self.send_error(404)
            return
        file = (
            root
            / ".cache/file-fixtures/files-a"
            / ("large.parquet" if path == "/large.parquet" else "events/part0.parquet")
        )
        size = file.stat().st_size
        start = 0
        end = size
        etag = '"changed"' if path == "/changed.parquet" and body else '"fixture-v1"'
        if self.headers.get("If-Match") not in (None, etag):
            self.send_error(412)
            return
        match = re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers.get("Range", ""))
        partial = bool(match) and path != "/ignore.parquet"
        if partial:
            start = int(match[1])
            end = min(int(match[2]) + 1, size)
            if start >= end:
                self.send_error(416)
                return
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", "Tue, 29 Sep 2026 12:00:00 GMT")
        self.send_header("Content-Length", str(end - start))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header(
                "Content-Range",
                f'bytes {start+1 if path=="/bad-range.parquet" else start}-{end-1}/{size}',
            )
        self.end_headers()
        if not body:
            return
        if path == "/truncated.parquet":
            end -= 1
        try:
            with file.open("rb") as f:
                f.seek(start)
                while start < end:
                    chunk = f.read(min(65536, end - start))
                    self.wfile.write(chunk)
                    start += len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


print("Parquet range fixture listening on 127.0.0.1:8798", flush=True)
http.server.ThreadingHTTPServer(("127.0.0.1", 8798), Handler).serve_forever()
