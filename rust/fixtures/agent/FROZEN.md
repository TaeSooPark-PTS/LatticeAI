# FROZEN — last generating tree: commit fc65e60

`commands.json` and `execution.json` were produced by
`scripts/generate_agent_parity_fixtures.py` against
`latticeai.tools.commands.run_command`. WP-P1 dropped `run_command` from
the worker. Those two files stay committed; do not regenerate them.

The other goldens in `golden/` (policies, decisions, calls, shlex, paths,
normalize, contract, manifest) still regenerate from keep-set Python.
