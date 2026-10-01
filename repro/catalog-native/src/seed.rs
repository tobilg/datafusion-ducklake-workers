//! The writer exists only in the native fixture tool, never in the query Worker.
use datafusion_ducklake_provider::{DuckLakeConfig, DuckLakeSessionContext, datafusion::{
    execution::{runtime_env::RuntimeEnvBuilder, object_store::ObjectStoreUrl},
    prelude::SessionConfig,
}};
use futures_util::TryStreamExt;
use std::{error::Error, path::Path, sync::Arc};

pub async fn run(state: &serde_json::Value, mode: &str) -> Result<(), Box<dyn Error>> {
    let seed=!matches!(mode,"attach"|"read_inline");
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.cache/fixture-objects");
    std::fs::create_dir_all(&root)?;
    let runtime = RuntimeEnvBuilder::new().build_arc()?;
    // The physical fixture store contains the exact keys referenced by metadata.
    let store = object_store::local::LocalFileSystem::new_with_prefix(&root)?;
    runtime.register_object_store(ObjectStoreUrl::parse("r2://quacklake-fixture")?.as_ref(), Arc::new(store));
    let mut ducklake = DuckLakeConfig::default();
    ducklake.contain_paths = true;
    ducklake.default_data_inlining_row_limit = if matches!(mode,"inline"|"advance") {1000} else {0};
    let context = DuckLakeSessionContext::new_with_config_rt(
        SessionConfig::new().with_target_partitions(1).with_batch_size(1024).with_option_extension(ducklake), runtime);
    let token_key = if seed { "bootstrap_jwt" } else { "reader_jwt" };
    let token = state[token_key].as_str().ok_or("missing fixture credential")?.replace('\'', "''");
    let read_only = if seed { "READ_WRITE" } else { "READ_ONLY" };
    // Regression: the provider strips ducklake:quack:, leaving the Quack URI.
    let uri = "quack:127.0.0.1:8792";
    let location = format!("ducklake:quack:{uri}");
    assert_eq!(location, "ducklake:quack:quack:127.0.0.1:8792");
    let sql = format!("ATTACH '{location}' AS lake ({read_only}, CREATE_IF_NOT_EXISTS false, TOKEN '{token}', SSL false, DATA_PATH '{}')", super::DATA_PATH);
    context.sql(&sql).await?;
    assert!(context.catalog("lake").is_some());
    if mode=="read_inline" {
        let batches=context.sql("SELECT id FROM lake.main.inline_probe WHERE id=1 AND label IS NULL").await?.collect().await?;
        assert_eq!(batches.iter().map(|b|b.num_rows()).sum::<usize>(),1,"Older inlined row must survive unrelated schema changes");
        println!("PASS native historical inlined row after unrelated schema changes");
    } else if matches!(mode,"inline"|"advance") {
        let statement=if mode=="inline" {
            "CREATE TABLE lake.main.inline_probe AS SELECT CAST(1 AS BIGINT) AS id, CAST(NULL AS VARCHAR) AS label".to_string()
        } else {
            let table=std::env::args().nth(2).ok_or("missing unique snapshot fixture table")?;
            assert!(table.starts_with("snapshot_") && table.chars().all(|c|c.is_ascii_alphanumeric()||c=='_'));
            format!("CREATE TABLE lake.main.{table} AS SELECT CAST(7 AS BIGINT) AS id, CAST(NULL AS VARCHAR) AS label")
        };
        let mut stream=context.sql(&statement).await?.execute_stream().await?;
        while stream.try_next().await?.is_some() {}
        println!("PASS {mode}: local inlined fixture metadata committed");
    } else if mode=="extended" {
        super::extended::run(&context,state,&root).await?;
    } else if seed {
        // Never silently replace existing tables; re-seeding is an operator action.
        for statement in [
            "CREATE TABLE lake.main.sales AS SELECT id, CAST(id AS BIGINT) + 9007199254740992 AS large_int, CAST(id * 1.25 AS DECIMAL(18,2)) AS amount, CASE WHEN id % 3 = 0 THEN NULL ELSE 'Grüße 🌍' END AS label, TIMESTAMP '2026-09-01 12:34:56' AS happened_at, id % 4 AS category FROM generate_series(1, 5000) AS t(id)",
            "CREATE TABLE lake.main.categories AS SELECT id, 'group-' || CAST(id AS VARCHAR) AS name FROM generate_series(0, 3) AS t(id)",
            "DELETE FROM lake.main.sales WHERE id % 10 = 0",
        ] {
            let mut stream = context.sql(statement).await?.execute_stream().await?;
            while stream.try_next().await?.is_some() {}
        }
        println!("PASS seed: external Parquet tables and row deletions committed through the provider");
    } else {
        println!("PASS layered attach: ducklake:quack:quack:127.0.0.1:8792 with reader policy");
    }
    context.sql("DETACH lake").await?;
    Ok(())
}
