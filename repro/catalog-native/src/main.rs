//! Real, local QuackLake provider probe. No DuckDB/SQLite/PostgreSQL client.
use ducklake_storage::{CatalogBackend, CreateSchemaCommit, DuckLakeCatalogInitOptions, quack::QuackCatalog};
use std::{error::Error, path::Path, time::Duration};
mod seed;
mod extended;

const DATA_PATH: &str = "r2://quacklake-fixture/catalogs/fixture/";

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.cache/local-catalog.json");
    let state: serde_json::Value = serde_json::from_slice(&std::fs::read(path).expect("local fixture state"))
        .expect("valid fixture JSON");
    let mode = std::env::args().nth(1).expect("mode: missing | initialize | read");
    match tokio::time::timeout(Duration::from_secs(if mode=="extended" {180}else{20}), probe(&state, &mode)).await {
        Ok(Ok(())) => (),
        error => {
            // The fixture file contains credentials; errors must never expose them.
            let mut message = format!("{error:?}");
            for name in ["bootstrap_jwt", "reader_jwt"] {
                if let Some(token) = state[name].as_str() {
                    message = message.replace(token, "[REDACTED]");
                }
            }
            eprintln!("Native catalog probe failed: {message}");
            std::process::exit(1);
        }
    }
}

async fn probe(state: &serde_json::Value, mode: &str) -> Result<(), Box<dyn Error>> {
    assert_eq!(state["data_path"], DATA_PATH);
    if matches!(mode,"seed"|"attach"|"extended"|"inline"|"advance"|"read_inline") { return seed::run(state, mode).await; }
    let initialize = mode == "initialize";
    assert!(initialize || mode == "read" || mode == "missing");
    let key = if initialize { "bootstrap_jwt" } else { "reader_jwt" };
    let catalog = QuackCatalog::try_new_with_ssl("quack:127.0.0.1:8792", state[key].as_str().ok_or("missing credential")?, false)?;
    catalog.connect().await?;
    let result = catalog.initialize_ducklake(DuckLakeCatalogInitOptions {
        data_path: Some(DATA_PATH.into()), create_if_not_exists: initialize,
        automatic_migration: false, override_data_path: false,
        ..Default::default()
    }).await;
    if mode == "missing" {
        let error = result.expect_err("registry creation must not initialize metadata");
        assert!(error.to_string().contains("Existing DuckLake catalog does not exist"), "unexpected missing metadata error: {error}");
        catalog.disconnect().await?;
        println!("PASS registry exists, actual DuckLake metadata is absent");
        return Ok(());
    }
    let metadata = result?;
    assert_eq!(metadata.data_path, DATA_PATH);
    let snapshots = catalog.snapshots().await?;
    let latest = snapshots.iter().max_by_key(|s| s.snapshot_id.0).ok_or("missing initial snapshot")?;
    let schemas = catalog.schemas_at(latest.snapshot_id).await?;
    let mut tables = 0;
    for schema in &schemas {
        tables += catalog.tables_at(schema.schema_id, latest.snapshot_id).await?.len();
    }
    if !initialize {
        let denied = catalog.commit_create_schema(CreateSchemaCommit {
            schema_name: "must_be_denied".into(), commit_message: "negative reader test".into(),
        }).await;
        let error = denied.expect_err("reader must not mutate metadata").to_string();
        assert!(error.contains("authoriz") || error.contains("denied") || error.contains("allow rule"), "unexpected denial: {error}");
    }
    catalog.disconnect().await?;
    println!("PASS {mode}: version={} snapshots={} schemas={} tables={} canonical_path_verified=true", metadata.version, snapshots.len(), schemas.len(), tables);
    Ok(())
}
