#!/usr/bin/env bash
# Manual deploy fallback (use when GitHub Actions is offline or unwired).
#
# Usage:
#   AWS_REGION=us-east-1 ./scripts/deploy.sh
#
# Reads instance ID + ECR repo + secret ARN from Terraform outputs.
set -euo pipefail

REGION="${AWS_REGION:-$(aws configure get region || echo us-east-1)}"
TF_DIR="$(dirname "$0")/../infra/terraform"

cd "$TF_DIR"

INSTANCE_ID=$(terraform output -raw ec2_instance_id)
ECR_URL=$(terraform output -raw ecr_repo_url)
SECRET_ARN=$(terraform output -raw secret_arn)

cd "$OLDPWD"

echo "--> Logging in to ECR ($ECR_URL)"
aws ecr get-login-password --region "$REGION" | \
    docker login --username AWS --password-stdin "${ECR_URL%/*}"

TAG="$(git rev-parse --short HEAD)-$(date +%s)"
echo "--> Building ARM image with tag $TAG"
docker buildx build --platform linux/arm64 \
    -t "$ECR_URL:latest" \
    -t "$ECR_URL:$TAG" \
    --push .

echo "--> Triggering remote redeploy on $INSTANCE_ID"
CMD_ID=$(aws ssm send-command \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --document-name AWS-RunShellScript \
    --comment "manual deploy $TAG" \
    --parameters "commands=[\
        'set -euo pipefail',\
        'cd /opt/aegis',\
        'aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin ${ECR_URL%/*}',\
        '/usr/local/bin/aegis-render-env $REGION $SECRET_ARN',\
        '/usr/local/bin/aegis-prepare-pgdump-env',\
        'docker compose pull',\
        'docker compose up -d',\
        'sleep 5',\
        'curl -fsS http://127.0.0.1:8000/api/health'\
    ]" \
    --query 'Command.CommandId' --output text)

echo "SSM command: $CMD_ID"
for _ in $(seq 1 60); do
    STATUS=$(aws ssm get-command-invocation --region "$REGION" \
        --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" \
        --query Status --output text 2>/dev/null || echo Pending)
    echo "  status=$STATUS"
    case "$STATUS" in
        Success) echo "--> Deploy complete."; exit 0 ;;
        Failed|Cancelled|TimedOut)
            aws ssm get-command-invocation --region "$REGION" \
                --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" \
                --query '{stdout:StandardOutputContent,stderr:StandardErrorContent}' \
                --output text
            exit 1 ;;
    esac
    sleep 5
done
echo "Deploy timed out." >&2
exit 1
