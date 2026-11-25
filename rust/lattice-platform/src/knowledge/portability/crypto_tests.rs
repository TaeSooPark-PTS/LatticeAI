use super::*;

#[test]
fn x25519_identity_public_key_matches_ed25519_montgomery_conversion() {
    let dir = tempfile::tempdir().expect("tempdir");
    let id = DeviceIdentity::load_or_create(&dir.path().join("device.key"));
    let (_, x_pub) = identity_x25519_keypair(&id);
    let mont_pub = id.x25519_public_bytes();
    assert_eq!(x_pub, mont_pub);
}

#[test]
fn scrypt_kdf_rejects_invalid_cost_params() {
    let mut out = [0u8; 32];
    assert!(scrypt_kdf(b"pw", b"salt", 3, 8, 1, &mut out).is_err());
    assert!(scrypt_kdf(b"pw", b"salt", 1, 8, 1, &mut out).is_err());
    assert!(scrypt_kdf(b"pw", b"salt", 16, 0, 1, &mut out).is_err());
    assert!(scrypt_kdf(b"pw", b"salt", 16, 8, 0, &mut out).is_err());
    assert!(scrypt_kdf(b"pw", b"salt", 16, 8, 1, &mut out).is_ok());
}

#[test]
fn path_traversal_checks_reject_unsafe_entries() {
    for unsafe_name in [
        "",
        "../evil.txt",
        "a/../../evil.txt",
        "/etc/passwd",
        "foo\\bar",
        "C:secret",
        "foo\0bar",
        "./dot",
        "a//b",
    ] {
        assert!(
            validate_archive_entry_name(unsafe_name).is_err(),
            "expected rejection for {unsafe_name:?}"
        );
    }
    assert!(validate_archive_entry_name("knowledge_graph.sqlite").is_ok());
    assert!(validate_archive_entry_name("knowledge_graph_blobs/sub/file.bin").is_ok());
}

#[test]
fn sealed_box_encrypt_decrypt_roundtrip_and_wrong_recipient_fails() {
    let dir_sender = tempfile::tempdir().unwrap();
    let dir_recv = tempfile::tempdir().unwrap();
    let dir_other = tempfile::tempdir().unwrap();
    let sender = DeviceIdentity::load_or_create(&dir_sender.path().join("sender.key"));
    let receiver = DeviceIdentity::load_or_create(&dir_recv.path().join("recv.key"));
    let other = DeviceIdentity::load_or_create(&dir_other.path().join("other.key"));

    let recv_pub_b64 = URL_SAFE_NO_PAD.encode(receiver.x25519_public_bytes());
    let archive_path = dir_sender.path().join("sealed.latticebrain");
    let mut manifest = OrderedMap::new();
    manifest.insert("kind", json!("subgraph"));
    let payload = b"hello confidential brain";
    seal_and_write_archive(
        &archive_path,
        "subgraph",
        payload,
        &mut manifest,
        EncryptionMode::RecipientPublicKey {
            public_key_b64: &recv_pub_b64,
        },
        &sender,
    )
    .expect("seal");

    let opened = verify_and_decrypt_archive(&archive_path, DecryptionKey::Identity(&receiver))
        .expect("receiver unseals");
    assert_eq!(opened.plaintext, payload);
    assert!(opened.signature_verified);

    let wrong = verify_and_decrypt_archive(&archive_path, DecryptionKey::Identity(&other));
    assert!(wrong.is_err(), "wrong recipient key must be rejected");
}

#[test]
fn v2_pbkdf2_600k_and_v2_scrypt_16k_roundtrip_cleanly() {
    let dir = tempfile::tempdir().unwrap();
    let signer = DeviceIdentity::load_or_create(&dir.path().join("signer.key"));
    let payload = b"confidential archive v2 kdf payload";

    std::env::remove_var(ARCHIVE_KDF_ENV);
    let scrypt_path = dir.path().join("v2_scrypt.latticebrain");
    let mut manifest1 = OrderedMap::new();
    let (hdr1, _) = seal_and_write_archive(
        &scrypt_path,
        "full",
        payload,
        &mut manifest1,
        EncryptionMode::Passphrase("strong-passphrase"),
        &signer,
    )
    .unwrap();
    assert_eq!(hdr1["kdf"], "scrypt");
    assert_eq!(hdr1["kdf_params"]["v"], 2);
    assert_eq!(hdr1["kdf_params"]["n"], SCRYPT_N);
    let dec1 =
        verify_and_decrypt_archive(&scrypt_path, DecryptionKey::Passphrase("strong-passphrase"))
            .unwrap();
    assert_eq!(dec1.plaintext, payload);

    std::env::set_var(ARCHIVE_KDF_ENV, "pbkdf2-hmac-sha256");
    let pbkdf2_path = dir.path().join("v2_pbkdf2.latticebrain");
    let mut manifest2 = OrderedMap::new();
    let (hdr2, _) = seal_and_write_archive(
        &pbkdf2_path,
        "full",
        payload,
        &mut manifest2,
        EncryptionMode::Passphrase("strong-passphrase"),
        &signer,
    )
    .unwrap();
    std::env::remove_var(ARCHIVE_KDF_ENV);
    assert_eq!(hdr2["kdf"], "pbkdf2-hmac-sha256");
    assert_eq!(hdr2["kdf_params"]["iterations"], PBKDF2_V2_ITERATIONS);
    let dec2 =
        verify_and_decrypt_archive(&pbkdf2_path, DecryptionKey::Passphrase("strong-passphrase"))
            .unwrap();
    assert_eq!(dec2.plaintext, payload);
}
