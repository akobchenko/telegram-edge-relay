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
- `POST /internal/telegram/answerCallbackQuery`
- `POST /internal/telegram/deleteMessage`
- `POST /internal/telegram/sendChatAction`

## Quick Start

Requirements:

- Python 3.12+

Create an environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Create a local env file from [`.env.example`](/Users/antonkobcenko/Documents/projects/telegram-edge-relay/.env.example) and set real values.

Run the server:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080
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
| `TELEGRAM_PHOTO_MAX_BYTES` | no | Optional max upload size for internal `sendPhoto` |
| `BACKEND_TIMEOUT_SECONDS` | yes | Timeout for forwarding webhook updates to the backend |
| `DEBUG` | yes | Enables plain-text logs when `true`; keep `false` in production |

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

## Reverse Proxy and TLS

Run the relay behind a reverse proxy or load balancer that handles TLS.

Operational notes:

- terminate TLS before traffic reaches the relay
- restrict public exposure to the webhook and system endpoints only
- keep internal `/internal/telegram/...` endpoints reachable only from trusted backend infrastructure
- preserve `X-Request-ID` if your proxy already sets one
- set request body limits that fit your expected Telegram webhook and internal upload sizes

Typical production placement:

```text
Internet -> Nginx/Caddy/ALB -> Relay
```

The relay itself does not manage certificates.

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
- return a normalized envelope
- return `{"ok": true, "result": ...}` on success
- return `{"ok": false, ...}` on failure

Normalized internal error categories:

- `validation_error`
- `auth_error`
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
- optional Telegram metadata for Telegram-originated failures
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

## Minimal Backend Integration Notes

On the private backend side, you need only two things:

1. A webhook intake endpoint for forwarded Telegram updates.
2. A small relay client for outbound Telegram method calls.

Recommended backend behavior:

- verify `X-Relay-Timestamp` and `X-Relay-Signature` on inbound forwarded updates
- treat the relay as a transport adapter, not as an application backend
- keep outbound requests explicit per Telegram method you actually use
- reuse the same request-signing code for both directions

The relay contract is intentionally narrow. If a bot method is not implemented, add it explicitly rather than exposing the full Telegram Bot API surface by default.

## Python Examples

Small backend integration examples live in [`examples/README.md`](/Users/antonkobcenko/Documents/projects/telegram-edge-relay/examples/README.md):

- [`examples/backend_client.py`](/Users/antonkobcenko/Documents/projects/telegram-edge-relay/examples/backend_client.py)
- [`examples/backend_receiver.py`](/Users/antonkobcenko/Documents/projects/telegram-edge-relay/examples/backend_receiver.py)
