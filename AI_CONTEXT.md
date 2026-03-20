# AI_CONTEXT.md

## Project summary

`telegram-edge-relay` is a tiny Telegram transport service.

It runs on a public or foreign edge server and acts as a relay between Telegram and a private backend.

It solves a narrow but important problem:

- Telegram-facing traffic may be unreliable or blocked from the private backend
- the private backend still needs to keep business logic, storage, AI, and domain workflows

The relay moves Telegram ingress and egress to a small external node while leaving the backend architecture largely unchanged.

---

## What the project is

This project is:

- a minimal Telegram edge gateway
- a transport proxy for webhook updates and outbound Bot API calls
- a small deployable infrastructure component
- intentionally limited in scope

---

## What the project is not

This project is not:

- a Telegram bot framework
- a general-purpose API gateway
- an AI service
- a full backend platform
- a workflow engine
- a state management system
- a queue system
- a multi-tenant SaaS control plane

Do not expand it in those directions unless explicitly requested.

---

## Main use case

The main use case is a production Telegram bot whose private backend cannot reliably communicate with Telegram directly.

Desired architecture:

- Telegram talks only to the relay
- the relay talks to the private backend
- the private backend talks back to the relay for outbound Telegram actions
- the relay sends those actions to Telegram

The relay should be small enough to deploy quickly on a foreign VPS.

---

## Product values

The most important values are:

1. simplicity
2. reliability
3. tiny operational footprint
4. fast deployment
5. easy auditability
6. easy adapter integration into an existing bot

When in doubt, choose the smaller and more explicit design.

---

## Required behavior

### Public side

The relay must expose a public Telegram webhook endpoint.

### Internal side

The relay must expose a protected internal API for the private backend to request outbound Telegram actions.

### Security

All server-to-server traffic must be protected.

Minimum acceptable baseline:

- HTTPS
- shared secret or HMAC signing
- timestamp validation

### Observability

The relay must produce useful logs and basic health information.

---

## Technical preferences

Preferred stack:

- Python
- FastAPI
- Pydantic
- httpx
- uvicorn

Preferred qualities:

- small dependency count
- typed config
- typed request models
- stateless design
- very small API surface

Avoid large frameworks or unnecessary operational complexity.

---

## Security preferences

Preferred internal auth model:

- HMAC signature with timestamp header
- small replay window
- constant-time comparison
- optional nonce strategy later if needed

Do not default to unauthenticated internal endpoints.

Do not log secrets or full bot tokens.

---

## Outbound Telegram method scope for v1

Initial supported outbound methods should be intentionally limited:

- `sendMessage`
- `sendPhoto`
- `editMessageText`
- `editMessageCaption`
- `answerCallbackQuery`
- `deleteMessage`
- `sendChatAction`

Do not try to mirror the entire Telegram Bot API immediately.

---

## File transfer preference

Preferred v1 choice:

- backend uploads files directly to relay internal endpoints when needed

Alternative options like signed URLs may be added later, but should not complicate the initial design.

---

## Error philosophy

The relay should produce clear normalized errors.

It should clearly separate:

- relay transport failures
- backend forwarding failures
- Telegram API errors
- internal auth failures
- validation failures

Soft-ignore only a small explicit whitelist of harmless Telegram conditions.

---

## Operational philosophy

The relay is designed for incident resilience.

That means:

- fast to deploy
- fast to understand
- fast to recover
- minimal moving parts
- minimal persistent state

The relay should never become more complex than the backend it protects.

---

## Integration philosophy

The private backend should be able to integrate with the relay using a thin adapter.

The backend should ideally switch between two modes:

- direct Telegram transport
- relay Telegram transport

This keeps application logic unchanged.

The relay contract should therefore be explicit and stable.

---

## Output expectations for AI coding agents

When generating code for this project:

- keep modules small
- keep architecture flat
- keep request models explicit
- keep auth logic easy to inspect
- keep deployment simple
- prefer clarity over generic reuse
- do not introduce speculative abstractions

If proposing a feature, always justify why it belongs in the relay and not in the backend.

---

## Scope guard

Before adding anything substantial, ask:

- Is this required for Telegram transport reliability?
- Is this required for secure relay-backend communication?
- Is this required for minimal operability?
- Is this still small?

If the answer is no, it likely does not belong here.

---

## Ideal v1 outcome

A small repository that can be deployed quickly and provides:

- Telegram webhook receiver
- signed forwarding to backend
- protected internal outbound API
- Telegram Bot API client
- health endpoint
- simple config
- tests for the critical transport and security paths

That is enough for a useful first release.