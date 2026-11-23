use super::*;

#[test]
fn meminfo_total_is_kilobytes_times_1024() {
    let sample = "MemTotal:       16384000 kB\nMemFree:         1000 kB\n";
    assert_eq!(parse_meminfo_total(sample), Some(16_384_000 * 1024));
    assert_eq!(parse_meminfo_total("nope"), None);
}

#[test]
fn worker_down_recommendations_are_empty_with_a_reason() {
    let probe = HostProbe {
        os: "macos".into(),
        os_version: "15.0".into(),
        arch: "aarch64".into(),
        apple_silicon: true,
        ram_bytes: Some(16 * 1024 * 1024 * 1024),
        ram_gb: 16,
        ram_mb: 16 * 1024,
        cpu_model: "Apple M-series".into(),
        cpu_cores: 8,
        cpu_logical_cores: 8,
        disk_free_bytes: Some(64 * 1024 * 1024 * 1024),
        disk_free_gb: 64,
        disk_free_mb: 64 * 1024,
        package_manager: "brew".into(),
        has_internet: false,
    };
    let catalog = WorkerCatalog {
        reachable: false,
        reason: Some("worker is not configured".into()),
        models: json!({}),
        sysinfo: json!({}),
    };
    let (recs, registry) = recommend_from_catalog(&probe, "local_mlx", &catalog);
    assert_eq!(recs.get("top_pick"), Some(&Value::Null));
    assert_eq!(recs.get("families"), Some(&json!([])));
    assert_eq!(recs.get("models"), Some(&json!([])));
    assert_eq!(recs.get("engine_available"), Some(&json!(false)));
    assert_eq!(recs.get("reason"), Some(&json!("worker is not configured")));
    assert_eq!(recs.get("ram_gb"), Some(&json!(16)));
    assert_eq!(recs.get("apple_silicon"), Some(&json!(true)));
    assert_eq!(registry.get("verified_total"), Some(&json!(0)));
}

#[test]
fn top_pick_honors_ram_tier_then_recommended_default_then_priority() {
    let probe = HostProbe {
        os: "macos".into(),
        os_version: "15.0".into(),
        arch: "aarch64".into(),
        apple_silicon: true,
        ram_bytes: Some(16 * 1024 * 1024 * 1024),
        ram_gb: 16,
        ram_mb: 16 * 1024,
        cpu_model: "Apple M-series".into(),
        cpu_cores: 8,
        cpu_logical_cores: 8,
        disk_free_bytes: None,
        disk_free_gb: 0,
        disk_free_mb: 0,
        package_manager: "none".into(),
        has_internet: false,
    };
    let catalog = WorkerCatalog {
        reachable: true,
        reason: None,
        models: json!({
            "engines": [{"id": "local_mlx", "installed": true}],
            "recommended": [
                {
                    "id": "mlx-community/gemma-4-12B-it-4bit",
                    "name": "Gemma 4 12B",
                    "family": "Gemma 4",
                    "recommended_default": true,
                    "display_priority": 25,
                    "hardware": {"min_ram_gb": 12.0, "recommended_ram_gb": 16.0}
                },
                {
                    "id": "mlx-community/Qwen3.5-9B-MLX-4bit",
                    "name": "Qwen3.5 9B",
                    "family": "Qwen3.5",
                    "recommended_default": true,
                    "display_priority": 20,
                    "hardware": {"min_ram_gb": 12.0, "recommended_ram_gb": 16.0}
                },
                {
                    "id": "mlx-community/gemma-4-31b-it-4bit",
                    "name": "Gemma 4 31B",
                    "family": "Gemma 4",
                    "recommended_default": false,
                    "display_priority": 60,
                    "hardware": {"min_ram_gb": 26.0, "recommended_ram_gb": 48.0}
                }
            ],
            "registry": {"version": "5.2.0", "verified_count": 3, "verified": []}
        }),
        sysinfo: json!({}),
    };
    let (recs, registry) = recommend_from_catalog(&probe, "local_mlx", &catalog);
    assert_eq!(recs.get("engine_available"), Some(&json!(true)));
    let top = recs
        .get("top_pick")
        .and_then(Value::as_object)
        .expect("top");
    assert_eq!(
        top.get("id").and_then(Value::as_str),
        Some("mlx-community/Qwen3.5-9B-MLX-4bit")
    );
    assert_eq!(recs.get("counts").unwrap()["recommended"], json!(2));
    assert_eq!(recs.get("counts").unwrap()["not_recommended"], json!(1));
    assert_eq!(registry.get("verified_total"), Some(&json!(3)));
    let families = recs.get("families").and_then(Value::as_array).unwrap();
    assert!(families.iter().any(|row| row["family"] == "Gemma 4"));
}

#[test]
fn catalog_hints_cover_qwen38_and_gemma4_qat_models() {
    let qwen38 = catalog_hints("mlx-community/Qwen3.8-27B-4bit").expect("qwen3.8 hint");
    assert_eq!(qwen38.family, "Qwen3.8");
    assert_eq!(qwen38.recommended_ram_gb, 32.0);
    assert_eq!(qwen38.display_priority, 52);

    let qwen38_omlx =
        catalog_hints("omlx:mlx-community--Qwen3.8-27B-4bit").expect("omlx qwen3.8 hint");
    assert_eq!(qwen38_omlx.recommended_ram_gb, 32.0);

    let qwen38_gguf =
        catalog_hints("ollama:hf.co/ggml-org/Qwen3.8-27B-GGUF:Q4_K_M").expect("gguf qwen3.8 hint");
    assert_eq!(qwen38_gguf.recommended_ram_gb, 32.0);

    let qat = catalog_hints("mlx-community/gemma-4-26B-A4B-it-qat-4bit").expect("qat hint");
    assert_eq!(qat.family, "Gemma 4");
    assert_eq!(qat.display_priority, 48);

    let optiq =
        catalog_hints("mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit").expect("optiq hint");
    assert_eq!(optiq.family, "Gemma 4");
    assert_eq!(optiq.display_priority, 51);

    let emb2 = catalog_hints("google/embeddinggemma-2").expect("embeddinggemma-2 hint");
    assert_eq!(emb2.family, "EmbeddingGemma");
    assert_eq!(emb2.display_priority, 70);
}

#[test]
fn recommend_embedding_for_host_tailors_to_ram_and_accounts_for_footprint() {
    let probe_16gb = HostProbe {
        os: "macos".into(),
        os_version: "15.0".into(),
        arch: "aarch64".into(),
        apple_silicon: true,
        ram_bytes: Some(16 * 1024 * 1024 * 1024),
        ram_gb: 16,
        ram_mb: 16 * 1024,
        cpu_model: "Apple M-series".into(),
        cpu_cores: 8,
        cpu_logical_cores: 8,
        disk_free_bytes: None,
        disk_free_gb: 0,
        disk_free_mb: 0,
        package_manager: "brew".into(),
        has_internet: true,
    };
    let emb_16 = recommend_embedding_for_host(&probe_16gb, "local_mlx");
    assert_eq!(emb_16["id"], json!("google/embeddinggemma-2"));
    assert_eq!(emb_16["hf_repo_id"], json!("google/embeddinggemma-2"));
    assert_eq!(
        emb_16["mlx_repo_id"],
        json!("mlx-community/embeddinggemma-2-4bit")
    );
    assert_eq!(emb_16["category"], json!("embedding"));
    assert_eq!(emb_16["role"], json!("embedding"));
    assert_eq!(emb_16["task"], json!("embedding"));
    assert_eq!(emb_16["dimensions"], json!(768));
    assert_eq!(emb_16["memory_footprint_gb"], json!(0.5));

    let mut probe_6gb = probe_16gb.clone();
    probe_6gb.ram_gb = 6;
    probe_6gb.ram_mb = 6 * 1024;
    probe_6gb.ram_bytes = Some(6 * 1024 * 1024 * 1024);
    let emb_6 = recommend_embedding_for_host(&probe_6gb, "local_mlx");
    assert_eq!(emb_6["id"], json!("google/embeddinggemma-300m"));
    assert_eq!(
        emb_6["mlx_repo_id"],
        json!("mlx-community/embeddinggemma-300m-4bit")
    );
    assert_eq!(emb_6["memory_footprint_gb"], json!(0.3));
}
