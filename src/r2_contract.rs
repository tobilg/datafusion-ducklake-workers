//! Real binding tests run only through the local fixture probe.
use crate::{path_policy::PathPolicy, r2_store::R2Store};
use futures_util::{StreamExt, TryStreamExt, stream};
use object_store::{Error, GetOptions, GetRange, ObjectStore, ObjectStoreExt, path::Path};
use std::sync::atomic::Ordering;

pub async fn run(env: &worker::Env) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    let policy = PathPolicy::new(
        "quacklake-fixture",
        "fixture",
        "r2://quacklake-fixture/catalogs/fixture/",
    )?;
    let store = R2Store::new(env.bucket("CATALOG_R2")?, policy);
    let key = Path::parse("catalogs/fixture/adapter/range.bin")?;
    let meta = store.head(&key).await?;
    assert_eq!(meta.size, 6 * 1024 * 1024);
    assert!(meta.last_modified.timestamp_millis() > 0);
    assert!(meta.e_tag.is_some() && meta.version.is_some());
    for request in [
        GetRange::Bounded(3..11),
        GetRange::Suffix(17),
        GetRange::Offset(meta.size - 9),
        GetRange::Bounded(meta.size - 4..meta.size + 50),
    ] {
        let expected = request.as_range(meta.size)?;
        let result = store
            .get_opts(&key, GetOptions::new().with_range(Some(request)))
            .await?;
        assert_eq!(result.range, expected);
        let bytes = result.bytes().await?;
        assert_eq!(
            bytes.as_ref(),
            &expected.map(|i| (i % 251) as u8).collect::<Vec<_>>()
        );
    }
    // A >1 MiB request must be split into bounded pieces while preserving bytes.
    let bytes = store.get_range(&key, 1023..1024 * 1024 + 1047).await?;
    assert!(
        bytes
            .iter()
            .enumerate()
            .all(|(i, b)| *b == ((i + 1023) % 251) as u8)
    );
    let multi = store.get_ranges(&key, &[17..25, 1..4, 100..111]).await?;
    assert_eq!(
        multi.iter().map(|b| b.len()).collect::<Vec<_>>(),
        vec![8, 3, 11]
    );
    for range in [0..0, 100..99, meta.size..meta.size + 1] {
        assert!(store.get_range(&key, range).await.is_err());
    }
    assert!(
        store.get(&key).await.is_err(),
        "full 6 MiB read must exceed cap"
    );
    assert!(matches!(
        store
            .get_opts(
                &key,
                GetOptions {
                    version: meta.version.clone(),
                    ..Default::default()
                }
            )
            .await,
        Err(Error::NotSupported { .. })
    ));
    assert!(matches!(
        store
            .get_opts(
                &key,
                GetOptions {
                    if_match: Some("wrong".into()),
                    ..Default::default()
                }
            )
            .await,
        Err(Error::Precondition { .. })
    ));
    assert!(matches!(
        store
            .get_opts(
                &key,
                GetOptions {
                    if_none_match: meta.e_tag.clone(),
                    ..Default::default()
                }
            )
            .await,
        Err(Error::NotModified { .. })
    ));
    let empty = store
        .get(&Path::from("catalogs/fixture/adapter/empty.bin"))
        .await?
        .bytes()
        .await?;
    assert!(empty.is_empty());
    assert_eq!(
        store
            .get(&Path::parse("catalogs/fixture/adapter/Grüße 100%25.bin")?)
            .await?
            .bytes()
            .await?
            .as_ref(),
        &[1, 2, 3]
    );
    assert!(matches!(
        store
            .head(&Path::from("catalogs/fixture/adapter/missing"))
            .await,
        Err(Error::NotFound { .. })
    ));
    assert!(matches!(
        store.head(&Path::from("outside/denied.bin")).await,
        Err(Error::PermissionDenied { .. })
    ));
    let prefix = Path::from("catalogs/fixture/adapter/list");
    let listed: Vec<_> = store.list(Some(&prefix)).try_collect().await?;
    assert_eq!(listed.len(), 130, "listing must paginate");
    let delimited = store
        .list_with_delimiter(Some(&Path::from("catalogs/fixture/adapter")))
        .await?;
    assert!(delimited.common_prefixes.contains(&prefix));
    assert!(
        store
            .list(None)
            .try_collect::<Vec<_>>()
            .await?
            .iter()
            .all(|m| m.location.as_ref().starts_with("catalogs/fixture/"))
    );
    assert!(matches!(
        store
            .put(&key, bytes::Bytes::from_static(b"denied").into())
            .await,
        Err(Error::NotSupported { .. })
    ));
    assert!(matches!(
        store.put_multipart(&key).await,
        Err(Error::NotSupported { .. })
    ));
    assert!(matches!(
        store.copy(&key, &key).await,
        Err(Error::NotSupported { .. })
    ));
    assert!(matches!(
        store.rename(&key, &key).await,
        Err(Error::NotSupported { .. })
    ));
    let mut deletion = store.delete_stream(stream::iter(vec![Ok(key)]).boxed());
    assert!(matches!(
        deletion.next().await,
        Some(Err(Error::NotSupported { .. }))
    ));
    assert_eq!(store.metrics.active.load(Ordering::Relaxed), 0);
    assert_eq!(store.metrics.peak.load(Ordering::Relaxed), 2);
    Ok(
        serde_json::json!({"passed":true,"range_requests":store.metrics.ranges.load(Ordering::Relaxed),"object_bytes":store.metrics.bytes.load(Ordering::Relaxed),"listed":listed.len(),"peak_object_reads":store.metrics.peak.load(Ordering::Relaxed)}),
    )
}
