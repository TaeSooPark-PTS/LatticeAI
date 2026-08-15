# ingest fixtures (F-ING)

`tiny.pdf` is a deterministic one-page PDF (`%PDF-1.1`, 3-object empty page).
It exists so `/upload/document` binary-path tests do not invent bytes at
runtime. Do not regenerate; the HTTP test mocks `/worker/parse` and only
needs the magic header plus a stable sha.

Generated once from the same byte string as
`lattice-ingest::local_files_api::enrich::TINY_PDF`.
