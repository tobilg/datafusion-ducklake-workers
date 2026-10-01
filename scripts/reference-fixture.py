#!/usr/bin/env python3
"""Independent native DuckDB reference over the actual Parquet + position deletes.
DuckDB is a fixture tool only; it is never linked into the query Worker.
"""

from devlib import REPORTS
import json, pathlib, subprocess

root = pathlib.Path(__file__).resolve().parents[1]
version = subprocess.check_output(
    ["duckdb", "-init", "/dev/null", "--version"], text=True
).strip()
prefix = ".cache/fixture-objects/catalogs/fixture/main/"
setup = f"""
CREATE VIEW sales_raw AS SELECT * FROM read_parquet('{prefix}sales/*.parquet',file_row_number=true,filename=true);
CREATE VIEW deletes AS SELECT * FROM read_parquet('{prefix}sales/datafusion-ducklake-provider/*.parquet');
CREATE VIEW sales AS SELECT s.* FROM sales_raw s ANTI JOIN deletes d ON s.file_row_number=d.pos AND split_part(s.filename,'/',-1)=d.file_path;
CREATE VIEW categories AS SELECT * FROM read_parquet('{prefix}categories/*.parquet');
CREATE VIEW compressed AS SELECT * FROM read_parquet('{prefix}compressed/*.parquet');
CREATE VIEW large_object AS SELECT * FROM read_parquet('{prefix}large_object/*.parquet');
CREATE VIEW partitioned AS SELECT * FROM read_parquet('{prefix}partitioned/**/*.parquet',union_by_name=true,hive_partitioning=true);
"""
queries = {
    "aggregate": "SELECT CAST(count(*) AS VARCHAR) AS n,CAST(sum(id) AS VARCHAR) AS s FROM sales",
    "projection": "SELECT CAST(id AS VARCHAR) AS id,CAST(large_int AS VARCHAR) AS large_int,CAST(amount AS VARCHAR) AS amount,label,CAST(epoch_ns(happened_at) AS VARCHAR) AS happened_at FROM sales ORDER BY sales.id LIMIT 3",
    "join": "SELECT c.name,CAST(count(*) AS VARCHAR) AS n,CAST(sum(s.id) AS VARCHAR) AS s FROM sales s JOIN categories c ON s.category=c.id GROUP BY c.name ORDER BY c.name",
    "compressed": "SELECT CAST(count(*) AS VARCHAR) AS n,CAST(sum(id) AS VARCHAR) AS s,CAST(min(big) AS VARCHAR) AS lo,CAST(max(big) AS VARCHAR) AS hi FROM compressed",
    "large_object": "SELECT CAST(count(*) AS VARCHAR) AS n,CAST(sum(id) AS VARCHAR) AS s FROM large_object",
    "partitioned": "SELECT CAST(category AS VARCHAR) AS category,CAST(count(*) AS VARCHAR) AS n,CAST(sum(id) AS VARCHAR) AS s,CAST(sum(coalesce(extra,7)) AS VARCHAR) AS e FROM partitioned GROUP BY category ORDER BY category",
}
results = {}
for name, sql in queries.items():
    rows = json.loads(
        subprocess.check_output(
            ["duckdb", "-init", "/dev/null", "-json", "-c", setup + sql],
            cwd=root,
            text=True,
        )
    )
    results[name] = [list(r.values()) for r in rows]
assert results["aggregate"] == [["4500", "11250000"]]
assert results["compressed"] == [
    ["12000", "72006000", "9223372036854775809", "9223372036854787808"]
]
assert results["large_object"] == [["9000", "40504500"]]
assert results["partitioned"] == [
    ["0", "50", "2550", "350"],
    ["1", "51", "2601", "359"],
]
out = root / ".cache/reports/native-reference.json"
out.write_text(
    json.dumps(
        {
            "engine": version,
            "method": "Direct external Parquet scan with independent position-delete anti join",
            "results": results,
        },
        indent=2,
        ensure_ascii=False,
    )
    + "\n"
)
print("Independent native Parquet/deletion reference saved:", out.relative_to(root))
