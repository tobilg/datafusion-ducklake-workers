//! Independent DuckDB Parquet writer plus explicit provider metadata commits.
//! This host-only tool is never in the query Worker's dependency graph.
use datafusion_ducklake_provider::DuckLakeSessionContext;
use ducklake_storage::{CatalogBackend,AppendFile,quack::QuackCatalog};
use ducklake_catalog::DataFileId;
use futures_util::TryStreamExt;
use std::{error::Error,path::Path,io::{Read,Seek,SeekFrom},process::Command};
async fn sql(ctx:&DuckLakeSessionContext,sql:&str)->Result<(),Box<dyn Error>> {
    let mut s=ctx.sql(sql).await?.execute_stream().await?;while s.try_next().await?.is_some(){} Ok(())
}
pub async fn run(ctx:&DuckLakeSessionContext,state:&serde_json::Value,root:&Path)->Result<(),Box<dyn Error>> {
    let backend=QuackCatalog::try_new_with_ssl("quack:127.0.0.1:8792",state["bootstrap_jwt"].as_str().ok_or("bootstrap credential")?,false)?;
    backend.connect().await?;
    for (name,large) in [("compressed",false),("large_object",true)] {
        sql(ctx,&format!("CREATE TABLE IF NOT EXISTS lake.main.{name} (id BIGINT, big BIGINT UNSIGNED, label VARCHAR)")).await?;
        let snapshot=backend.snapshots().await?.into_iter().max_by_key(|s|s.snapshot_id.0).ok_or("snapshot")?;
        let schema=backend.schemas_at(snapshot.snapshot_id).await?.into_iter().find(|s|s.schema_name=="main").ok_or("main")?;
        let table=backend.tables_at(schema.schema_id,snapshot.snapshot_id).await?.into_iter().find(|t|t.table_name==name).ok_or("table")?;
        if !backend.data_files_at(table.table_id,snapshot.snapshot_id).await?.is_empty(){println!("Preserved existing extended fixture {name}");continue;}
        assert!(schema.path_is_relative && table.path_is_relative);
        let columns=backend.columns_at(table.table_id,snapshot.snapshot_id).await?;
        let ids=columns.iter().map(|c|format!("{}:{}",c.column_name,c.column_id.0)).collect::<Vec<_>>().join(",");
        let key=format!("catalogs/fixture/{}{}/",schema.path.as_deref().unwrap_or(""),table.path.as_deref().unwrap_or("").trim_end_matches('/'));
        assert!(key.starts_with("catalogs/fixture/main/"));
        let directory=root.join(&key);std::fs::create_dir_all(&directory)?;
        let mut append=vec![];
        let variants=if large {vec![("large","UNCOMPRESSED",1,9000)]}else{vec![("zstd","ZSTD",1,6000),("snappy","SNAPPY",6001,12000)]};
        for (index,(label,codec,first,last)) in variants.into_iter().enumerate() {
            let filename=format!("fixture-{label}.parquet");let path=directory.join(&filename);
            let text=if large {"repeat(lpad(CAST(id AS VARCHAR),5,'0'),3277)"}else{"CASE WHEN id%3=0 THEN NULL ELSE 'Grüße 🌍' END"};
            let statement=format!("COPY (SELECT id, CAST(id AS UBIGINT)+9223372036854775808::UBIGINT AS big,{text} AS label FROM range({first},{}) t(id)) TO '{}' (FORMAT PARQUET,COMPRESSION {codec},ROW_GROUP_SIZE 2048,FIELD_IDS {{{ids}}})",last+1,path.to_string_lossy().replace('\'',"''"));
            let result=Command::new("duckdb").args(["-init","/dev/null","-c",&statement]).output()?;
            if !result.status.success(){return Err(format!("DuckDB fixture write failed: {}",String::from_utf8_lossy(&result.stderr)).into());}
            let mut file=std::fs::File::open(&path)?;let size=file.metadata()?.len();
            file.seek(SeekFrom::End(-8))?;let mut footer=[0u8;8];file.read_exact(&mut footer)?;assert_eq!(&footer[4..],b"PAR1");
            if large {assert!(size>128*1024*1024);}
            append.push(AppendFile {table_id:table.table_id,expected_snapshot:snapshot.snapshot_id,
                data_file_id:DataFileId(snapshot.next_file_id+index as i64),begin_snapshot:None,path:filename,path_is_relative:true,
                record_count:last-first+1,file_size_bytes:size as i64,footer_size:Some(u32::from_le_bytes(footer[..4].try_into()?) as i64+8),
                row_id_start:None,explicit_row_ids:false,column_stats:vec![],variant_stats:vec![],partition_id:None,partition_values:vec![],
                encryption_key:None,mapping_id:None,partial_max:None});
            println!("Generated {name}/{label}: {size} bytes, {codec}, 2048-row groups");
        }
        backend.commit_append_files(append).await?;
    }
    // Provider-managed partitioning and evolution are separate from custom Parquet generation.
    sql(ctx,"CREATE TABLE IF NOT EXISTS lake.main.partitioned (id BIGINT, category BIGINT)").await?;
    let mut count=ctx.sql("SELECT count(*) FROM lake.main.partitioned").await?.execute_stream().await?;
    let empty=count.try_next().await?.ok_or("count")?.column(0).as_any().downcast_ref::<datafusion_ducklake_provider::datafusion::arrow::array::Int64Array>().ok_or("count type")?.value(0)==0;
    drop(count);
    if empty {
        sql(ctx,"ALTER TABLE lake.main.partitioned SET PARTITIONED BY (category)").await?;
        sql(ctx,"INSERT INTO lake.main.partitioned SELECT id,id%2 FROM generate_series(1,100) t(id)").await?;
        sql(ctx,"ALTER TABLE lake.main.partitioned ADD COLUMN extra BIGINT DEFAULT 7").await?;
        sql(ctx,"INSERT INTO lake.main.partitioned VALUES (101,1,9)").await?;
    }
    backend.disconnect().await?;
    println!("PASS extended fixture: compressed/multiple files and row groups, large external object, partition/evolution metadata");
    Ok(())
}
