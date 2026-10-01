#!/usr/bin/env bash
# 조회·검증 API를 Cloud Run(서울)에 배포한다. 사용법: deploy/cloudrun.sh <프로젝트 ID>
# 처음 실행할 때 필요한 API를 켜고, 전용 서비스 계정과 이미지 정리 정책을 만든다(다시 실행해도 안전하다).
set -euo pipefail

PROJECT_ID="${1:?사용법: deploy/cloudrun.sh <프로젝트 ID>}"
REGION="${REGION:-asia-northeast3}"
SERVICE="chabom-api"
SA="${SERVICE}@${PROJECT_ID}.iam.gserviceaccount.com"
cd "$(dirname "$0")/.."

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  firestore.googleapis.com --project "$PROJECT_ID"

# API 서버는 Firestore의 허용 목록·조회 한도만 읽고 쓴다. 기본 계정 대신 권한을 좁힌 전용 계정을 쓴다.
if ! gcloud iam service-accounts describe "$SA" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SERVICE" --project "$PROJECT_ID" --display-name "차봄 API"
fi
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$SA" \
  --role roles/datastore.user --condition=None >/dev/null

# 요청이 있을 때만 CPU를 쓰고(min 0), 인스턴스 수를 막아 무료 한도를 넘지 않게 한다.
# 인증은 앱이 Firebase ID 토큰으로 직접 검사하므로 Cloud Run 자체는 공개로 둔다(Hosting rewrite에 필요).
gcloud run deploy "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --source . \
  --service-account "$SA" --allow-unauthenticated \
  --memory 1Gi --cpu 1 --cpu-throttling --min-instances 0 --max-instances 2 \
  --concurrency 4 --timeout 120 \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID},LOOKUP_DAILY_LIMIT=${LOOKUP_DAILY_LIMIT:-30}"

# 소스 배포가 만든 이미지 저장소에 최신 이미지만 남긴다(Artifact Registry 무료 0.5GB 안에 두기 위해).
gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy --project "$PROJECT_ID" \
  --location "$REGION" --policy deploy/artifact-cleanup-policy.json --no-dry-run >/dev/null

echo "배포 완료: $(gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --format 'value(status.url)')"
