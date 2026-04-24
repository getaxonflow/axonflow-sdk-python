#!/usr/bin/env bash
# Validates that the Python SDK's version declarations match the most
# recent released section of CHANGELOG.md. Patterned on the same
# script in axonflow-enterprise and axonflow-sdk-go.
#
# Why: the release workflow sed-rewrites pyproject.toml + axonflow/
# _version.py at publish time but never commits the bump back to main,
# so the repo version silently lags the registry version between
# releases. This gate enforces the invariant on every PR:
#
#   pyproject.toml::version
#     == axonflow/_version.py::__version__
#     == most recent `## [X.Y.Z]` section in CHANGELOG.md
#
# When it's time to release, a single release-prep PR renames
# [Unreleased] → [X.Y.Z] - DATE AND bumps both manifest files in the
# same commit so this gate always sees them together.
#
# Run locally:
#   ./.github/scripts/validate-version-alignment.sh

set -euo pipefail

ERRORS=0

# Latest RELEASED version = first `## [x.y.z]` line that isn't
# [Unreleased] (which starts with a letter, not a digit).
LATEST_VERSION=$(grep -m1 -E '^## \[[0-9]' CHANGELOG.md | sed 's/## \[\(.*\)\].*/\1/' | sed 's/^v//')

if [ -z "${LATEST_VERSION:-}" ]; then
    echo "❌ Could not extract a released version (## [X.Y.Z]) from CHANGELOG.md"
    exit 1
fi

echo "📋 Latest CHANGELOG version: $LATEST_VERSION"
echo ""

# Check pyproject.toml::version
echo "📦 Checking pyproject.toml..."
PYPROJECT_VER=$(grep -m1 -E '^version = "' pyproject.toml | sed 's/version = "\(.*\)"/\1/' || true)
if [ -z "${PYPROJECT_VER:-}" ]; then
    echo "  ❌ pyproject.toml — could not read version"
    ERRORS=$((ERRORS + 1))
elif [ "$PYPROJECT_VER" != "$LATEST_VERSION" ]; then
    echo "  ❌ pyproject.toml — version is \"$PYPROJECT_VER\", expected \"$LATEST_VERSION\""
    ERRORS=$((ERRORS + 1))
else
    echo "  ✅ pyproject.toml — $PYPROJECT_VER"
fi

# Check axonflow/_version.py::__version__
echo "🔧 Checking axonflow/_version.py..."
VERSION_PY=$(grep -m1 -E '^__version__ = "' axonflow/_version.py | sed 's/__version__ = "\(.*\)"/\1/' || true)
if [ -z "${VERSION_PY:-}" ]; then
    echo "  ❌ axonflow/_version.py — could not read __version__"
    ERRORS=$((ERRORS + 1))
elif [ "$VERSION_PY" != "$LATEST_VERSION" ]; then
    echo "  ❌ axonflow/_version.py — __version__ is \"$VERSION_PY\", expected \"$LATEST_VERSION\""
    ERRORS=$((ERRORS + 1))
else
    echo "  ✅ axonflow/_version.py — $VERSION_PY"
fi

echo ""

if [ "$ERRORS" -gt 0 ]; then
    echo "❌ Found $ERRORS version misalignment(s)."
    echo ""
    echo "Fix: bump the stale file(s) to match CHANGELOG v$LATEST_VERSION."
    echo "Or, if CHANGELOG is behind a tag you already pushed, add the"
    echo "missing '## [X.Y.Z] - YYYY-MM-DD' section."
    exit 1
fi

echo "✅ All version constants match CHANGELOG v$LATEST_VERSION."
