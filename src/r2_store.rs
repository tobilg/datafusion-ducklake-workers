//! Single-threaded Worker-only ObjectStore 0.13.2 adapter. No custom unsafe code.
//! JS handles remain request-owned; SDK SendWrapper/SendFuture bridge trait bounds.
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
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
};
use tokio::sync::Semaphore;
use worker::{
    Bucket, Conditional, Object,
    send::{SendFuture, SendWrapper},
};

const CHUNK_BYTES: u64 = 1024 * 1024;
pub const MAX_READ_BYTES: u64 = 4 * 1024 * 1024;
const MAX_MULTI_BYTES: u64 = 8 * 1024 * 1024;
const MAX_LIST_PAGES: usize = 64;

#[derive(Default, Debug)]
pub struct ReadMetrics {
    pub ranges: AtomicU64,
    pub bytes: AtomicU64,
    pub listed: AtomicU64,
    pub active: AtomicU64,
    pub peak: AtomicU64,
}
struct ReadGuard(Arc<ReadMetrics>);
impl Drop for ReadGuard {
    fn drop(&mut self) {
        self.0.active.fetch_sub(1, Ordering::Relaxed);
    }
}

#[derive(Clone)]
pub struct R2Store {
    bucket: Arc<SendWrapper<Bucket>>,
    policy: PathPolicy,
    permits: Arc<Semaphore>,
    pub metrics: Arc<ReadMetrics>,
}
impl fmt::Debug for R2Store {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("R2Store")
            .field("policy", &self.policy)
            .finish_non_exhaustive()
    }
}
impl fmt::Display for R2Store {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "R2 binding ({})", self.policy.bucket)
    }
}
fn generic(message: impl Into<String>) -> Error {
    Error::Generic {
        store: "R2",
        source: message.into().into(),
    }
}
fn unsupported(op: &str) -> Error {
    Error::NotSupported {
        source: format!("Read-only R2 adapter does not support {op}").into(),
    }
}
fn missing(key: &str) -> Error {
    Error::NotFound {
        path: key.into(),
        source: "R2 object not found".into(),
    }
}
fn upstream(error: worker::Error, key: &str) -> Error {
    let message = error.to_string();
    // The SDK exposes R2 failures as JS errors, not a typed HTTP status.
    if message.contains("403") || message.contains("unauthorized") || message.contains("Forbidden")
    {
        Error::PermissionDenied {
            path: key.into(),
            source: "R2 binding denied access".into(),
        }
    } else {
        generic("R2 operation failed (upstream or transient error)")
    }
}

impl R2Store {
    pub fn new(bucket: Bucket, policy: PathPolicy) -> Self {
        Self {
            bucket: Arc::new(SendWrapper::new(bucket)),
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
    fn validate(&self, key: &Path) -> Result<()> {
        self.policy
            .key(key.as_ref())
            .map_err(|e| Error::PermissionDenied {
                path: key.to_string(),
                source: e.into(),
            })
    }
    fn meta(&self, object: &Object) -> Result<ObjectMeta> {
        let key = Path::parse(object.key())?;
        self.validate(&key)?;
        let size = object.size();
        if self.policy.prefix.is_empty() && size == 0 && key.as_ref().ends_with(".parquet") {
            return Err(generic("Empty Parquet object"));
        }
        if size > (1_u64 << 53) - 1 {
            return Err(generic("Object exceeds JS integer precision"));
        }
        let millis = i64::try_from(object.uploaded().as_millis())
            .map_err(|_| generic("Invalid upload time"))?;
        Ok(ObjectMeta {
            location: key,
            size,
            last_modified: chrono::DateTime::from_timestamp_millis(millis)
                .ok_or_else(|| generic("Invalid upload time"))?,
            e_tag: Some(object.http_etag()),
            version: Some(object.version()),
        })
    }
    async fn head_meta(&self, key: &Path) -> Result<ObjectMeta> {
        self.validate(key)?;
        SendFuture::new(async {
            let _permit = self
                .permits
                .acquire()
                .await
                .map_err(|_| generic("Read semaphore closed"))?;
            let object = self
                .bucket
                .head(key.as_ref())
                .await
                .map_err(|e| upstream(e, key.as_ref()))?
                .ok_or_else(|| missing(key.as_ref()))?;
            self.meta(&object)
        })
        .await
    }
    async fn chunk(&self, key: &Path, range: Range<u64>, etag: &str) -> Result<Bytes> {
        SendFuture::new(async {
            let _permit = self
                .permits
                .acquire()
                .await
                .map_err(|_| generic("Read semaphore closed"))?;
            let active = self.metrics.active.fetch_add(1, Ordering::Relaxed) + 1;
            self.metrics.peak.fetch_max(active, Ordering::Relaxed);
            let _active = ReadGuard(self.metrics.clone());
            let object = self
                .bucket
                .get(key.as_ref())
                .range(worker::Range::OffsetWithLength {
                    offset: range.start,
                    length: range.end - range.start,
                })
                .only_if(Conditional {
                    etag_matches: Some(etag.trim_matches('"').into()),
                    ..Default::default()
                })
                .execute()
                .await
                .map_err(|e| upstream(e, key.as_ref()))?
                .ok_or_else(|| missing(key.as_ref()))?;
            let body = object.body().ok_or_else(|| Error::Precondition {
                path: key.to_string(),
                source: "Object changed during ranged read".into(),
            })?;
            match object
                .range()
                .map_err(|_| generic("R2 omitted range metadata"))?
            {
                worker::Range::OffsetWithLength { offset, length }
                    if offset == range.start && length == range.end - range.start =>
                {
                    ()
                }
                _ => return Err(generic("R2 returned an unexpected range")),
            }
            // At most 1 MiB is copied from JS per call; never fetch then slice a large object.
            let bytes = body.bytes().await.map_err(|e| upstream(e, key.as_ref()))?;
            if bytes.len() as u64 != range.end - range.start {
                return Err(generic("R2 range length mismatch"));
            }
            self.metrics.ranges.fetch_add(1, Ordering::Relaxed);
            self.metrics
                .bytes
                .fetch_add(bytes.len() as u64, Ordering::Relaxed);
            Ok(Bytes::from(bytes))
        })
        .await
    }
    async fn page(
        &self,
        prefix: &str,
        cursor: Option<String>,
        delimiter: bool,
    ) -> Result<(Vec<ObjectMeta>, Vec<Path>, Option<String>)> {
        SendFuture::new(async {
            let _permit = self
                .permits
                .acquire()
                .await
                .map_err(|_| generic("Read semaphore closed"))?;
            let mut request = self.bucket.list().prefix(prefix).limit(128);
            if let Some(cursor) = cursor {
                request = request.cursor(cursor);
            }
            if delimiter {
                request = request.delimiter("/");
            }
            let page = request.execute().await.map_err(|e| upstream(e, prefix))?;
            let count = page.objects().len() as u64;
            if self
                .metrics
                .listed
                .fetch_add(count, Ordering::Relaxed)
                .saturating_add(count)
                > 8192
            {
                return Err(generic("Request listing budget exceeded"));
            }
            let objects = page
                .objects()
                .iter()
                .map(|o| self.meta(o))
                .collect::<Result<Vec<_>>>()?;
            let prefixes = page
                .delimited_prefixes()
                .into_iter()
                .map(|p| {
                    self.policy.list_prefix(Some(&p)).map_err(generic)?;
                    Path::parse(p).map_err(Error::from)
                })
                .collect::<Result<Vec<_>>>()?;
            let cursor = if page.truncated() {
                Some(
                    page.cursor()
                        .ok_or_else(|| generic("Missing R2 listing cursor"))?,
                )
            } else {
                None
            };
            Ok((objects, prefixes, cursor))
        })
        .await
    }
}

#[async_trait]
impl ObjectStore for R2Store {
    async fn get_opts(&self, key: &Path, options: GetOptions) -> Result<GetResult> {
        if options.version.is_some() {
            return Err(unsupported("historical version reads"));
        }
        let meta = self.head_meta(key).await?;
        options.check_preconditions(&meta)?;
        let range = if options.head {
            0..0
        } else {
            match options.range {
                Some(r) => r.as_range(meta.size).map_err(|e| generic(e.to_string()))?,
                None => 0..meta.size,
            }
        };
        if !options.head && range.end - range.start > MAX_READ_BYTES {
            return Err(generic("Object read exceeds 4 MiB range budget"));
        }
        let etag = meta
            .e_tag
            .clone()
            .ok_or_else(|| generic("R2 metadata missing ETag"))?;
        let store = self.clone();
        let key = key.clone();
        let end = if options.head { range.start } else { range.end };
        let payload = stream::try_unfold(
            (store, key, etag, range.start),
            move |(store, key, etag, offset)| async move {
                if offset >= end {
                    return Ok(None);
                }
                let next = offset.saturating_add(CHUNK_BYTES).min(end);
                let data = store.chunk(&key, offset..next, &etag).await?;
                Ok(Some((data, (store, key, etag, next))))
            },
        )
        .boxed();
        Ok(GetResult {
            payload: GetResultPayload::Stream(payload),
            meta,
            range,
            attributes: Attributes::default(),
        })
    }
    async fn get_ranges(&self, key: &Path, ranges: &[Range<u64>]) -> Result<Vec<Bytes>> {
        let size = ranges
            .iter()
            .try_fold(0_u64, |sum, r| {
                r.end.checked_sub(r.start).and_then(|n| sum.checked_add(n))
            })
            .ok_or_else(|| generic("Invalid multi-range request"))?;
        if ranges.len() > 128 || size > MAX_MULTI_BYTES {
            return Err(generic("Multi-range read exceeds budget"));
        }
        // Preserve order; bounded per-call and across all concurrent calls.
        stream::iter(ranges.iter().cloned().map(|range| async move {
            self.get_opts(key, GetOptions::new().with_range(Some(range)))
                .await?
                .bytes()
                .await
        }))
        .buffered(2)
        .try_collect()
        .await
    }
    fn list(&self, prefix: Option<&Path>) -> BoxStream<'static, Result<ObjectMeta>> {
        let prefix = self.policy.list_prefix(prefix.map(AsRef::as_ref));
        let store = self.clone();
        stream::try_unfold(
            (store, prefix, None, 0, false),
            |(store, prefix, cursor, pages, done)| async move {
                if done {
                    return Ok(None);
                }
                let prefix = prefix.map_err(generic)?;
                if pages >= MAX_LIST_PAGES {
                    return Err(generic("Listing exceeds 8192-entry budget"));
                }
                let (objects, _, next) = store.page(&prefix, cursor.clone(), false).await?;
                if next.is_some() && next == cursor {
                    return Err(generic("R2 listing cursor did not advance"));
                }
                let done = next.is_none();
                Ok(Some((
                    stream::iter(objects.into_iter().map(Ok)),
                    (store, Ok(prefix), next, pages + 1, done),
                )))
            },
        )
        .try_flatten()
        .boxed()
    }
    async fn list_with_delimiter(&self, prefix: Option<&Path>) -> Result<ListResult> {
        let prefix = self
            .policy
            .list_prefix(prefix.map(AsRef::as_ref))
            .map_err(generic)?;
        let mut result = ListResult {
            common_prefixes: vec![],
            objects: vec![],
        };
        let mut cursor = None;
        for _ in 0..MAX_LIST_PAGES {
            let (objects, prefixes, next) = self.page(&prefix, cursor.clone(), true).await?;
            result.objects.extend(objects);
            result.common_prefixes.extend(prefixes);
            if next.is_none() {
                return Ok(result);
            }
            if next == cursor {
                return Err(generic("R2 listing cursor did not advance"));
            }
            cursor = next;
        }
        Err(generic("Listing exceeds 8192-entry budget"))
    }
    async fn put_opts(&self, _: &Path, _: PutPayload, _: PutOptions) -> Result<PutResult> {
        Err(unsupported("put"))
    }
    async fn put_multipart_opts(
        &self,
        _: &Path,
        _: PutMultipartOptions,
    ) -> Result<Box<dyn MultipartUpload>> {
        Err(unsupported("multipart upload"))
    }
    fn delete_stream(
        &self,
        locations: BoxStream<'static, Result<Path>>,
    ) -> BoxStream<'static, Result<Path>> {
        locations.map(|_| Err(unsupported("delete"))).boxed()
    }
    async fn copy_opts(&self, _: &Path, _: &Path, _: CopyOptions) -> Result<()> {
        Err(unsupported("copy"))
    }
    async fn rename_opts(&self, _: &Path, _: &Path, _: RenameOptions) -> Result<()> {
        Err(unsupported("rename"))
    }
}
