#!/usr/bin/env bash
# Forwards the parsed --flag args from action.yml to aegis_gate.py.
# Targets come in via the AEGIS_TARGETS env var so spaces survive the
# GitHub Actions argument-marshalling.
set -euo pipefail
exec python /opt/aegis/aegis_gate.py "$@"
