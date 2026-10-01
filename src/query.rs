#[cfg(feature = "ducklake")]
use crate::registry::CatalogRegistry;
use crate::{
    api::{ApiError, QueryRequest},
    config::Config,
    output::Output,
    policy,
};
use datafusion::{
    execution::{
        disk_manager::{DiskManagerBuilder, DiskManagerMode},
        memory_pool::GreedyMemoryPool,
        runtime_env::RuntimeEnvBuilder,
    },
    prelude::{SessionConfig, SessionContext},
};
#[cfg(feature = "ducklake")]
use datafusion_ducklake_provider::catalog::{
    DuckLakeCatalogOptions, register_ducklake_catalog_at_snapshot_with_options,
};
#[cfg(feature = "ducklake")]
use ducklake_catalog::SnapshotSelector;
#[cfg(feature = "ducklake")]
use ducklake_storage::{CatalogBackend, DuckLakeCatalogInitOptions, quack::QuackCatalog};
use futures_util::TryStreamExt;
#[cfg(feature = "ducklake")]
use std::time::Duration;
use std::{
    sync::{Arc, atomic::Ordering},
    time::Instant,
};
use worker::Env;

#[cfg(feature = "ducklake")]
async fn run_catalog(
    env: &Env,
    config: &Config,
    input: Option<QueryRequest>,
    id: &str,
    start: Instant,
) -> Result<Vec<u8>, ApiError> {
    let catalog = config.catalog.as_ref().ok_or_else(ApiError::config)?;
    let token = env
        .secret("QUACKLAKE_JWT")
        .map_err(|_| ApiError::config())?
        .to_string();
    if token.is_empty() || token.len() > 16384 {
        return Err(ApiError::config());
    }
    let backend = Arc::new(
        QuackCatalog::try_new_with_ssl(&catalog.uri, &token, catalog.ssl)
            .map_err(|_| ApiError::config())?,
    );
    let timeout = input
        .as_ref()
        .and_then(|i| i.timeout_ms)
        .unwrap_or(config.timeout_ms);
    let remaining = Duration::from_millis(timeout)
        .checked_sub(start.elapsed())
        .ok_or_else(ApiError::timeout)?;
    let operation = async {
        let metrics;
        let store: Arc<dyn object_store::ObjectStore> = match catalog.backend.as_str() {
            "r2_binding" => {
                let store = crate::r2_store::R2Store::new(
                    env.bucket("CATALOG_R2").map_err(|_| ApiError::config())?,
                    catalog.policy.clone(),
                );
                metrics = Some(store.metrics.clone());
                Arc::new(store)
            }
            "s3" => {
                let store = crate::storage::s3(env, catalog.policy.clone())
                    .map_err(|_| ApiError::config())?;
                metrics = Some(store.metrics.clone());
                store
            }
            _ => return Err(ApiError::config()),
        };
        let context = session(
            config,
            Arc::new(CatalogRegistry {
                bucket: catalog.policy.bucket.clone(),
                store,
            }),
        )?;
        let metadata = backend
            .initialize_ducklake(DuckLakeCatalogInitOptions {
                data_path: Some(catalog.path.clone()),
                create_if_not_exists: false,
                automatic_migration: false,
                override_data_path: false,
                ..Default::default()
            })
            .await
            .map_err(|_| ApiError::config())?;
        if metadata.data_path != catalog.path {
            return Err(ApiError::config());
        }
        // Freeze one snapshot for every table in this request; a later request discovers a new one.
        let latest = backend
            .snapshots()
            .await
            .map_err(|_| ApiError::config())?
            .into_iter()
            .max_by_key(|s| s.snapshot_id.0)
            .ok_or_else(ApiError::config)?
            .snapshot_id;
        register_ducklake_catalog_at_snapshot_with_options(
            &context,
            "lake",
            backend.clone(),
            SnapshotSelector::Version(latest),
            DuckLakeCatalogOptions { read_only: true },
        )
        .await
        .map_err(|_| ApiError::config())?;
        worker::console_log!(
            "{}",
            serde_json::json!({"request_id":id,"snapshot":latest.0.to_string()})
        );
        execute(&context, config, input, id, start, &[], metrics.as_deref()).await
    };
    let result = tokio::time::timeout(remaining, operation)
        .await
        .map_err(|_| ApiError::timeout())
        .and_then(|r| r);
    // Dropping the operation first drops its stream/store/session. This bounded cleanup runs on
    // errors and timeouts too, while the isolate permit is still held. No background tasks.
    let _ = tokio::time::timeout(Duration::from_millis(500), backend.disconnect()).await;
    result
}
pub(crate) fn execution_error(e: datafusion::error::DataFusionError) -> ApiError {
    use datafusion::error::DataFusionError;
    // These phrases come from our bounded storage/decoder adapters. Never return the upstream text.
    if e.to_string().contains("budget") {
        return ApiError::resource();
    }
    match e.find_root() {
        DataFusionError::ResourcesExhausted(_) => ApiError::resource(),
        DataFusionError::NotImplemented(_) => ApiError::unsupported(),
        _ => ApiError {
            status: 503,
            code: "QUERY_EXECUTION_FAILED",
            message: "Query source read failed",
        },
    }
}

pub async fn run(
    env: &Env,
    config: &Config,
    input: Option<QueryRequest>,
    id: &str,
    start: Instant,
) -> Result<Vec<u8>, ApiError> {
    if config.mode == crate::config::QueryMode::Files {
        return crate::files::run(env, config, input, id, start).await;
    }
    #[cfg(feature = "ducklake")]
    {
        run_catalog(env, config, input, id, start).await
    }
    #[cfg(not(feature = "ducklake"))]
    {
        Err(ApiError::config())
    }
}

pub fn session(
    config: &Config,
    registry: Arc<dyn datafusion::execution::object_store::ObjectStoreRegistry>,
) -> Result<SessionContext, ApiError> {
    let runtime = RuntimeEnvBuilder::new()
        .with_memory_pool(Arc::new(GreedyMemoryPool::new(config.memory_bytes)))
        .with_disk_manager_builder(
            DiskManagerBuilder::default().with_mode(DiskManagerMode::Disabled),
        )
        .with_object_store_registry(registry)
        .build_arc()
        .map_err(|_| ApiError::config())?;
    let mut options = SessionConfig::new()
        .with_target_partitions(1)
        .set_usize("datafusion.execution.meta_fetch_concurrency", 2)
        .with_batch_size(1024)
        .with_default_catalog_and_schema("lake", "main");
    options.options_mut().execution.collect_statistics = false;
    #[cfg(feature = "ducklake")]
    let options = if config.mode == crate::config::QueryMode::DuckLake {
        let mut extension = datafusion_ducklake_provider::DuckLakeConfig::default();
        extension.contain_paths = true;
        options.with_option_extension(extension)
    } else {
        options
    };
    Ok(SessionContext::new_with_config_rt(options, runtime))
}

pub async fn execute(
    context: &SessionContext,
    config: &Config,
    input: Option<QueryRequest>,
    id: &str,
    start: Instant,
    providers: &[Arc<dyn datafusion::catalog::TableProvider>],
    metrics: Option<&crate::r2_store::ReadMetrics>,
) -> Result<Vec<u8>, ApiError> {
    let Some(input) = input else {
        return serde_json::to_vec(&serde_json::json!({"request_id":id,"ready":true}))
            .map_err(|_| ApiError::resource());
    };
    let frame = context.sql(&input.sql).await.map_err(|e| {
        if e.to_string().contains("budget") {
            ApiError::resource()
        } else {
            ApiError::input("INVALID_QUERY", "Query could not be planned")
        }
    })?;
    policy::plan(frame.logical_plan(), config.mode, providers)?;
    let mut stream = frame.execute_stream().await.map_err(execution_error)?;
    let mut output = Output::new(
        id,
        &stream.schema(),
        input.max_rows.unwrap_or(config.rows),
        config.bytes,
    )?;
    'batches: while let Some(batch) = stream.try_next().await.map_err(execution_error)? {
        for i in 0..batch.num_rows() {
            if !output.row(&batch, i)? {
                break 'batches;
            }
            if i % 64 == 0 {
                tokio::task::yield_now().await;
            }
        }
        tokio::task::yield_now().await;
    }
    drop(stream);
    worker::console_log!(
        "{}",
        serde_json::json!({"request_id":id,
        "mode":if config.mode==crate::config::QueryMode::Files {"files"}else{"ducklake"},
        "row_count":output.rows,"truncated":output.truncated,
        "range_requests":metrics.map(|m|m.ranges.load(Ordering::Relaxed)),
        "peak_object_reads":metrics.map(|m|m.peak.load(Ordering::Relaxed)),
        "object_bytes":metrics.map(|m|m.bytes.load(Ordering::Relaxed))})
    );
    output.finish(start.elapsed().as_millis())
}
