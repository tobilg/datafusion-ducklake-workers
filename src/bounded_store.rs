//! Read-only prefix and resource boundary for the explicit S3 transport.
use crate::path_policy::PathPolicy;
use async_trait::async_trait;
use bytes::Bytes;
use futures_util::{
    StreamExt, TryStreamExt,
    stream::{self, BoxStream},
};
use object_store::{path::Path, *};
use std::{
    fmt,
    ops::Range,
    sync::{Arc, atomic::Ordering},
};
use tokio::sync::Semaphore;

#[derive(Debug, Clone)]
pub struct BoundedStore {
    pub inner: Arc<dyn ObjectStore>,
    pub policy: PathPolicy,
    permits: Arc<Semaphore>,
    pub metrics: Arc<crate::r2_store::ReadMetrics>,
}
struct Active(Arc<crate::r2_store::ReadMetrics>);
impl Drop for Active {
    fn drop(&mut self) {
        self.0.active.fetch_sub(1, Ordering::Relaxed);
    }
}
fn error(message: impl Into<String>) -> Error {
    Error::Generic {
        store: "bounded S3",
        source: message.into().into(),
    }
}
fn unsupported() -> Error {
    Error::NotSupported {
        source: "S3 transport is read-only".into(),
    }
}
impl fmt::Display for BoundedStore {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Bounded S3 ({})", self.policy.bucket)
    }
}
impl BoundedStore {
    pub fn new(inner: Arc<dyn ObjectStore>, policy: PathPolicy) -> Self {
        Self {
            inner,
            policy,
            permits: Arc::new(Semaphore::new(2)),
            metrics: Arc::default(),
        }
    }
    pub fn with_budget(
        mut self,
        permits: Arc<Semaphore>,
        metrics: Arc<crate::r2_store::ReadMetrics>,
    ) -> Self {
        self.permits = permits;
        self.metrics = metrics;
        self
    }
    fn key(&self, key: &Path) -> Result<()> {
        self.policy
            .key(key.as_ref())
            .map_err(|e| Error::PermissionDenied {
                path: key.to_string(),
                source: e.into(),
            })
    }
}
#[async_trait]
impl ObjectStore for BoundedStore {
    async fn get_opts(&self, key: &Path, mut opts: GetOptions) -> Result<GetResult> {
        self.key(key)?;
        if opts.version.is_some() {
            return Err(unsupported());
        }
        let permit = self
            .permits
            .clone()
            .acquire_owned()
            .await
            .map_err(|_| error("Read permit closed"))?;
        // HEAD validates the range before any body allocation, and pins the read's ETag.
        let meta = self.inner.head(key).await?;
        opts.check_preconditions(&meta)?;
        if self.policy.prefix.is_empty() && meta.size == 0 && key.as_ref().ends_with(".parquet") {
            return Err(error("Empty Parquet object"));
        }
        let range = if opts.head {
            0..0
        } else {
            match &opts.range {
                Some(r) => r.as_range(meta.size).map_err(|e| error(e.to_string()))?,
                None => 0..meta.size,
            }
        };
        if range.end - range.start > 4 * 1024 * 1024 {
            return Err(error("Read exceeds 4 MiB budget"));
        }
        if opts.head || range.is_empty() {
            return Ok(GetResult {
                meta,
                range,
                attributes: Attributes::default(),
                payload: GetResultPayload::Stream(stream::empty().boxed()),
            });
        }
        opts.if_match = meta.e_tag.clone();
        if opts.if_match.is_none() && meta.last_modified != chrono::DateTime::UNIX_EPOCH {
            opts.if_unmodified_since = Some(meta.last_modified);
        }
        opts.range = Some(GetRange::Bounded(range.clone()));
        let active = self.metrics.active.fetch_add(1, Ordering::Relaxed) + 1;
        self.metrics.peak.fetch_max(active, Ordering::Relaxed);
        let active = Active(self.metrics.clone());
        let result = self.inner.get_opts(key, opts).await?;
        if result.range != range
            || result.meta.e_tag != meta.e_tag
            || result.meta.size != meta.size
            || result.meta.last_modified != meta.last_modified
        {
            return Err(error("S3 range/ETag mismatch"));
        }
        let end = range.end - range.start;
        self.metrics.ranges.fetch_add(1, Ordering::Relaxed);
        let payload = stream::try_unfold(
            (result.into_stream(), permit, active, 0u64),
            move |(mut body, permit, active, read)| async move {
                match body.try_next().await? {
                    Some(bytes) => {
                        let total = read + bytes.len() as u64;
                        if total > end {
                            return Err(error("S3 body exceeds declared range"));
                        }
                        active
                            .0
                            .bytes
                            .fetch_add(bytes.len() as u64, Ordering::Relaxed);
                        Ok(Some((bytes, (body, permit, active, total))))
                    }
                    None if read == end => Ok(None),
                    None => Err(error("S3 response was truncated")),
                }
            },
        )
        .boxed();
        Ok(GetResult {
            meta,
            range,
            attributes: Attributes::default(),
            payload: GetResultPayload::Stream(payload),
        })
    }
    async fn get_ranges(&self, key: &Path, ranges: &[Range<u64>]) -> Result<Vec<Bytes>> {
        let size = ranges
            .iter()
            .try_fold(0u64, |s, r| {
                r.end.checked_sub(r.start).and_then(|n| s.checked_add(n))
            })
            .ok_or_else(|| error("Invalid ranges"))?;
        if ranges.len() > 128 || size > 8 * 1024 * 1024 {
            return Err(error("Multi-range budget exceeded"));
        }
        stream::iter(ranges.iter().cloned().map(|r| async move {
            self.get_opts(key, GetOptions::new().with_range(Some(r)))
                .await?
                .bytes()
                .await
        }))
        .buffered(2)
        .try_collect()
        .await
    }
    fn list(&self, prefix: Option<&Path>) -> BoxStream<'static, Result<ObjectMeta>> {
        let prefix = match self.policy.list_prefix(prefix.map(AsRef::as_ref)) {
            Ok(p) => Path::from(p),
            Err(e) => return stream::once(async move { Err(error(e)) }).boxed(),
        };
        let policy = self.policy.clone();
        let inner = self.inner.clone();
        let permits = self.permits.clone();
        let metrics = self.metrics.clone();
        stream::once(async move {
            let permit = permits
                .acquire_owned()
                .await
                .map_err(|_| error("Read permit closed"))?;
            let entries = inner.list(Some(&prefix));
            Ok::<_, Error>(stream::try_unfold(
                (entries, permit),
                move |(mut entries, permit)| {
                    let policy = policy.clone();
                    let metrics = metrics.clone();
                    async move {
                        match entries.try_next().await? {
                            Some(item) => {
                                if metrics.listed.fetch_add(1, Ordering::Relaxed) >= 8192 {
                                    return Err(error("Request listing budget exceeded"));
                                }
                                policy.key(item.location.as_ref()).map_err(error)?;
                                if policy.prefix.is_empty()
                                    && item.size == 0
                                    && item.location.as_ref().ends_with(".parquet")
                                {
                                    return Err(error("Empty Parquet object"));
                                }
                                Ok(Some((item, (entries, permit))))
                            }
                            None => Ok(None),
                        }
                    }
                },
            ))
        })
        .try_flatten()
        .boxed()
    }

    async fn list_with_delimiter(&self, prefix: Option<&Path>) -> Result<ListResult> {
        // Stream the bounded listing instead of buffering an unbounded S3 delimiter result.
        let raw = self
            .policy
            .list_prefix(prefix.map(AsRef::as_ref))
            .map_err(error)?;
        let mut entries = self.list(prefix);
        let mut objects = vec![];
        let mut dirs = std::collections::BTreeSet::new();
        while let Some(item) = entries.try_next().await? {
            let tail = item
                .location
                .as_ref()
                .strip_prefix(&raw)
                .ok_or_else(|| error("Listing escaped prefix"))?;
            if let Some((dir, _)) = tail.split_once('/') {
                dirs.insert(Path::from(format!("{raw}{dir}")));
            } else {
                objects.push(item);
            }
        }
        Ok(ListResult {
            objects,
            common_prefixes: dirs.into_iter().collect(),
        })
    }
    async fn put_opts(&self, _: &Path, _: PutPayload, _: PutOptions) -> Result<PutResult> {
        Err(unsupported())
    }
    async fn put_multipart_opts(
        &self,
        _: &Path,
        _: PutMultipartOptions,
    ) -> Result<Box<dyn MultipartUpload>> {
        Err(unsupported())
    }
    fn delete_stream(
        &self,
        locations: BoxStream<'static, Result<Path>>,
    ) -> BoxStream<'static, Result<Path>> {
        locations.map(|_| Err(unsupported())).boxed()
    }
    async fn copy_opts(&self, _: &Path, _: &Path, _: CopyOptions) -> Result<()> {
        Err(unsupported())
    }
    async fn rename_opts(&self, _: &Path, _: &Path, _: RenameOptions) -> Result<()> {
        Err(unsupported())
    }
}
