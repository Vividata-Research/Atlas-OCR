#!/usr/bin/env bash
set -euo pipefail

# ---------- Config ----------
# Pass profile and region via env vars:
#   AWS_PROFILE=my-admin AWS_REGION=eu-west-2 ./setup-ecr-ptc-dockerhub.sh
#
# Or rely on your default AWS CLI config if unset.
PROFILE_OPT=${AWS_PROFILE:+--profile "$AWS_PROFILE"}
REGION_OPT=${AWS_REGION:+--region "$AWS_REGION"}

# Prefix under which cached repos will be created in ECR.
ECR_REPO_PREFIX="docker"

# Name for the Secrets Manager secret (must start with ecr-pullthroughcache/)
SECRET_NAME="ecr-pullthroughcache/dockerhub"

UPSTREAM_REGISTRY_URL="registry-1.docker.io"
UPSTREAM_REGISTRY_NAME="docker-hub"
# ----------------------------

# Require aws + jq
for cmd in aws jq; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "$cmd not found"; exit 1; }
done

# Discover account and region
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text $PROFILE_OPT)
AWS_REGION=${AWS_REGION:-$(aws configure get region $PROFILE_OPT)}

if [[ -z "$AWS_REGION" ]]; then
  echo "No region found. Please set AWS_REGION or configure a default region in AWS CLI."
  exit 1
fi

echo "Using AWS account: ${AWS_ACCOUNT_ID}, region: ${AWS_REGION}, profile: ${AWS_PROFILE:-default}"
echo

# Collect Docker Hub credentials
DOCKERHUB_USERNAME="${DOCKERHUB_USERNAME:-}"
DOCKERHUB_TOKEN="${DOCKERHUB_TOKEN:-}"

if [[ -z "${DOCKERHUB_USERNAME}" ]]; then
  read -rp "Docker Hub username: " DOCKERHUB_USERNAME
fi
if [[ -z "${DOCKERHUB_TOKEN}" ]]; then
  read -rsp "Docker Hub access token (input hidden): " DOCKERHUB_TOKEN; echo
fi

SECRET_PAYLOAD=$(jq -cn --arg u "$DOCKERHUB_USERNAME" --arg t "$DOCKERHUB_TOKEN" '{username:$u, accessToken:$t}')

echo "Ensuring Secrets Manager secret '${SECRET_NAME}' exists..."
set +e
aws secretsmanager describe-secret --secret-id "${SECRET_NAME}" $PROFILE_OPT $REGION_OPT >/dev/null 2>&1
EXISTS=$?
set -e

if [[ $EXISTS -ne 0 ]]; then
  echo "Creating secret..."
  SECRET_ARN=$(aws secretsmanager create-secret \
    --name "${SECRET_NAME}" \
    --secret-string "${SECRET_PAYLOAD}" \
    $PROFILE_OPT $REGION_OPT \
    --query ARN --output text)
else
  echo "Updating secret..."
  SECRET_ARN=$(aws secretsmanager update-secret \
    --secret-id "${SECRET_NAME}" \
    --secret-string "${SECRET_PAYLOAD}" \
    $PROFILE_OPT $REGION_OPT \
    --query ARN --output text)
fi

echo
echo "Creating Pull-Through Cache rule..."
set +e
CREATE_OUTPUT=$(aws ecr create-pull-through-cache-rule \
  --ecr-repository-prefix "${ECR_REPO_PREFIX}" \
  --upstream-registry-url "${UPSTREAM_REGISTRY_URL}" \
  --upstream-registry "${UPSTREAM_REGISTRY_NAME}" \
  --credential-arn "${SECRET_ARN}" \
  $PROFILE_OPT $REGION_OPT 2>&1)
STATUS=$?
set -e

if [[ $STATUS -ne 0 ]]; then
  if echo "$CREATE_OUTPUT" | grep -qi "already exists"; then
    echo "Rule already exists, skipping."
  else
    echo "Failed to create rule:"
    echo "$CREATE_OUTPUT"
    exit 1
  fi
else
  echo "Pull-through cache rule created."
fi

ECR_HOST="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
CACHE_FROM="${ECR_HOST}/${ECR_REPO_PREFIX}/vllm/vllm-openai:v0.9.1"

cat <<EOF

Done!

Use this in your Dockerfile:

  FROM ${CACHE_FROM}

Authenticate & test pull:

  aws ecr get-login-password --region ${AWS_REGION} $PROFILE_OPT \\
    | docker login --username AWS --password-stdin ${ECR_HOST}

  docker pull ${CACHE_FROM}
EOF
