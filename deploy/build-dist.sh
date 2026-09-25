#!/usr/bin/env bash
# Сборка dist/ (метаданные версии + копия лендинга/доков)
# Usage: ./deploy/build-dist.sh [domain] [version]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
DOMAIN="${1:-}"
VERSION="${2:-0.0.0-dev}"

rm -rf "$DIST"
mkdir -p "$DIST"

cp -R "$ROOT/landing" "$DIST/landing"
cp -R "$ROOT/docs" "$DIST/docs"
cp -R "$ROOT/dashboard" "$DIST/dashboard" 2>/dev/null || true
mkdir -p "$DIST/artifacts"
cp -R "$ROOT/artifacts/public/"* "$DIST/artifacts/" 2>/dev/null || true

cat > "$DIST/robots.txt" <<'EOF'
User-agent: *
Allow: /
EOF

cat > "$DIST/version.json" <<EOF
{
  "product": "МосТранс Предикт",
  "version": "${VERSION}",
  "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "modules": ["backend", "ml", "dashboard"],
  "horizon_sec": [600, 900],
  "public": true
}
EOF

printf '%s\n' "$VERSION" > "$DIST/VERSION"
printf '%s\n' "$VERSION" > "$ROOT/VERSION"

if [[ -n "$DOMAIN" ]]; then
  python3 - <<PY
from pathlib import Path
p = Path("$DIST/landing/index.html")
t = p.read_text(encoding="utf-8")
# optional domain note injection
note = Path("$DIST/landing/index.html")
print("Domain hint: https://${DOMAIN}/")
PY
fi

echo "Built $DIST version=$VERSION"
du -sh "$DIST"
