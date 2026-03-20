# Telegram Edge Relay

`telegram-edge-relay` is a small FastAPI service that sits between Telegram and a private backend.

It exists to solve one transport problem: Telegram-facing traffic may need to terminate on a public edge node, while bot logic and application state stay on private infrastructure.

## Purpose

This relay does only transport work:

- accepts Telegram webhooks on a public endpoint
- forwards webhook updates to a private backend
- accepts protected internal outbound requests from the backend
- calls Telegram Bot API methods
- emits request-correlated structured logs

It does not own business logic, bot state, queues, databases, or admin tooling.

## Architecture

```text
Telegram
  -> POST /telegram/webhook/{path_secret}
  -> Relay
  -> POST {BACKEND_BASE_URL}{BACKEND_FORWARD_PATH} with HMAC signature
  -> Private backend

Private backend
  -> POST /internal/telegram/...
  -> Relay
  -> Telegram Bot API
```

Key properties:

- stateless by default
- no shared storage
- no database
- explicit HMAC signing for server-to-server traffic
- explicit timeouts for backend and Telegram calls

## Current Endpoints

System:

- `GET /health`
- `GET /version`

Public:

- `POST /telegram/webhook/{path_secret}`

Internal outbound:

- `POST /internal/telegram/sendMessage`
- `POST /internal/telegram/sendPhoto`
- `POST /internal/telegram/editMessageText`
- `POST /internal/telegram/editMessageCaption`
- `POST /internal/telegram/editMessageMedia`
- `POST /internal/telegram/answerCallbackQuery`
- `POST /internal/telegram/deleteMessage`
- `POST /internal/telegram/sendChatAction`
- `POST /internal/telegram/raw/{method}`

## Quick Start

Requirements:

- Python 3.11+

Create an environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

This installs all runtime dependencies, including `python-multipart`, which FastAPI needs for the internal `sendPhoto` endpoint.

Create a local env file from `.env.example` and set real values.

The relay does not load `.env` automatically. Export the variables in your shell, inject them with your process manager, or use your container/orchestrator environment settings before startup.

Start the relay with the typed config from the environment:

```bash
python -m app.main
```

Alternative startup if you prefer invoking Uvicorn directly:

```bash
uvicorn app.main:create_app --factory --host "${APP_HOST}" --port "${APP_PORT}"
```

Basic checks:

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/version
```

## Environment Variables

The service fails fast on missing or invalid required values.

| Variable | Required | Purpose |
| --- | --- | --- |
| `APP_NAME` | no | Service name exposed by `/health` and `/version` |
| `APP_VERSION` | no | Version exposed by `/health` and `/version`; defaults to the package version |
| `APP_HOST` | yes | Bind address for the relay process |
| `APP_PORT` | yes | Bind port for the relay process |
| `LOG_LEVEL` | yes | Standard log level such as `INFO` or `DEBUG` |
| `TELEGRAM_BOT_TOKEN` | yes | Bot token used for outbound Telegram Bot API calls |
| `TELEGRAM_WEBHOOK_PATH_SECRET` | yes | Secret path component for the public webhook URL |
| `BACKEND_BASE_URL` | yes | Base URL for the private backend |
| `BACKEND_FORWARD_PATH` | yes | Backend path used for forwarded Telegram updates |
| `INTERNAL_SHARED_SECRET` | yes | HMAC secret for relay/backend request signing |
| `SIGNATURE_TTL_SECONDS` | yes | Allowed timestamp skew/window for signed internal requests |
| `TELEGRAM_TIMEOUT_SECONDS` | yes | Timeout for outbound Telegram requests |
| `TELEGRAM_OUTBOUND_MODE` | no | Outbound mode: `typed`, `mixed`, or `proxy`; defaults to `mixed` |
| `TELEGRAM_RESPONSE_MODE` | no | Internal outbound response mode: `normalized` or `transparent`; defaults to `normalized` |
| `TELEGRAM_PHOTO_MAX_BYTES` | no | Optional max upload size for internal `sendPhoto` |
| `BACKEND_TIMEOUT_SECONDS` | yes | Timeout for forwarding webhook updates to the backend |
| `DEBUG` | yes | Enables plain-text logs when `true`; keep `false` in production |

## Health and Version

`GET /health` returns:

- relay status
- app name
- app version
- a safe config summary without secrets

`GET /version` returns only the app name and version.

Use `/health` for first-boot verification and `/version` for deployment identification.

## Webhook Setup

Expose the relay behind HTTPS on a stable public hostname, for example:

```text
https://relay.example.com/telegram/webhook/<TELEGRAM_WEBHOOK_PATH_SECRET>
```

Set the Telegram webhook:

```bash
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://relay.example.com/telegram/webhook/'"${TELEGRAM_WEBHOOK_PATH_SECRET}"'"
  }'
```

The relay:

- validates that `{path_secret}` matches `TELEGRAM_WEBHOOK_PATH_SECRET`
- validates that the body is a JSON object
- forwards the exact raw JSON bytes to the backend
- signs the relay-to-backend request with timestamp + HMAC

Before switching production traffic, verify:

- the webhook URL is publicly reachable over HTTPS
- the deployed path secret matches `TELEGRAM_WEBHOOK_PATH_SECRET`
- the backend endpoint at `{BACKEND_BASE_URL}{BACKEND_FORWARD_PATH}` accepts signed requests from the relay
- the private backend can reach the relay internal API

## Reverse Proxy and TLS

Run the relay behind a reverse proxy or load balancer that handles TLS.

Operational notes:

- terminate TLS before traffic reaches the relay
- do not rewrite the webhook path; Telegram must reach `/telegram/webhook/{TELEGRAM_WEBHOOK_PATH_SECRET}` exactly
- expose only `/telegram/webhook/{secret}`, `/health`, and `/version` to the public internet
- keep internal `/internal/telegram/...` endpoints reachable only from trusted backend infrastructure, private networking, VPN, or strict IP allowlists
- preserve `X-Request-ID` if your proxy already sets one
- forward the request body unchanged
- pass standard `Host` and `X-Forwarded-*` headers normally; the relay does not require special proxy middleware for v1
- set request body limits that fit your expected Telegram webhook and internal upload sizes
- align proxy timeouts with the relay timeouts so the proxy does not fail first

Typical production placement:

```text
Internet -> Nginx/Caddy/ALB -> Relay
```

The relay itself does not manage certificates.

Recommended first deployment shape:

```text
Telegram -> Public TLS proxy -> Relay -> Private backend
Private backend -> Relay internal API -> Telegram
```

## Internal Signing Model

Internal relay-bound and backend-bound signed requests use:

- `X-Relay-Timestamp`
- `X-Relay-Signature`

Signature input:

```text
{timestamp}.{raw_request_body}
```

Signature algorithm:

```text
HMAC-SHA256(INTERNAL_SHARED_SECRET, "{timestamp}.{raw_body}")
```

Validation rules:

- constant-time signature comparison
- timestamp must parse as an integer unix timestamp
- timestamp must fall inside `SIGNATURE_TTL_SECONDS`
- signature must use the `sha256=<hex>` format
- missing or invalid auth fails closed

### Example: Generate Signature for JSON

```bash
BODY='{"chat_id":123456,"text":"hello"}'
TIMESTAMP=$(date +%s)
SIGNATURE=$(printf '%s.%s' "$TIMESTAMP" "$BODY" \
  | openssl dgst -sha256 -hmac "$INTERNAL_SHARED_SECRET" -hex \
  | awk '{print "sha256=" $2}')
```

### Example: Generate Signature for Multipart

For multipart requests, sign the exact encoded request body bytes. In shell, the simplest practical approach is to build the multipart body in the caller and sign that serialized body before sending it. If your backend is not shell-based, do this in code using the same rule:

```text
signature = HMAC_SHA256(secret, f"{timestamp}.{raw_http_body_bytes}")
```

## Outbound API Overview

All internal outbound endpoints:

- require HMAC auth
- return `401` for auth failures
- return `422` for request validation failures

Response modes:

- `normalized`: relay returns its current internal envelope on failures and `{"ok": true, "result": ...}` on success
- `transparent`: relay stays operationally observable, but successful and Telegram-originated error responses are returned as close as possible to native Telegram Bot API responses

In `transparent` mode:

- success responses are `{"ok": true, "result": ...}`
- Telegram JSON errors are returned with the same HTTP status and original Telegram JSON body
- upstream non-JSON errors still return deterministic JSON, including `raw_response_text`
- relay-local failures such as auth, validation, timeout, or transport errors still return deterministic JSON because there is no native Telegram response to mirror

Outbound modes:

- `typed`: only the explicitly implemented typed endpoints are allowed; raw fallback is rejected
- `mixed`: default and recommended mode; typed endpoints remain primary, and `/internal/telegram/raw/{method}` is available as a trusted internal escape hatch
- `proxy`: typed endpoints remain available for compatibility, but both typed and raw requests use the same generic Telegram forwarding engine for JSON, form-urlencoded, and multipart requests

Transport semantics:

- the generic/raw forwarding path is the canonical Telegram transport layer for JSON, form-urlencoded, and multipart requests
- typed endpoints are thin compatibility and safety wrappers over that same transport
- when the same Telegram method is available through both typed and raw paths, the relay aims to produce equivalent outbound Telegram requests

Typed endpoints remain the preferred interface for steady-state integrations that benefit from early validation.
Raw/proxy mode is intended only for trusted internal/private use.

Normalized internal error categories:

- `validation_error`
- `auth_error`
- `operation_not_allowed`
- `relay_timeout`
- `relay_network_error`
- `telegram_http_error`
- `telegram_api_error`

Representative success values:

- object payloads for methods like `sendMessage` or `sendPhoto`
- boolean results for methods like `deleteMessage`

Normalized error envelope fields:

- `error_type`
- `message`
- `status_code`
- optional Telegram metadata for Telegram-originated failures:
  `telegram_status_code`, `telegram_error_code`, `telegram_description`, `telegram_response`, `telegram_response_text`
- optional `details` for validation failures

## Internal API Examples

Base variables:

```bash
RELAY_BASE_URL="https://relay.example.com"
INTERNAL_SHARED_SECRET="replace-me"
```

### sendMessage

```bash
BODY='{"chat_id":123456,"text":"hello from backend"}'
TIMESTAMP=$(date +%s)
SIGNATURE=$(printf '%s.%s' "$TIMESTAMP" "$BODY" \
  | openssl dgst -sha256 -hmac "$INTERNAL_SHARED_SECRET" -hex \
  | awk '{print "sha256=" $2}')

curl -X POST "${RELAY_BASE_URL}/internal/telegram/sendMessage" \
  -H "Content-Type: application/json" \
  -H "X-Relay-Timestamp: ${TIMESTAMP}" \
  -H "X-Relay-Signature: ${SIGNATURE}" \
  -d "$BODY"
```

### editMessageText

```bash
BODY='{"chat_id":123456,"message_id":42,"text":"updated text"}'
TIMESTAMP=$(date +%s)
SIGNATURE=$(printf '%s.%s' "$TIMESTAMP" "$BODY" \
  | openssl dgst -sha256 -hmac "$INTERNAL_SHARED_SECRET" -hex \
  | awk '{print "sha256=" $2}')

curl -X POST "${RELAY_BASE_URL}/internal/telegram/editMessageText" \
  -H "Content-Type: application/json" \
  -H "X-Relay-Timestamp: ${TIMESTAMP}" \
  -H "X-Relay-Signature: ${SIGNATURE}" \
  -d "$BODY"
```

### answerCallbackQuery

```bash
BODY='{"callback_query_id":"1234567890","text":"done","show_alert":false}'
TIMESTAMP=$(date +%s)
SIGNATURE=$(printf '%s.%s' "$TIMESTAMP" "$BODY" \
  | openssl dgst -sha256 -hmac "$INTERNAL_SHARED_SECRET" -hex \
  | awk '{print "sha256=" $2}')

curl -X POST "${RELAY_BASE_URL}/internal/telegram/answerCallbackQuery" \
  -H "Content-Type: application/json" \
  -H "X-Relay-Timestamp: ${TIMESTAMP}" \
  -H "X-Relay-Signature: ${SIGNATURE}" \
  -d "$BODY"
```

### deleteMessage

```bash
BODY='{"chat_id":123456,"message_id":42}'
TIMESTAMP=$(date +%s)
SIGNATURE=$(printf '%s.%s' "$TIMESTAMP" "$BODY" \
  | openssl dgst -sha256 -hmac "$INTERNAL_SHARED_SECRET" -hex \
  | awk '{print "sha256=" $2}')

curl -X POST "${RELAY_BASE_URL}/internal/telegram/deleteMessage" \
  -H "Content-Type: application/json" \
  -H "X-Relay-Timestamp: ${TIMESTAMP}" \
  -H "X-Relay-Signature: ${SIGNATURE}" \
  -d "$BODY"
```

### sendChatAction

```bash
BODY='{"chat_id":123456,"action":"typing"}'
TIMESTAMP=$(date +%s)
SIGNATURE=$(printf '%s.%s' "$TIMESTAMP" "$BODY" \
  | openssl dgst -sha256 -hmac "$INTERNAL_SHARED_SECRET" -hex \
  | awk '{print "sha256=" $2}')

curl -X POST "${RELAY_BASE_URL}/internal/telegram/sendChatAction" \
  -H "Content-Type: application/json" \
  -H "X-Relay-Timestamp: ${TIMESTAMP}" \
  -H "X-Relay-Signature: ${SIGNATURE}" \
  -d "$BODY"
```

### sendPhoto

For `multipart/form-data`, the backend must sign the exact multipart body bytes it sends. The relay accepts a direct upload and forwards the file to Telegram without storing it.

Request fields:

- `chat_id`
- `photo`
- optional caption-related fields from the Bot API subset implemented by the relay

Portable backend advice:

- generate the multipart body in code
- compute HMAC over the exact serialized body bytes
- send that body with matching `X-Relay-Timestamp` and `X-Relay-Signature`

### raw fallback example: sendDice

```bash
BODY='{"chat_id":123456}'
TIMESTAMP=$(date +%s)
SIGNATURE=$(printf '%s.%s' "$TIMESTAMP" "$BODY" \
  | openssl dgst -sha256 -hmac "$INTERNAL_SHARED_SECRET" -hex \
  | awk '{print "sha256=" $2}')

curl -X POST "${RELAY_BASE_URL}/internal/telegram/raw/sendDice" \
  -H "Content-Type: application/json" \
  -H "X-Relay-Timestamp: ${TIMESTAMP}" \
  -H "X-Relay-Signature: ${SIGNATURE}" \
  -d "$BODY"
```

### raw fallback example: getChatMemberCount

```bash
BODY='{"chat_id":123456}'
TIMESTAMP=$(date +%s)
SIGNATURE=$(printf '%s.%s' "$TIMESTAMP" "$BODY" \
  | openssl dgst -sha256 -hmac "$INTERNAL_SHARED_SECRET" -hex \
  | awk '{print "sha256=" $2}')

curl -X POST "${RELAY_BASE_URL}/internal/telegram/raw/getChatMemberCount" \
  -H "Content-Type: application/json" \
  -H "X-Relay-Timestamp: ${TIMESTAMP}" \
  -H "X-Relay-Signature: ${SIGNATURE}" \
  -d "$BODY"
```

### raw fallback multipart note

The raw endpoint also accepts signed `multipart/form-data`, including methods such as `sendDocument` and `editMessageMedia`. As with the typed `sendPhoto` endpoint, the backend must sign the exact serialized multipart body bytes, so this is best implemented in backend code rather than with ad hoc shell `curl -F` commands.

## Forwarded Webhook Contract

When Telegram calls the relay webhook, the relay forwards the raw update body to:

```text
{BACKEND_BASE_URL}{BACKEND_FORWARD_PATH}
```

The forwarded request:

- uses `Content-Type: application/json`
- includes `X-Relay-Timestamp`
- includes `X-Relay-Signature`
- includes `X-Request-ID` when available
- contains the exact raw Telegram JSON bytes

The backend should verify the same HMAC scheme before accepting the forwarded update.

The relay uses the same header names in both directions:

- `X-Relay-Timestamp`
- `X-Relay-Signature`
- `X-Request-ID` when available

## Minimal Backend Integration Notes

On the private backend side, you need only two things:

1. A webhook intake endpoint for forwarded Telegram updates.
2. A small relay client for outbound Telegram method calls.

Recommended backend behavior:

- verify `X-Relay-Timestamp` and `X-Relay-Signature` on inbound forwarded updates
- treat the relay as a transport adapter, not as an application backend
- prefer the typed internal endpoints for your steady-state contract
- use `/internal/telegram/raw/{method}` as the canonical escape hatch for methods or payload shapes not covered by typed wrappers
- reuse the same request-signing code for both directions

When `TELEGRAM_RESPONSE_MODE=transparent`, backend response handling can stay close to direct Telegram Bot API handling. The relay still preserves structured logs and internal classification, but successful and Telegram-originated error responses are returned in Telegram-style JSON whenever possible.

Remaining intentional drift from native Telegram API:

- internal endpoints still require relay HMAC auth and timestamp checks
- relay-local failures such as auth, validation, timeout, or transport errors still return deterministic relay JSON
- typed endpoints still validate a small set of stable fields before forwarding
- typed multipart wrappers may reject malformed inputs earlier than Telegram itself when that improves operational safety

## Python Examples

Small backend integration examples live in `examples/README.md`:

- `examples/backend_client.py`
- `examples/backend_receiver.py`

## Deployment Checklist

1. Populate all required environment variables from [`.env.example`](/Users/antonkobcenko/Documents/projects/telegram-edge-relay/.env.example) with real secrets and endpoints.
2. Install dependencies and start the relay with `python -m app.main` or `uvicorn app.main:create_app --factory`.
3. Put the relay behind a public HTTPS reverse proxy and do not expose `/internal/telegram/...` publicly.
4. Confirm `GET /health` returns `status=ok`, the expected version, and the expected non-secret config summary.
5. Confirm the private backend endpoint at `{BACKEND_BASE_URL}{BACKEND_FORWARD_PATH}` is reachable from the relay and verifies relay signatures.
6. Confirm the private backend can call the relay internal endpoints with valid `X-Relay-Timestamp` and `X-Relay-Signature` headers.
7. Register the Telegram webhook URL `https://<public-host>/telegram/webhook/<TELEGRAM_WEBHOOK_PATH_SECRET>` and verify `setWebhook` succeeds.
8. Send one real webhook and one signed internal outbound request, then verify correlated logs using `X-Request-ID`, route, direction, status, and elapsed time.
