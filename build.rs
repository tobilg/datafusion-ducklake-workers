fn main() {
    // Cargo does not discover changes in the managed Emscripten JS libraries.
    // Relink when the pinned tool configuration or compatibility series changes.
    println!("cargo:rerun-if-changed=tools.lock.json");
    println!("cargo:rerun-if-changed=patches");
    println!("cargo:rerun-if-env-changed=WORKER_LINK_OPT");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("emscripten") {
        // Explicit O1 is a faster development link; release defaults to size optimization.
        let opt = std::env::var("WORKER_LINK_OPT").unwrap_or_else(|_| "s".into());
        assert!(
            matches!(opt.as_str(), "s" | "1"),
            "WORKER_LINK_OPT must be s or 1"
        );
        println!("cargo:rustc-link-arg=-O{opt}");
        // Rust 1.98's query-planning frames also overflow 64 KiB in optimized
        // CTE/subquery file queries. Keep the same bounded stack in every variant.
        println!("cargo:rustc-link-arg=-sSTACK_SIZE=262144");
    }
}
