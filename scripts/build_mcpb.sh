#!/usr/bin/env bash
# Build the Claude Desktop Extension bundle (dist/gmail-mcp-<version>.mcpb).
#
# Stages extension/manifest.json together with the Python project files the
# uv runtime needs (pyproject.toml, uv.lock, src/), validates the manifest,
# and packs it with the official @anthropic-ai/mcpb CLI.
#
# Usage: scripts/build_mcpb.sh [version]
#   version defaults to the version in pyproject.toml (a leading "v" is stripped).
# Env: MCPB_CLI overrides the CLI command (default: npx -y @anthropic-ai/mcpb@2.1.2).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MCPB_CLI="${MCPB_CLI:-npx -y @anthropic-ai/mcpb@2.1.2}"
VERSION="${1:-$(python3 -c 'import tomllib;print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])')}"
VERSION="${VERSION#v}"

STAGE="$ROOT/build/mcpb"
OUT_DIR="$ROOT/dist"
OUT="$OUT_DIR/gmail-mcp-$VERSION.mcpb"

rm -rf "$STAGE"
mkdir -p "$STAGE" "$OUT_DIR"

cp extension/.mcpbignore "$STAGE/"
cp pyproject.toml uv.lock README.md config.json.example "$STAGE/"
[ -f LICENSE ] && cp LICENSE "$STAGE/"
cp -R src "$STAGE/src"
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +

# Stamp the version into the staged manifest.
python3 - "$VERSION" extension/manifest.json "$STAGE/manifest.json" <<'PY'
import json, sys
version, src, dst = sys.argv[1:]
m = json.load(open(src))
m["version"] = version
json.dump(m, open(dst, "w"), indent=2)
open(dst, "a").write("\n")
PY

$MCPB_CLI validate "$STAGE/manifest.json"
rm -f "$OUT"
$MCPB_CLI pack "$STAGE" "$OUT"
echo "Built $OUT"
