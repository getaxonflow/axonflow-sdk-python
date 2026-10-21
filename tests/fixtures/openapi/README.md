# OpenAPI schema snapshot for the wire-shape contract

The wire-shape contract (`tests/test_wire_shape.py`, run by the `wire-shape-contract` job in
`.github/workflows/ci.yml`) diffs the SDK's pydantic models against the platform's OpenAPI specs.
It reads the schema declarations under `components.schemas` and the names of each one's
`properties`.

The four files here hold exactly that, and nothing else. `scripts/snapshot_openapi_schemas.py`
derives them from the platform's `docs/api/*.yaml` at platform commit
`ba5201a52edd2bfbe9f1dadd736f19cae03d9cc4`, the merged platform source for the v11.2.0 train. It works on the YAML node
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
| `agent-api.yaml` | `617e6236f91dd7716baaab8e2f22ef24f1713ebfd4938cc52a60a3368e3acb1a` |
| `masfeat-api.yaml` | `eb224773c2a14fc6a84a630b652b17e010a4776da28529b13e173b002d2d4756` |
| `orchestrator-api.yaml` | `a1f96d929ff35acb1204ce5ba3c6e605b8f91d93e7fa62a2fa8a760ea8486860` |
| `policy-api.yaml` | `a1a7aff59d759033f99721a40d942e8750c6026d69f9efb0edf66016f0069ae9` |

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
python scripts/snapshot_openapi_schemas.py <path to docs/api> tests/fixtures/openapi --source-commit <full 40-character commit>
python scripts/refresh_wire_shape_baseline.py tests/fixtures/openapi
```

`python scripts/snapshot_openapi_schemas.py --check-snapshot tests/fixtures/openapi` checks that
every file carries the generated header, that all of them name one full platform commit, and that each
is exactly the script's derived form, which refuses
prose, types or a spec copied in verbatim. It cannot see a declaration added by hand in the derived
form itself; the `spec-pin-bump` label that any change here requires is what makes that visible.

Then update the table above. The refresh script reads the platform commit from the files' headers
and refuses a `--sha` that disagrees with them. A test pins that commit to the baseline's
`openapi_specs_sha`.
