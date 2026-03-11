#!/usr/bin/env bash
# deploy/setup.sh — One-shot Cloud Run bootstrap for PAMBS
# ─────────────────────────────────────────────────────────
# Usage:
#   PROJECT_ID=your-gcp-project ./deploy/setup.sh
#
# Prerequisites:
#   - gcloud CLI installed and authenticated (gcloud auth login)
#   - .env file present in repo root with real credentials
#   - Docker desktop running (for local build verification)
# ─────────────────────────────────────────────────────────

set -euo pipefail

PROJECT="${PROJECT_ID:-reel-fuse}"
REGION="${REGION:-us-east1}"
REPO="pambs"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/pambs"
BUCKET="pambs-state-${PROJECT}"
SA="pambs-sa"
SA_EMAIL="${SA}@${PROJECT}.iam.gserviceaccount.com"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║         PAMBS Cloud Run Setup                        ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  Project : ${PROJECT}"
echo "║  Region  : ${REGION}"
echo "║  Image   : ${IMAGE}:latest"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── 1. Enable APIs ─────────────────────────────────────────────────────────────
echo "==> [1/7] Enabling GCP APIs..."
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    secretmanager.googleapis.com \
    artifactregistry.googleapis.com \
    storage.googleapis.com \
    --project="${PROJECT}"

# ── 2. Artifact Registry ───────────────────────────────────────────────────────
echo "==> [2/7] Creating Artifact Registry repository..."
gcloud artifacts repositories create "${REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --project="${PROJECT}" 2>/dev/null || echo "  (already exists)"

# ── 3. Service account ─────────────────────────────────────────────────────────
echo "==> [3/7] Creating service account..."
if gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT}" &>/dev/null; then
    echo "  (already exists)"
else
    gcloud iam service-accounts create "${SA}" \
        --display-name="PAMBS runtime service account" \
        --project="${PROJECT}"
fi

gcloud projects add-iam-policy-binding "${PROJECT}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" --quiet

gcloud projects add-iam-policy-binding "${PROJECT}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/storage.objectAdmin" --quiet

# ── 4. GCS bucket for SQLite persistence ──────────────────────────────────────
echo "==> [4/7] Creating GCS bucket for persistent SQLite state..."
gcloud storage buckets create "gs://${BUCKET}" \
    --location="${REGION}" \
    --project="${PROJECT}" 2>/dev/null || echo "  (already exists)"

# Grant Cloud Run SA access to GCS FUSE
gcloud projects add-iam-policy-binding "${PROJECT}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/storage.objectUser" --quiet

# ── 5. Load secrets from .env ──────────────────────────────────────────────────
echo "==> [5/7] Pushing secrets to Secret Manager..."

_secret() {
    local name="$1"
    local value="$2"
    if [ -z "${value}" ]; then
        echo "  SKIP: ${name} (empty value)"
        return
    fi
    echo -n "${value}" | gcloud secrets create "${name}" \
        --data-file=- --project="${PROJECT}" 2>/dev/null || \
    echo -n "${value}" | gcloud secrets versions add "${name}" \
        --data-file=- --project="${PROJECT}"
    echo "  OK: ${name}"
}

# Parse .env with Python3 (handles quotes, inline comments, CRLF, long tokens)
_env_val() {
    python3 - "$1" <<'PYEOF'
import sys, os, re
key = sys.argv[1]
with open(".env") as f:
    for line in f:
        line = line.rstrip("\r\n")
        if not line or line.lstrip().startswith("#"):
            continue
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$', line)
        if m and m.group(1) == key:
            val = m.group(2).strip()
            # Strip matching quotes if present
            if (val.startswith('"') and val.endswith('"')) or \
               (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            print(val, end="")
            sys.exit(0)
PYEOF
}

_secret pambs-pinterest-token          "$(_env_val PINTEREST_ACCESS_TOKEN)"
_secret pambs-pinterest-account-id    "$(_env_val PINTEREST_AD_ACCOUNT_ID)"
_secret pambs-pinterest-conversion-tag "$(_env_val PINTEREST_CONVERSION_TAG_ID)"
_secret pambs-meta-token               "$(_env_val META_ACCESS_TOKEN)"
_secret pambs-meta-account-id          "$(_env_val META_AD_ACCOUNT_ID)"
_secret pambs-northbeam-api-key        "$(_env_val NORTHBEAM_API_KEY)"
_secret pambs-northbeam-client-id      "$(_env_val NORTHBEAM_CLIENT_ID)"
_secret pambs-anthropic-key            "$(_env_val ANTHROPIC_API_KEY)"
_secret pambs-slack-token              "$(_env_val SLACK_BOT_TOKEN)"

# Grant SA access to each secret
for SECRET in \
    pambs-pinterest-token pambs-pinterest-account-id pambs-pinterest-conversion-tag \
    pambs-meta-token pambs-meta-account-id \
    pambs-northbeam-api-key pambs-northbeam-client-id \
    pambs-anthropic-key pambs-slack-token
do
    gcloud secrets add-iam-policy-binding "${SECRET}" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="roles/secretmanager.secretAccessor" \
        --project="${PROJECT}" --quiet 2>/dev/null || true
done

# ── 6. Build & push Docker image ───────────────────────────────────────────────
echo "==> [6/7] Building and pushing Docker image via Cloud Build..."
gcloud builds submit \
    --tag "${IMAGE}:latest" \
    --project="${PROJECT}"

# ── 7. Deploy to Cloud Run ─────────────────────────────────────────────────────
echo "==> [7/7] Deploying PAMBS service to Cloud Run..."
sed "s/PROJECT_ID/${PROJECT}/g" deploy/cloud-run.yaml | \
    gcloud run services replace - \
        --region="${REGION}" \
        --project="${PROJECT}"

SERVICE_URL=$(gcloud run services describe pambs \
    --region="${REGION}" \
    --project="${PROJECT}" \
    --format="value(status.url)")

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ✅  PAMBS deployed successfully!                            ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Service URL : ${SERVICE_URL}"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Next steps:                                                 ║"
echo "║  1. Tail logs:                                               ║"
echo "║     gcloud logging read 'resource.labels.service_name=pambs' ║"
echo "║        --project=${PROJECT} --limit=50 --format=text"
echo "║  2. Seed campaigns:                                          ║"
echo "║     gcloud run jobs execute pambs-seed (or --once locally)   ║"
echo "║  3. When shadow looks right, set SHADOW_MODE=false in        ║"
echo "║     deploy/cloud-run.yaml and re-run this script.            ║"
echo "║  4. Set SLACK_ENABLED=true once your Slack bot is ready.     ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
