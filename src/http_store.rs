//! One exact HTTP URL per store; query strings never enter object keys or Debug output.
//! BoundedStore supplies concurrency/copy budgets. No write or directory operations.
use async_trait::async_trait;
use futures_util::{
    StreamExt, TryStreamExt,
    stream::{self, BoxStream},
};
use object_store::{path::Path, *};
use reqwest::{Method, Response, header};
use std::fmt;
use url::Url;

pub struct HttpFile {
    url: Url,
    client: reqwest::Client,
    local: bool,
}
impl fmt::Debug for HttpFile {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("HttpFile(<redacted>)")
    }
}
impl fmt::Display for HttpFile {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("HTTPS file")
    }
}
fn error(message: &'static str) -> Error {
    Error::Generic {
        store: "HTTPS file",
        source: message.into(),
    }
}
fn unsupported() -> Error {
    Error::NotSupported {
        source: "Read-only HTTPS file operation".into(),
    }
}
impl HttpFile {
    pub fn new(url: Url, local: bool) -> Result<Self> {
        crate::network::validate_url(&url, local).map_err(error)?;
        let client =
            crate::network::client(local).map_err(|_| error("HTTP client configuration"))?;
        Ok(Self { url, client, local })
    }
    async fn request(
        &self,
        method: Method,
        range: Option<String>,
        opts: &GetOptions,
    ) -> Result<Response> {
        let mut url = self.url.clone();
        for redirects in 0..=3 {
            crate::network::validate_url(&url, self.local).map_err(error)?;
            let mut req = self
                .client
                .request(method.clone(), url.clone())
                .header(header::ACCEPT_ENCODING, "identity");
            if let Some(value) = &range {
                req = req.header(header::RANGE, value);
            }
            if let Some(value) = &opts.if_match {
                req = req.header(header::IF_MATCH, value);
            }
            if let Some(value) = &opts.if_none_match {
                req = req.header(header::IF_NONE_MATCH, value);
            }
            if let Some(value) = opts.if_unmodified_since {
                req = req.header(
                    header::IF_UNMODIFIED_SINCE,
                    value.format("%a, %d %b %Y %H:%M:%S GMT").to_string(),
                );
            }
            if let Some(value) = opts.if_modified_since {
                req = req.header(
                    header::IF_MODIFIED_SINCE,
                    value.format("%a, %d %b %Y %H:%M:%S GMT").to_string(),
                );
            }
            let response = req
                .send()
                .await
                .map_err(|_| error("HTTP source unavailable"))?;
            if matches!(response.status().as_u16(), 301 | 302 | 303 | 307 | 308) {
                if redirects == 3 {
                    return Err(error("HTTP redirect budget exceeded"));
                }
                let location = response
                    .headers()
                    .get(header::LOCATION)
                    .and_then(|v| v.to_str().ok())
                    .ok_or_else(|| error("Missing redirect location"))?;
                // Resolve against the current URL, preserving signed query parameters as supplied
                // by the server. Never copy credentials or queries to a different destination.
                url = url.join(location).map_err(|_| error("Invalid redirect"))?;
                continue;
            }
            return Ok(response);
        }
        Err(error("HTTP redirect budget exceeded"))
    }
    fn status(response: &Response) -> Result<()> {
        match response.status().as_u16() {
            200 | 206 => Ok(()),
            404 => Err(Error::NotFound {
                path: "data.parquet".into(),
                source: "HTTP object missing".into(),
            }),
            401 | 403 => Err(Error::PermissionDenied {
                path: "data.parquet".into(),
                source: "HTTP object denied".into(),
            }),
            412 => Err(Error::Precondition {
                path: "data.parquet".into(),
                source: "HTTP object changed".into(),
            }),
            304 => Err(Error::NotModified {
                path: "data.parquet".into(),
                source: "HTTP object unmodified".into(),
            }),
            _ => Err(error("HTTP source returned an unexpected status")),
        }
    }
    fn content_range(response: &Response) -> Result<(std::ops::Range<u64>, u64)> {
        let value = response
            .headers()
            .get(header::CONTENT_RANGE)
            .and_then(|h| h.to_str().ok())
            .ok_or_else(|| error("HTTP range metadata missing"))?;
        let (range, size) = value
            .strip_prefix("bytes ")
            .and_then(|s| s.split_once('/'))
            .ok_or_else(|| error("Invalid HTTP range"))?;
        let (start, end) = range
            .split_once('-')
            .ok_or_else(|| error("Invalid HTTP range"))?;
        let start = start
            .parse::<u64>()
            .map_err(|_| error("Invalid HTTP range"))?;
        let end = end
            .parse::<u64>()
            .ok()
            .and_then(|n| n.checked_add(1))
            .ok_or_else(|| error("Invalid HTTP range"))?;
        let size = size
            .parse::<u64>()
            .map_err(|_| error("Invalid HTTP size"))?;
        if start >= end || end > size {
            return Err(error("Invalid HTTP range"));
        }
        Ok((start..end, size))
    }
    fn metadata(response: &Response, size: u64) -> Result<ObjectMeta> {
        if response
            .headers()
            .get(header::CONTENT_ENCODING)
            .is_some_and(|v| v != "identity")
        {
            return Err(error("Encoded HTTP body unsupported"));
        }
        let e_tag = response
            .headers()
            .get(header::ETAG)
            .and_then(|v| v.to_str().ok())
            .map(str::to_owned);
        let last_modified = match response.headers().get(header::LAST_MODIFIED) {
            None => chrono::DateTime::UNIX_EPOCH,
            Some(v) => chrono::DateTime::parse_from_rfc2822(
                v.to_str().map_err(|_| error("Invalid HTTP timestamp"))?,
            )
            .map_err(|_| error("Invalid HTTP timestamp"))?
            .to_utc(),
        };
        Ok(ObjectMeta {
            location: Path::from("data.parquet"),
            size,
            last_modified,
            e_tag,
            version: None,
        })
    }
}
#[async_trait]
impl ObjectStore for HttpFile {
    async fn get_opts(&self, key: &Path, opts: GetOptions) -> Result<GetResult> {
        if key.as_ref() != "data.parquet" || opts.version.is_some() {
            return Err(unsupported());
        }
        if opts.head {
            let mut response = self.request(Method::HEAD, None, &opts).await?;
            let size = if response.status().is_success()
                && response.headers().contains_key(header::CONTENT_LENGTH)
            {
                response.headers()[header::CONTENT_LENGTH]
                    .to_str()
                    .ok()
                    .and_then(|v| v.parse::<u64>().ok())
                    .ok_or_else(|| error("Invalid HTTP size"))?
            } else {
                // Signed GET URLs and servers without HEAD can still provide bounded metadata.
                response = self
                    .request(Method::GET, Some("bytes=0-0".into()), &opts)
                    .await?;
                Self::status(&response)?;
                if response.status().as_u16() != 206 {
                    return Err(error("HTTP ranges unsupported"));
                }
                let (range, size) = Self::content_range(&response)?;
                if range != (0..1) {
                    return Err(error("Incorrect HTTP probe range"));
                }
                size
            };
            Self::status(&response)?;
            let meta = Self::metadata(&response, size)?;
            opts.check_preconditions(&meta)?;
            return Ok(GetResult {
                meta,
                range: 0..0,
                attributes: Attributes::default(),
                payload: GetResultPayload::Stream(stream::empty().boxed()),
            });
        }
        // The outer read boundary always supplies a bounded range, including whole small files.
        let Some(GetRange::Bounded(range)) = opts.range.clone() else {
            return Err(unsupported());
        };
        if range.is_empty() || range.end - range.start > 4 * 1024 * 1024 {
            return Err(error("HTTP range budget exceeded"));
        }
        let response = self
            .request(
                Method::GET,
                Some(format!("bytes={}-{}", range.start, range.end - 1)),
                &opts,
            )
            .await?;
        Self::status(&response)?;
        if response.status().as_u16() != 206 {
            return Err(error("HTTP server ignored range"));
        }
        let (actual, size) = Self::content_range(&response)?;
        if range != actual {
            return Err(error("HTTP range mismatch"));
        }
        let meta = Self::metadata(&response, size)?;
        opts.check_preconditions(&meta)?;
        let expected = range.end - range.start;
        let payload = stream::try_unfold(
            (response.bytes_stream(), 0u64),
            move |(mut body, total)| async move {
                match body
                    .try_next()
                    .await
                    .map_err(|_| error("HTTP body read failed"))?
                {
                    Some(bytes) if bytes.len() as u64 <= expected.saturating_sub(total) => {
                        let next = total + bytes.len() as u64;
                        Ok(Some((bytes, (body, next))))
                    }
                    Some(_) => Err(error("HTTP body exceeds range budget")),
                    None if total == expected => Ok(None),
                    None => Err(error("Truncated HTTP body")),
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
    fn list(&self, _: Option<&Path>) -> BoxStream<'static, Result<ObjectMeta>> {
        stream::once(async { Err(unsupported()) }).boxed()
    }
    async fn list_with_delimiter(&self, _: Option<&Path>) -> Result<ListResult> {
        Err(unsupported())
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
        keys: BoxStream<'static, Result<Path>>,
    ) -> BoxStream<'static, Result<Path>> {
        keys.map(|_| Err(unsupported())).boxed()
    }
    async fn copy_opts(&self, _: &Path, _: &Path, _: CopyOptions) -> Result<()> {
        Err(unsupported())
    }
    async fn rename_opts(&self, _: &Path, _: &Path, _: RenameOptions) -> Result<()> {
        Err(unsupported())
    }
}
