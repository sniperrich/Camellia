#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
USER_NAME="${USER_NAME:-admin}"
USER_PASS="${USER_PASS:-admin123}"
DEVICE_ID="${DEVICE_ID:-server-test}"

echo "Base: ${BASE_URL}"

echo "== health =="
curl -s "${BASE_URL}/auth/health" | cat
echo

echo "== register (may fail if disabled) =="
curl -s -X POST "${BASE_URL}/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"${USER_NAME}\",\"password\":\"${USER_PASS}\"}" | cat
echo

echo "== login =="
LOGIN_RESP="$(curl -s -X POST "${BASE_URL}/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"${USER_NAME}\",\"password\":\"${USER_PASS}\",\"device_id\":\"${DEVICE_ID}\"}")"
echo "${LOGIN_RESP}"
echo

ACCESS_TOKEN="$(echo "${LOGIN_RESP}" | sed -n 's/.*"access_token":"\\([^"]*\\)".*/\\1/p')"
REFRESH_TOKEN="$(echo "${LOGIN_RESP}" | sed -n 's/.*"refresh_token":"\\([^"]*\\)".*/\\1/p')"

echo "== verify =="
if [[ -n "${ACCESS_TOKEN}" ]]; then
  curl -s -X POST "${BASE_URL}/auth/verify" \
    -H "Content-Type: application/json" \
    -d "{\"access_token\":\"${ACCESS_TOKEN}\"}" | cat
else
  echo "skip (no access_token)"
fi
echo

echo "== refresh =="
if [[ -n "${REFRESH_TOKEN}" ]]; then
  curl -s -X POST "${BASE_URL}/auth/refresh" \
    -H "Content-Type: application/json" \
    -d "{\"refresh_token\":\"${REFRESH_TOKEN}\",\"device_id\":\"${DEVICE_ID}\"}" | cat
else
  echo "skip (no refresh_token)"
fi
echo

echo "== logout =="
if [[ -n "${REFRESH_TOKEN}" ]]; then
  curl -s -X POST "${BASE_URL}/auth/logout" \
    -H "Content-Type: application/json" \
    -d "{\"refresh_token\":\"${REFRESH_TOKEN}\"}" | cat
else
  echo "skip (no refresh_token)"
fi
echo
