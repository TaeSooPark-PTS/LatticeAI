# FROZEN — last generating tree: commit fc65e60

These HTTP goldens were produced by
`scripts/gen_http_fixtures_{brain,platform,ecosystem}.py`,
`scripts/gen_static_fixtures.py`, and `scripts/gen_auth_fixtures.py`
against `latticeai.app_factory.create_app`.

WP-P1 deleted `create_app` and the product routers. The generators cannot
survive on the keep-set. The committed JSON stays; Rust HTTP replay tests
keep asserting them. Do not regenerate.
