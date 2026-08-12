#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

REPORT="${GITHUB_WORKSPACE}/issue-246-authenticated-phase1-v2.json"
STATUS="failed"
REASON="unexpected_failure"
AUTH_MODE="none"
VOLUME_ID=""
VOLUME_NAME=""
VOLUME_STATUS=""
VOLUME_CREATED="false"
CONFIG_VERIFIED="false"
DEVICE_BUNDLE_PUBLISHED="false"
KEEP_DEVICE_BUNDLE="false"
LOGIN_PID=""

write_report() {
  STATUS="$STATUS" REASON="$REASON" AUTH_MODE="$AUTH_MODE" \
  VOLUME_ID="$VOLUME_ID" VOLUME_NAME="$VOLUME_NAME" VOLUME_STATUS="$VOLUME_STATUS" \
  VOLUME_CREATED="$VOLUME_CREATED" CONFIG_VERIFIED="$CONFIG_VERIFIED" \
  DEVICE_BUNDLE_PUBLISHED="$DEVICE_BUNDLE_PUBLISHED" REPORT="$REPORT" \
  python - <<'PY'
import json
import os
from datetime import UTC, datetime
from pathlib import Path

payload = {
    "status": os.environ.get("STATUS", "failed"),
    "reason": os.environ.get("REASON", "unexpected_failure"),
    "generated_at": datetime.now(UTC).isoformat(),
    "authentication_mode": os.environ.get("AUTH_MODE", "none"),
    "device_bundle_published": os.environ.get("DEVICE_BUNDLE_PUBLISHED") == "true",
    "project_id": os.environ["PROJECT_ID"],
    "environment_id": os.environ["ENVIRONMENT_ID"],
    "service": {
        "id": os.environ["BACKUP_SERVICE_ID"],
        "name": os.environ["BACKUP_SERVICE_NAME"],
        "config_file": os.environ["EXPECTED_CONFIG_FILE"],
        "cron_schedule": os.environ["EXPECTED_CRON"],
        "start_command": os.environ["EXPECTED_START_COMMAND"],
        "dockerfile_path": os.environ["EXPECTED_DOCKERFILE"],
        "restart_policy": "NEVER",
        "config_verified": os.environ.get("CONFIG_VERIFIED") == "true",
    },
    "volume": {
        "id": os.environ.get("VOLUME_ID") or None,
        "name": os.environ.get("VOLUME_NAME") or None,
        "mount_path": os.environ["EXPECTED_MOUNT_PATH"],
        "status": os.environ.get("VOLUME_STATUS") or None,
        "created_in_this_run": os.environ.get("VOLUME_CREATED") == "true",
    },
    "backup_executed": False,
    "restore_executed": False,
    "secret_values_included": False,
}
Path(os.environ["REPORT"]).write_text(
    json.dumps(payload, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
}

github_api() {
  curl --fail --silent --show-error \
    -H "Authorization: Bearer ${GH_API_TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "$@"
}

delete_device_bundle() {
  local metadata sha payload
  metadata="$RUNNER_TEMP/device-bundle-metadata.json"
  if github_api \
    "https://api.github.com/repos/${GITHUB_REPOSITORY}/contents/${DEVICE_BUNDLE_PATH}?ref=${GITHUB_REF_NAME}" \
    --output "$metadata" 2>/dev/null; then
    sha="$(python -c 'import json,sys; print(json.load(open(sys.argv[1])).get("sha", ""))' "$metadata")"
    if [[ -n "$sha" ]]; then
      payload="$RUNNER_TEMP/device-bundle-delete.json"
      SHA="$sha" PAYLOAD="$payload" python - <<'PY'
import json
import os
from pathlib import Path
Path(os.environ["PAYLOAD"]).write_text(json.dumps({
    "message": "ops: remove short-lived Railway device authorization bundle",
    "sha": os.environ["SHA"],
    "branch": os.environ["GITHUB_REF_NAME"],
}), encoding="utf-8")
PY
      github_api -X DELETE \
        "https://api.github.com/repos/${GITHUB_REPOSITORY}/contents/${DEVICE_BUNDLE_PATH}" \
        -H "Content-Type: application/json" \
        --data-binary "@$payload" >/dev/null 2>&1 || true
    fi
  fi
}

publish_encrypted_bundle() {
  local cleartext="$1"
  local public_key encrypted content payload existing sha
  public_key="$RUNNER_TEMP/device-auth-public.pem"
  encrypted="$RUNNER_TEMP/device-auth.enc"
  payload="$RUNNER_TEMP/device-bundle-upload.json"
  cat > "$public_key" <<'PEM'
-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAu9iZ6TEPvMIFp9+5FrbE
E/aM2dqZ+qcIqqj+VmzQDCr640ZBswjfduJrEgMrsj/Rnj+v+fq8OMYUlfEq9V4c
q2yERL6Nx5ForMne73v8e4bvR/8iYoNACipTCiEdcNLJ8NGp32imI2NZt9ZwAUSR
6TM2KmWwNV2To1hRQMk7Dxx9Z1zKtUlzh8tXM/IeglXCD5J214DponX6pOAY9ykH
oPXH48wzZZu0sOyBowBZaaX+wgpXd906tqGTPhvWI7av0io2ynIH2Fem3QPnrSYb
rtY6gTWzaqHjk52c+s7kDSSJGxKwLCYy526PLcd+TP5scfQHONLi4Aj7+hLToJjc
BIu0qYpCJquYZ5RvwDAqcHViqxQvIZKHrgKk2MlDxO3OtgEaLv1D6ATVWQqj7T49
CbEUNSVU01seH5LTrZobJEsZJQ1ZSmrgVU+a35uQYETU7g6utiaXXBJBggHuBvvU
iMROTvTpuqzxCHpbdt8Xu56luXGyuycsHItabxGYvjoSJwqF4LhzuOg/yyhwTFnK
TMoZUIpGIs7B3YoovuisJvJjs+2jck7s9WC7n+soqm0Ul70p+NR2694uBowvHMMx
a+as76DoCN9/tKYomglXe7ua/IYtI9PipBrqBo7RvpjV0XiI7zct/roWbBcybaEZ
JvUELFARB30MAxq4iRcZN9kCAwEAAQ==
-----END PUBLIC KEY-----
PEM
  openssl pkeyutl -encrypt -pubin -inkey "$public_key" \
    -pkeyopt rsa_padding_mode:oaep \
    -pkeyopt rsa_oaep_md:sha256 \
    -in "$cleartext" -out "$encrypted"
  content="$(base64 -w 0 "$encrypted")"

  existing="$RUNNER_TEMP/device-bundle-existing.json"
  sha=""
  if github_api \
    "https://api.github.com/repos/${GITHUB_REPOSITORY}/contents/${DEVICE_BUNDLE_PATH}?ref=${GITHUB_REF_NAME}" \
    --output "$existing" 2>/dev/null; then
    sha="$(python -c 'import json,sys; print(json.load(open(sys.argv[1])).get("sha", ""))' "$existing")"
  fi

  CONTENT="$content" SHA="$sha" PAYLOAD="$payload" python - <<'PY'
import json
import os
from pathlib import Path
body = {
    "message": "ops: publish encrypted short-lived Railway authorization bundle",
    "content": os.environ["CONTENT"],
    "branch": os.environ["GITHUB_REF_NAME"],
}
if os.environ.get("SHA"):
    body["sha"] = os.environ["SHA"]
Path(os.environ["PAYLOAD"]).write_text(json.dumps(body), encoding="utf-8")
PY
  github_api -X PUT \
    "https://api.github.com/repos/${GITHUB_REPOSITORY}/contents/${DEVICE_BUNDLE_PATH}" \
    -H "Content-Type: application/json" \
    --data-binary "@$payload" >/dev/null
  DEVICE_BUNDLE_PUBLISHED="true"
}

cleanup() {
  write_report
  if [[ -n "$LOGIN_PID" ]]; then
    kill "$LOGIN_PID" 2>/dev/null || true
  fi
  if [[ "$DEVICE_BUNDLE_PUBLISHED" == "true" && "$KEEP_DEVICE_BUNDLE" != "true" ]]; then
    delete_device_bundle
  fi
}
trap cleanup EXIT

if [[ -n "${RAILWAY_API_TOKEN:-}" ]]; then
  AUTH_MODE="api_token"
  unset RAILWAY_TOKEN
elif [[ -n "${RAILWAY_TOKEN:-}" ]]; then
  AUTH_MODE="project_token"
  unset RAILWAY_API_TOKEN
else
  AUTH_MODE="device_code"
  login_output="$RUNNER_TEMP/railway-device-login.out"
  login_rc="$RUNNER_TEMP/railway-device-login.rc"
  (
    set +e
    railway login --browserless >"$login_output" 2>&1
    printf '%s\n' "$?" >"$login_rc"
  ) &
  LOGIN_PID="$!"

  bundle="$RUNNER_TEMP/device-auth.json"
  parsed="false"
  for _ in $(seq 1 90); do
    if [[ -s "$login_output" ]]; then
      if LOGIN_OUTPUT="$login_output" BUNDLE="$bundle" python - <<'PY'
import json
import os
import re
from pathlib import Path

text = Path(os.environ["LOGIN_OUTPUT"]).read_text(encoding="utf-8", errors="replace")
text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
urls = list(dict.fromkeys(item.rstrip(".,);]") for item in re.findall(r"https://[^\s]+", text)))
if not urls:
    raise SystemExit(1)
one_click = max(urls, key=len)
verification = min(urls, key=len)
code = ""
for index, line in enumerate(line.strip() for line in text.splitlines()):
    if "enter this code" in line.casefold():
        lines = [item.strip(" →") for item in text.splitlines()[index + 1:]]
        code = next((item for item in lines if re.fullmatch(r"[A-Z0-9][A-Z0-9-]{3,20}", item)), "")
        break
if not code:
    candidates = re.findall(r"\b[A-Z0-9]{3,8}(?:-[A-Z0-9]{3,8})+\b", text)
    code = candidates[-1] if candidates else ""
payload = {"one_click_url": one_click, "verification_url": verification, "user_code": code}
encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
if len(encoded) > 400:
    payload = {"one_click_url": one_click, "user_code": code}
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
if len(encoded) > 400:
    raise SystemExit(1)
Path(os.environ["BUNDLE"]).write_bytes(encoded)
PY
      then
        parsed="true"
        break
      fi
    fi
    if ! kill -0 "$LOGIN_PID" 2>/dev/null; then
      break
    fi
    sleep 1
  done

  if [[ "$parsed" != "true" ]]; then
    diagnostic="$RUNNER_TEMP/device-auth-diagnostic.json"
    LOGIN_OUTPUT="$login_output" LOGIN_RC="$login_rc" DIAGNOSTIC="$diagnostic" python - <<'PY'
import json
import os
import re
from pathlib import Path
text = ""
path = Path(os.environ["LOGIN_OUTPUT"])
if path.exists():
    text = path.read_text(encoding="utf-8", errors="replace")
text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
text = " ".join(text.split())[:300]
rc = None
rc_path = Path(os.environ["LOGIN_RC"])
if rc_path.exists():
    try:
        rc = int(rc_path.read_text(encoding="utf-8").strip())
    except ValueError:
        rc = None
Path(os.environ["DIAGNOSTIC"]).write_text(
    json.dumps({"diagnostic": text or "no_output", "exit_code": rc}, separators=(",", ":")),
    encoding="utf-8",
)
PY
    publish_encrypted_bundle "$diagnostic"
    KEEP_DEVICE_BUNDLE="true"
    REASON="device_diagnostic_published"
    exit 1
  fi

  publish_encrypted_bundle "$bundle"
  if ! wait "$LOGIN_PID"; then
    REASON="device_authentication_failed_or_expired"
    exit 1
  fi
  LOGIN_PID=""
  if [[ ! -f "$login_rc" || "$(cat "$login_rc")" != "0" ]]; then
    REASON="device_authentication_failed_or_expired"
    exit 1
  fi
fi

if ! railway whoami >/dev/null 2>"$RUNNER_TEMP/railway-auth-check.err"; then
  rm -f "$RUNNER_TEMP/railway-auth-check.err"
  REASON="railway_authentication_not_usable"
  exit 1
fi
rm -f "$RUNNER_TEMP/railway-auth-check.err"

volumes="$RUNNER_TEMP/volumes.json"
if ! railway volume list --project "$PROJECT_ID" --environment "$ENVIRONMENT_ID" --json \
  >"$volumes" 2>"$RUNNER_TEMP/volume-list.err"; then
  rm -f "$RUNNER_TEMP/volume-list.err"
  REASON="volume_list_failed"
  exit 1
fi
rm -f "$RUNNER_TEMP/volume-list.err"

volume_state="$RUNNER_TEMP/volume-state.json"
VOLUMES="$volumes" OUTPUT="$volume_state" python - <<'PY'
import json
import os
from pathlib import Path
data = json.loads(Path(os.environ["VOLUMES"]).read_text(encoding="utf-8"))
matches = [v for v in data.get("volumes", []) if v.get("serviceName") == os.environ["BACKUP_SERVICE_NAME"]]
if len(matches) > 1:
    raise SystemExit("multiple_backup_volumes")
result = {"count": len(matches)}
if matches:
    v = matches[0]
    result.update({
        "id": str(v.get("id") or ""),
        "name": str(v.get("name") or ""),
        "mount_path": str(v.get("mountPath") or ""),
        "status": str(v.get("status") or ""),
        "pending": bool(v.get("isPendingDeletion")),
    })
Path(os.environ["OUTPUT"]).write_text(json.dumps(result), encoding="utf-8")
PY
count="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["count"])' "$volume_state")"
if [[ "$count" == "1" ]]; then
  VOLUME_ID="$(python -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id", ""))' "$volume_state")"
  VOLUME_NAME="$(python -c 'import json,sys; print(json.load(open(sys.argv[1])).get("name", ""))' "$volume_state")"
  mount_path="$(python -c 'import json,sys; print(json.load(open(sys.argv[1])).get("mount_path", ""))' "$volume_state")"
  pending="$(python -c 'import json,sys; print(str(json.load(open(sys.argv[1])).get("pending", False)).lower())' "$volume_state")"
  [[ "$mount_path" == "$EXPECTED_MOUNT_PATH" ]] || { REASON="existing_volume_wrong_mount"; exit 1; }
  [[ "$pending" != "true" ]] || { REASON="existing_volume_pending_deletion"; exit 1; }
else
  created="$RUNNER_TEMP/volume-created.json"
  if ! railway volume add \
    --project "$PROJECT_ID" --environment "$ENVIRONMENT_ID" \
    --service "$BACKUP_SERVICE_ID" --mount-path "$EXPECTED_MOUNT_PATH" --json \
    >"$created" 2>"$RUNNER_TEMP/volume-create.err"; then
    rm -f "$RUNNER_TEMP/volume-create.err"
    REASON="volume_create_failed"
    exit 1
  fi
  rm -f "$RUNNER_TEMP/volume-create.err"
  VOLUME_ID="$(python -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id", ""))' "$created")"
  VOLUME_NAME="$(python -c 'import json,sys; print(json.load(open(sys.argv[1])).get("name", ""))' "$created")"
  [[ -n "$VOLUME_ID" ]] || { REASON="volume_create_response_invalid"; exit 1; }
  VOLUME_CREATED="true"
fi

ready="false"
for _ in $(seq 1 30); do
  railway volume list --project "$PROJECT_ID" --environment "$ENVIRONMENT_ID" --json >"$volumes" 2>/dev/null || true
  CHECK="$volumes" OUTPUT="$volume_state" VOLUME_ID="$VOLUME_ID" python - <<'PY' || true
import json
import os
from pathlib import Path
data = json.loads(Path(os.environ["CHECK"]).read_text(encoding="utf-8"))
matches = [v for v in data.get("volumes", []) if v.get("serviceName") == os.environ["BACKUP_SERVICE_NAME"]]
result = {"verified": False}
if len(matches) == 1:
    v = matches[0]
    result = {
        "verified": (
            str(v.get("id") or "") == os.environ["VOLUME_ID"]
            and str(v.get("mountPath") or "") == os.environ["EXPECTED_MOUNT_PATH"]
            and not bool(v.get("isPendingDeletion"))
            and str(v.get("status") or "").casefold() == "ready"
        ),
        "id": str(v.get("id") or ""),
        "name": str(v.get("name") or ""),
        "status": str(v.get("status") or ""),
    }
Path(os.environ["OUTPUT"]).write_text(json.dumps(result), encoding="utf-8")
PY
  if [[ "$(python -c 'import json,sys; print(str(json.load(open(sys.argv[1])).get("verified", False)).lower())' "$volume_state")" == "true" ]]; then
    VOLUME_ID="$(python -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id", ""))' "$volume_state")"
    VOLUME_NAME="$(python -c 'import json,sys; print(json.load(open(sys.argv[1])).get("name", ""))' "$volume_state")"
    VOLUME_STATUS="$(python -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status", ""))' "$volume_state")"
    ready="true"
    break
  fi
  sleep 4
done
[[ "$ready" == "true" ]] || { REASON="volume_not_ready"; exit 1; }

mutation="$RUNNER_TEMP/update-service.graphql"
variables="$RUNNER_TEMP/update-service.json"
cat >"$mutation" <<'GRAPHQL'
mutation UpdateBackupService($serviceId: String!, $environmentId: String!, $input: ServiceInstanceUpdateInput!) {
  serviceInstanceUpdate(serviceId: $serviceId, environmentId: $environmentId, input: $input)
}
GRAPHQL
VARIABLES="$variables" python - <<'PY'
import json
import os
from pathlib import Path
Path(os.environ["VARIABLES"]).write_text(json.dumps({
    "serviceId": os.environ["BACKUP_SERVICE_ID"],
    "environmentId": os.environ["ENVIRONMENT_ID"],
    "input": {
        "railwayConfigFile": os.environ["EXPECTED_CONFIG_FILE"],
        "cronSchedule": os.environ["EXPECTED_CRON"],
        "startCommand": os.environ["EXPECTED_START_COMMAND"],
        "dockerfilePath": os.environ["EXPECTED_DOCKERFILE"],
        "restartPolicyType": "NEVER",
    },
}), encoding="utf-8")
PY
if ! railway api --file "$mutation" --variables "@$variables" --compact \
  >"$RUNNER_TEMP/update-service-result.json" 2>"$RUNNER_TEMP/update-service.err"; then
  rm -f "$RUNNER_TEMP/update-service.err"
  REASON="service_config_update_failed"
  exit 1
fi
rm -f "$RUNNER_TEMP/update-service.err"

query="$RUNNER_TEMP/read-config.graphql"
query_variables="$RUNNER_TEMP/read-config.json"
cat >"$query" <<'GRAPHQL'
query ReadEnvironmentConfig($id: String!, $decryptVariables: Boolean) {
  environment(id: $id) { config(decryptVariables: $decryptVariables) }
}
GRAPHQL
QUERY_VARIABLES="$query_variables" python - <<'PY'
import json
import os
from pathlib import Path
Path(os.environ["QUERY_VARIABLES"]).write_text(json.dumps({
    "id": os.environ["ENVIRONMENT_ID"],
    "decryptVariables": False,
}), encoding="utf-8")
PY
raw="$RUNNER_TEMP/read-config-raw.json"
safe="$RUNNER_TEMP/read-config-safe.json"
for _ in $(seq 1 24); do
  railway api --file "$query" --variables "@$query_variables" --compact >"$raw" 2>/dev/null || true
  RAW="$raw" SAFE="$safe" python - <<'PY' || true
import json
import os
from pathlib import Path
data = json.loads(Path(os.environ["RAW"]).read_text(encoding="utf-8"))
service = data.get("data", {}).get("environment", {}).get("config", {}).get("services", {}).get(os.environ["BACKUP_SERVICE_ID"], {})
deploy = service.get("deploy") or {}
build = service.get("build") or {}
mounts = service.get("volumeMounts") or {}
mount_paths = sorted(str(v.get("mountPath") or "") for v in mounts.values() if isinstance(v, dict))
result = {
    "verified": (
        str(service.get("configFile") or "") == os.environ["EXPECTED_CONFIG_FILE"]
        and str(deploy.get("cronSchedule") or "") == os.environ["EXPECTED_CRON"]
        and str(deploy.get("startCommand") or "") == os.environ["EXPECTED_START_COMMAND"]
        and str(deploy.get("restartPolicyType") or "").upper() == "NEVER"
        and str(build.get("dockerfilePath") or "") == os.environ["EXPECTED_DOCKERFILE"]
        and mount_paths == [os.environ["EXPECTED_MOUNT_PATH"]]
    )
}
Path(os.environ["SAFE"]).write_text(json.dumps(result), encoding="utf-8")
PY
  if [[ -f "$safe" && "$(python -c 'import json,sys; print(str(json.load(open(sys.argv[1])).get("verified", False)).lower())' "$safe")" == "true" ]]; then
    CONFIG_VERIFIED="true"
    break
  fi
  sleep 4
done
[[ "$CONFIG_VERIFIED" == "true" ]] || { REASON="service_config_readback_failed"; exit 1; }

STATUS="pass"
REASON="none"
write_report
trap - EXIT
if [[ "$DEVICE_BUNDLE_PUBLISHED" == "true" ]]; then
  delete_device_bundle
fi
