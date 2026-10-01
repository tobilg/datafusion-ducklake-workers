"""Publication checks shared with regression tests; never return secret values."""

import re
from devlib import parse_jsonc

SECRET_NAMES = (
    "API_KEY",
    "QUERY_API_TOKEN",
    "QUACKLAKE_JWT",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "S3_SESSION_TOKEN",
    "QUACKLAKE_ADMIN_TOKEN",
    "ADMIN_TOKEN",
    "QUACKLAKE_JWT_SECRET",
    "CONNECTION_SIGNING_SECRET",
    "CLOUDFLARE_API_TOKEN",
)
NAMES = "(?:" + "|".join(SECRET_NAMES) + ")"
QUOTED = re.compile(
    r"(?<![\w\"'])(?P<keyquote>[\"']?)(?P<name>"
    + NAMES
    + r")(?P=keyquote)(?![\w])\s*[:=]\s*"
    r"(?P<quote>[\"'])(?P<value>(?:\\.|(?!(?P=quote))[^\r\n])*)(?P=quote)"
)
BARE = re.compile(
    r"^\s*(?:export\s+)?(?P<name>" + NAMES + r")\s*[:=]\s*(?P<value>[^\s#;]+)",
    re.MULTILINE,
)
PATTERNS = {
    "JWT": re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{10,}"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS credential": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "GitHub credential": re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"
    ),
    "personal path": re.compile(r"/(?:Users|home)/[\w.-]+/"),
}


def placeholder(value):
    # Empty examples, explicit placeholders, and runtime interpolation are safe.
    # The last value is the deliberately invalid credential in local negative tests.
    return (
        not value
        or value == "invalid-local-test"
        or re.fullmatch(r"<[^<>\r\n]+>", value) is not None
        or re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*", value)
        is not None
        or value.startswith("$(")
    )


def secret_findings(text, name):
    findings = [label for label, pattern in PATTERNS.items() if pattern.search(text)]
    if name.endswith((".json", ".jsonc")):

        def visit(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if (
                        key in SECRET_NAMES
                        and item is not None
                        and not (isinstance(item, str) and placeholder(item))
                    ):
                        findings.append(f"Literal {key} JSON value")
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        try:
            visit(parse_jsonc(text))
        except ValueError:
            # Other checks validate config syntax; still scan malformed text.
            pass
    matches = list(QUOTED.finditer(text))
    # Bare assignments are dotenv/shell/documentation syntax. Bare Python/JS
    # identifiers are expressions; string literals there are covered above.
    if not name.endswith((".py", ".rs", ".mjs", ".js", ".ts", ".patch")):
        matches += list(BARE.finditer(text))
    for match in matches:
        value = match["value"].strip().strip("\"'")
        if not placeholder(value):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(f"Literal {match['name']} assignment at line {line}")
    return findings
