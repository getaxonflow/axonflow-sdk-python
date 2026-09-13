# OpenAPI schema snapshot for the wire-shape contract

The wire-shape contract (`tests/test_wire_shape.py`, run by the `wire-shape-contract` job in
`.github/workflows/ci.yml`) diffs the SDK's pydantic models against the platform's OpenAPI specs.
It reads the schema declarations under `components.schemas` and the names of each one's
`properties`.

The four files here hold exactly that, and nothing else. `scripts/snapshot_openapi_schemas.py`
derives them from the platform's `docs/api/*.yaml` at platform commit
`36e0e96b7e5c16626d394272b727b533f2b94a04`, the v11.0.0 candidate. It works on the YAML node
graph, so every declaration survives in source order, including a schema declared twice in one
file and a declaration with no properties. A loader that counts in-file duplicates reads the same
thing here as from the source. The script drops descriptions, types, paths and the `info` block;
the full specs carry the platform's own licence statement there, and this repository is MIT
(`tests/test_license_metadata.py`). It refuses a YAML merge key under `schemas` or `properties`,
since loaders disagree on expanding one. `python scripts/snapshot_openapi_schemas.py --self-test`
checks all of this on a planted spec.

Regenerating the baseline from these files gives a byte-identical
`tests/fixtures/wire_shape_baseline.json` to regenerating it from the full specs, so nothing the
contract checks is lost. `tests/test_snapshot_openapi_schemas.py` pins that property on a planted
spec.

| File | sha256 of the source spec |
|---|---|
| `agent-api.yaml` | `49bd1b145cd3b2d29e8cc8de240cc184d4f5d24f83b6c546894d1f0a682a032a` |
| `masfeat-api.yaml` | `2d49d6af2d5b1510b373c01fce1df2b712b32766b14d477652797746c52147e7` |
| `orchestrator-api.yaml` | `b173c9bec456e4af3aa09c63306e503caa73ca0cf61da3dfaa7b21453dcd1188` |
| `policy-api.yaml` | `090730a359bf242b6ed5c1a0d63a2831c749583bd957cc3ecc64c99ead5514b7` |

Each file's header repeats its source path, commit and digest.

## Why a snapshot, not the community mirror

Until now the contract checked out `getaxonflow/axonflow` at a pinned commit. The community
mirror publishes `docs/api/` byte for byte, but it receives v11 only at the v11.0.0 tag. So the
SDK's v11 wire work is checked against the same schemas, derived here ahead of the tag. At the
tag, the pin moves back to the mirror commit that publishes them.

## Changing these files

A change to any file in this directory is a change to the contract's pin. The `wire-shape-contract`
job treats it the same as a change to `openapi_specs_sha`: the PR needs the `spec-pin-bump` label,
and it should not also change SDK models. Do not edit the files by hand.

To refresh the snapshot from a platform commit:

```
python scripts/snapshot_openapi_schemas.py <path to docs/api> tests/fixtures/openapi --source-commit <commit>
python scripts/refresh_wire_shape_baseline.py tests/fixtures/openapi --sha <commit>
```

`python scripts/snapshot_openapi_schemas.py --check-snapshot tests/fixtures/openapi` checks that
every file carries the generated header and is exactly the script's derived form.

Then update the table above. Always pass `--sha`: without it, the refresh script records this
repository's own HEAD.
