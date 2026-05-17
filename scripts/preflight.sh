#!/usr/bin/env bash
# Pre-deploy sanity check. Run before the first `terraform apply` and
# again before each manual deploy.
set -euo pipefail

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
info()   { printf '   %s\n' "$*"; }

fail=0
warn=0

check() {
    local name="$1"; shift
    if "$@" >/dev/null 2>&1; then
        green "✓ $name"
    else
        red "✗ $name"
        fail=$((fail+1))
    fi
}

soft_check() {
    local name="$1"; shift
    if "$@" >/dev/null 2>&1; then
        green "✓ $name"
    else
        yellow "! $name"
        warn=$((warn+1))
    fi
}

echo "--- CLI tools ---"
check "docker installed"        command -v docker
check "docker buildx installed" docker buildx version
check "aws CLI installed"       command -v aws
check "terraform installed"     command -v terraform
soft_check "jq installed"       command -v jq

echo
echo "--- AWS credentials ---"
check "aws sts get-caller-identity" aws sts get-caller-identity

if aws sts get-caller-identity >/dev/null 2>&1; then
    info "Account: $(aws sts get-caller-identity --query Account --output text)"
    info "Region:  ${AWS_REGION:-$(aws configure get region || echo unset)}"
fi

echo
echo "--- Terraform state ---"
TF_DIR="$(dirname "$0")/../infra/terraform"
if [[ -f "$TF_DIR/terraform.tfvars" ]]; then
    green "✓ terraform.tfvars present"
else
    red "✗ terraform.tfvars missing — copy terraform.tfvars.example and fill it in"
    fail=$((fail+1))
fi

echo
echo "--- App build ---"
check "Dockerfile present"    test -f "$(dirname "$0")/../Dockerfile"
check "requirements.txt"      test -f "$(dirname "$0")/../requirements.txt"
check "alembic.ini present"   test -f "$(dirname "$0")/../alembic.ini"

echo
echo "--- Env spot-check ---"
for v in OPENAI_API_KEY; do
    if [[ -n "${!v:-}" ]]; then
        green "✓ $v set in shell"
    else
        yellow "! $v not in shell (fine if you set it via terraform.tfvars)"
        warn=$((warn+1))
    fi
done

echo
echo "--- Summary ---"
if [[ $fail -gt 0 ]]; then
    red "$fail check(s) failed."
    exit 1
fi
green "All required checks passed."
[[ $warn -gt 0 ]] && yellow "$warn warning(s)."
exit 0
