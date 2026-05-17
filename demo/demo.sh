#!/usr/bin/env bash
# Live-demo helper for the Loom walkthrough.
# Assumes `uvicorn app.main:app --reload` is running on :8000.
#
# Usage:  ./demo/demo.sh aeo-good | aeo-bad | fanout-good | fanout-bad | fanout-only
set -euo pipefail

API="${API:-http://127.0.0.1:8000}"
DIR="$(cd "$(dirname "$0")" && pwd)"

jq_pretty() { jq '.' 2>/dev/null || cat; }

case "${1:-help}" in
  aeo-good)
    echo "=== AEO scorer on a WELL-OPTIMIZED page ==="
    jq -n --arg t "$(cat "$DIR/aeo_good.html")" \
      '{input_type:"text", input_value:$t}' \
      | curl -sS -X POST "$API/api/aeo/analyze" \
          -H 'Content-Type: application/json' -d @- | jq_pretty
    ;;
  aeo-bad)
    echo "=== AEO scorer on a POORLY-OPTIMIZED page ==="
    jq -n --arg t "$(cat "$DIR/aeo_bad.html")" \
      '{input_type:"text", input_value:$t}' \
      | curl -sS -X POST "$API/api/aeo/analyze" \
          -H 'Content-Type: application/json' -d @- | jq_pretty
    ;;
  fanout-good)
    echo "=== Fan-out on entity-rich content (expect HIGH coverage) ==="
    jq -n --arg q "best AI writing tool for SEO" \
          --arg c "$(cat "$DIR/geo_content_good.txt")" \
      '{target_query:$q, existing_content:$c}' \
      | curl -sS -X POST "$API/api/fanout/generate" \
          -H 'Content-Type: application/json' -d @- | jq_pretty
    ;;
  fanout-bad)
    echo "=== Fan-out on sparse content (expect LOW coverage / many gaps) ==="
    jq -n --arg q "best AI writing tool for SEO" \
          --arg c "$(cat "$DIR/geo_content_bad.txt")" \
      '{target_query:$q, existing_content:$c}' \
      | curl -sS -X POST "$API/api/fanout/generate" \
          -H 'Content-Type: application/json' -d @- | jq_pretty
    ;;
  fanout-only)
    echo "=== Fan-out with no content — sub-queries only ==="
    jq -n --arg q "best AI writing tool for SEO" \
      '{target_query:$q}' \
      | curl -sS -X POST "$API/api/fanout/generate" \
          -H 'Content-Type: application/json' -d @- | jq_pretty
    ;;
  *)
    cat <<EOF
demo.sh — live demo runner for the Loom walkthrough.

Commands:
  aeo-good      AEO scorer on demo/aeo_good.html       (expect ~90-100)
  aeo-bad       AEO scorer on demo/aeo_bad.html        (expect ~0-25)
  fanout-good   Fan-out with entity-rich content        (expect high coverage)
  fanout-bad    Fan-out with sparse content             (expect many gaps)
  fanout-only   Fan-out with no content                 (sub-queries only)

Prereqs:
  uvicorn app.main:app --reload   # in another terminal
  jq + curl in PATH
EOF
    ;;
esac
