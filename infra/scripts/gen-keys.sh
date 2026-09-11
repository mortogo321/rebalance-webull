#!/usr/bin/env bash
# Generate the secrets an environment needs. Prints them; never writes them to a
# tracked file. Use for uat/production; development ships with fixed non-secret
# defaults so a clean clone runs with no setup.
set -euo pipefail

echo "# Generated $(date -u +%Y-%m-%dT%H:%M:%SZ) — store these as CI secrets."
echo "# APP_ENCRYPTION_KEY must be IDENTICAL for the engine and the edge functions,"
echo "# and rotating it without re-encrypting stored rows makes every saved"
echo "# broker credential permanently unreadable."
echo
echo "APP_ENCRYPTION_KEY=$(openssl rand -base64 32)"
echo "ENGINE_INTERNAL_TOKEN=$(openssl rand -hex 32)"
