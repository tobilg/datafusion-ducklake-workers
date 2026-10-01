use datafusion_ducklake_provider::catalog::{
    DuckLakeCatalogOptions, register_ducklake_catalog_at_snapshot_with_options,
};
use ducklake_catalog::SnapshotSelector;
use ducklake_storage::{CatalogBackend, DuckLakeCatalogInitOptions, quack::QuackCatalog};
use futures_util::TryStreamExt;
use std::{sync::Arc, time::Duration};
use worker::{Env, Response, Result};

pub async fn run(env: &Env, use_sql_wrapper: bool) -> Result<Response> {
    let token = env.secret("QUACKLAKE_JWT")?.to_string();
    let uri = env.var("QUACK_URI")?.to_string();
    let expected_path = env.var("CATALOG_DATA_PATH")?.to_string();
    // This probe permits plain HTTP solely for the fixed local fixture.
    let ssl = uri != "quack:127.0.0.1:8792";
    let backend = Arc::new(
        QuackCatalog::try_new_with_ssl(&uri, &token, ssl)
            .map_err(|_| worker::Error::RustError("Invalid catalog configuration".into()))?,
    );
    let operation = async {
        let policy =
            crate::path_policy::PathPolicy::new("quacklake-fixture", "fixture", &expected_path)?;
        let mode = env
            .var("STORAGE_BACKEND")
            .map(|v| v.to_string())
            .unwrap_or_else(|_| "r2".into());
        let mut metrics = None;
        let store: Arc<dyn object_store::ObjectStore> = match mode.as_str() {
            "r2_binding" => {
                let store = crate::r2_store::R2Store::new(env.bucket("CATALOG_R2")?, policy);
                metrics = Some(store.metrics.clone());
                Arc::new(store)
            }
            "s3" => crate::storage::s3(env, policy)?,
            _ => return Err("Invalid backend".into()),
        };
        let context =
            super::session_with_store(Some(Arc::new(crate::registry::CatalogRegistry {
                bucket: "quacklake-fixture".into(),
                store,
            })))?;
        let metadata = backend
            .initialize_ducklake(DuckLakeCatalogInitOptions {
                data_path: Some(expected_path.clone()),
                create_if_not_exists: false,
                automatic_migration: false,
                override_data_path: false,
                ..Default::default()
            })
            .await?;
        if metadata.data_path != expected_path {
            return Err("Canonical path mismatch".into());
        }
        let snapshots = backend.snapshots().await?;
        let latest = snapshots
            .iter()
            .max_by_key(|s| s.snapshot_id.0)
            .ok_or("missing snapshot")?
            .snapshot_id;
        register_ducklake_catalog_at_snapshot_with_options(
            &context,
            "lake",
            backend.clone(),
            SnapshotSelector::Version(latest),
            DuckLakeCatalogOptions { read_only: true },
        )
        .await?;
        #[cfg(feature = "protocol-probe")]
        if use_sql_wrapper {
            let wrapper = datafusion_ducklake_provider::DuckLakeSessionContext::from_context(
                super::session()?,
            );
            let quote = |s: &str| s.replace('\'', "''");
            let sql = format!(
                "ATTACH 'ducklake:quack:{}' AS lake (READ_ONLY, CREATE_IF_NOT_EXISTS false, TOKEN '{}', SSL {ssl}, DATA_PATH '{}')",
                quote(&uri),
                quote(&token),
                quote(&expected_path)
            );
            // Never log this internal token-bearing statement.
            wrapper.sql(&sql).await?;
            if wrapper.catalog("lake").is_none() {
                return Err("SQL wrapper did not attach lake".into());
            }
        }
        let lake = context.catalog("lake").ok_or("catalog not registered")?;
        let schemas = lake.schema_names();
        let mut stream = context
            .sql("SELECT COUNT(*) AS rows, SUM(id) AS sum_id FROM lake.main.sales")
            .await?
            .execute_stream()
            .await?;
        let batch = stream.try_next().await?.ok_or("no aggregate batch")?;
        let rows = batch
            .column(0)
            .as_any()
            .downcast_ref::<datafusion::arrow::array::Int64Array>()
            .ok_or("wrong count type")?
            .value(0);
        let sum_id = batch
            .column(1)
            .as_any()
            .downcast_ref::<datafusion::arrow::array::Int64Array>()
            .ok_or("wrong sum type")?
            .value(0);
        if rows != 4500 || sum_id != 11250000 {
            return Err("Parquet/deletion aggregate mismatch".into());
        }
        Ok::<_, Box<dyn std::error::Error>>(serde_json::json!({
            "version": metadata.version, "snapshot": latest.0.to_string(),
            "schemas": schemas, "canonical_path_verified": true,
            "sql_wrapper_tested": use_sql_wrapper, "rows": rows.to_string(), "sum_id": sum_id.to_string(),
            "backend":mode,
            "range_requests": metrics.as_ref().map(|m|m.ranges.load(std::sync::atomic::Ordering::Relaxed)),
            "object_bytes": metrics.as_ref().map(|m|m.bytes.load(std::sync::atomic::Ordering::Relaxed))
        }))
    };
    let result = tokio::time::timeout(Duration::from_secs(10), operation).await;
    // The typed backend is request-scoped. Do not keep its authenticated session.
    let _ = tokio::time::timeout(Duration::from_millis(500), backend.disconnect()).await;
    match result {
        Ok(Ok(value)) => Response::from_json(&value),
        Ok(Err(error)) => {
            let sanitized = error.to_string().replace(&token, "[REDACTED]");
            worker::console_error!("Catalog probe failed: {}", sanitized);
            Response::error("Catalog probe failed", 503)
        }
        Err(_) => Response::error("Catalog probe timed out", 504),
    }
}
