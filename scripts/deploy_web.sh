#!/usr/bin/env bash
# Generate web/config.js from Terraform outputs, then sync the dashboard to S3
# and invalidate CloudFront. Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."

REGION=$(cd terraform && terraform output -raw region 2>/dev/null || echo us-east-1)
API=$(cd terraform && terraform output -raw api_endpoint)
POOL=$(cd terraform && terraform output -raw user_pool_id)
CLIENT=$(cd terraform && terraform output -raw user_pool_client_id)
SITE=$(cd terraform && terraform output -raw site_bucket)
DASH=$(cd terraform && terraform output -raw dashboard_url)

cat > web/config.js <<EOF
window.JHCC_CONFIG = {
  region: "${REGION}",
  userPoolId: "${POOL}",
  clientId: "${CLIENT}",
  apiBase: "${API}",
};
EOF
echo "wrote web/config.js"

aws s3 sync web/ "s3://${SITE}/" --exclude "config.example.js" --delete \
  --cache-control "no-cache" --only-show-errors
echo "synced to s3://${SITE}/"

DIST=$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?Aliases.Items==null && contains(Origins.Items[0].DomainName, '${SITE}')].Id | [0]" \
  --output text 2>/dev/null || true)
if [ -n "${DIST:-}" ] && [ "$DIST" != "None" ]; then
  aws cloudfront create-invalidation --distribution-id "$DIST" --paths "/*" \
    --query "Invalidation.Id" --output text
fi
echo "dashboard: ${DASH}"
