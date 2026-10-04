# Security policy

This project is experimental. It exposes authenticated read-only SQL and does
not provide per-caller row/column isolation. Everyone holding an `API_KEY` can
use the deployment's configured storage capabilities. See [API boundaries](docs/api.md).

For a suspected vulnerability, use GitHub's private vulnerability reporting on
this repository when available. If private reporting is unavailable, open an
issue requesting a private contact without including exploit details, credentials,
query data or affected customer identifiers. Maintainers should enable private
reporting after making the repository public and before announcing a release;
see the [enable and verification commands](docs/release.md#github-safeguards).
There is no promised response SLA.

`npm run audit` checks the locked core/full production graphs and Node tooling
against current advisories. CI runs it during Worker validation; no advisory
exceptions or scheduled runs are configured. Advisory checks complement the
runtime boundary tests and do not prove absence of vulnerabilities.

Only the current development revision is maintained; no older release support
window is promised. Use separate caller and catalog credentials, restrict storage
permissions, and follow [rotation and revocation](docs/operations.md). Never send
catalog signing/admin secrets to the query Worker or include them in reports.
