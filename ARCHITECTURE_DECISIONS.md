# ARCHITECTURE_DECISIONS.md

## Purpose

`telegram-edge-relay` is a tiny edge service that isolates all Telegram-facing traffic from the private backend.

It exists for one specific reason:

- Telegram cannot reliably reach the main backend webhook endpoint, and/or
- the main backend cannot reliably reach the Telegram Bot API.

The relay is deployed on a foreign or otherwise stable public server and becomes the only component that communicates directly with Telegram.

---

## Core architectural decision

The relay is a **transport boundary**, not an application backend.

It must do only two classes of work:

1. **Inbound**
   - receive Telegram webhook updates
   - validate and normalize the request
   - forward the update to the private backend over a protected channel

2. **Outbound**
   - accept a small internal API call from the private backend
   - send the corresponding Bot API request to Telegram
   - return Telegram's response in a predictable format

Everything else belongs to the private backend.

---

## Strict boundary of responsibility

### The relay owns

- Telegram webhook ingress
- outbound Bot API calls
- request authentication at the relay boundary
- transport-level observability
- very small health and diagnostics surface

### The relay does not own

- business logic
- bot state
- FSM
- user data
- database writes
- queues for domain workflows
- analytics
- AI or LLM logic
- rich admin UI
- bot framework features

---

## Deployment model

### Public edge node

The relay is deployed on a public server with stable Telegram connectivity.

Requirements:

- public IP or stable public domain
- TLS termination
- reliable outbound access to Telegram
- access to the private backend over a protected channel

### Private backend

The backend remains on private infrastructure and exposes only the minimal internal surface required by the relay.

---

## Communication model

### Telegram -> Relay

Telegram sends webhook updates to the relay using HTTPS.

Recommended endpoint shape:

- `POST /telegram/webhook/{path_secret}`

The path secret is not the only protection, but it reduces unsolicited noise.

### Relay -> Backend

The relay forwards webhook updates to the backend using an internal HTTPS call with request signing.

Recommended endpoint shape:

- `POST /internal/inbound/telegram-update`

### Backend -> Relay

The backend calls the relay's internal API for outbound Telegram actions.

Recommended endpoint examples:

- `POST /internal/telegram/sendMessage`
- `POST /internal/telegram/sendPhoto`
- `POST /internal/telegram/editMessageText`
- `POST /internal/telegram/editMessageCaption`
- `POST /internal/telegram/answerCallbackQuery`

---

## Security decision

Transport security is mandatory.

The relay must support:

- HTTPS
- shared secret authentication for internal calls
- signed requests between relay and backend
- request timestamp validation
- replay-window validation
- optional IP allowlisting

Preferred default:

- HTTPS + HMAC request signing + timestamp + nonce window

The relay should be stateless by default and should not require a database.

---

## Data handling decision

The relay should minimize data retention.

Default behavior:

- do not persist Telegram updates
- do not persist outbound request payloads
- do not persist user content unless explicitly enabled for debugging
- log only metadata and error context
- redact secrets and tokens from logs

If optional debug logging is enabled, it must be clearly separated and easy to disable.

---

## Error handling decision

The relay must be explicit and boring.

### Inbound forwarding

If the backend is unavailable or returns a transport error:

- the relay must return a clear non-2xx response to Telegram when appropriate
- errors must be logged with correlation identifiers
- retry behavior should be kept simple and predictable

### Outbound sending

If Telegram returns an error:

- return the HTTP status and Telegram response body in normalized form
- distinguish transport errors from Telegram API errors
- do not silently swallow errors except for explicitly whitelisted harmless cases

Examples of harmless cases:

- `message is not modified`
- stale callback query acknowledgements when configured to soft-ignore

---

## API design decision

The relay API must remain narrow and stable.

### Public API

Only the Telegram webhook receiver should be public.

### Internal API

The internal API should mirror only a small subset of frequently needed Bot API methods.

Do not try to expose the entire Bot API on day one.

Initial target set:

- `sendMessage`
- `sendPhoto`
- `editMessageText`
- `editMessageCaption`
- `answerCallbackQuery`
- `deleteMessage`
- `sendChatAction`

Later expansion is allowed only if demanded by real integration needs.

---

## Protocol shape decision

Internal requests should use compact JSON.

Multipart complexity should remain inside the relay.

Example principle:

- the backend sends JSON metadata and references a file path, URL, or binary upload contract
- the relay converts it into the actual Telegram Bot API request

The public and internal APIs should not leak unnecessary Telegram transport complexity upstream.

---

## File handling decision

File handling should stay minimal.

For v1, prefer one of these approaches:

1. backend uploads a file directly to the relay internal endpoint
2. backend provides a short-lived signed URL
3. backend and relay share a private storage path only if deployment constraints require it

Preferred default for simplicity and portability:

- direct upload to relay internal endpoint for small/medium files

Avoid coupling v1 to shared storage.

---

## Framework and stack decision

Preferred stack for v1:

- Python
- FastAPI
- httpx
- Pydantic
- uvicorn
- standard logging
- no database

Why:

- fast implementation
- small dependency set
- easy deployment
- strong request validation
- good async support

---

## Configuration decision

Configuration must be environment-driven.

Required config categories:

- Telegram token
- webhook secret/path secret
- internal shared secret
- backend base URL
- timeouts
- logging level
- optional debug mode

Configuration loading must be centralized and typed.

---

## Observability decision

The relay needs only lightweight observability.

Required:

- request ID / correlation ID
- structured logs
- transport timings
- target endpoint labels
- health endpoint
- version endpoint

Nice to have later:

- Prometheus metrics
- latency histograms
- success/error counters by method

Do not block v1 on advanced observability.

---

## Scaling decision

The relay is intentionally single-purpose and horizontally stateless.

Expected v1 scaling model:

- multiple identical instances behind a reverse proxy or load balancer
- no sticky sessions required
- no internal persistent state required

If future multi-instance scaling becomes necessary, the relay should still avoid owning domain state.

---

## Anti-bloat decision

Any proposed feature must answer:

1. Does this improve Telegram transport reliability?
2. Does this belong to transport rather than business logic?
3. Can it be implemented without making the relay framework-like?
4. Can the whole service still be understood quickly?

If not, the feature probably does not belong here.

---

## Final principle

Keep Telegram at the edge.
Keep application logic in the core.
Keep the relay small enough to trust.