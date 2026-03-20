# AGENTS.md

## Purpose

This file provides instructions for human contributors and AI coding agents working on `telegram-edge-relay`.

The goal is to keep the project small, focused, and reliable.

---

## Mission

Build a tiny Telegram edge relay that:

- receives Telegram webhooks
- forwards updates to a private backend securely
- accepts protected outbound requests from the backend
- sends Bot API calls to Telegram
- stays easy to deploy, audit, and operate

---

## Non-mission

Do not turn this project into:

- a bot framework
- a second backend
- a queue platform
- a state machine engine
- an admin dashboard platform
- a plugin ecosystem
- a monolithic “Telegram platform”

The relay is a transport component only.

---

## Working principles

### 1. Prefer the smallest correct solution

If a direct solution works, do not introduce an abstraction.

### 2. Keep the code path obvious

An on-call engineer must be able to trace an inbound or outbound request quickly.

### 3. Keep the API surface narrow

Only expose what is needed for v1.

### 4. Make security visible

Security-critical code must be explicit, readable, and testable.

### 5. Avoid speculative architecture

Do not add generic layers “for future flexibility” unless there is a proven need.

---

## Expected repository shape

A good repository shape is compact and explicit.

Example:

- `app/main.py`
- `app/config.py`
- `app/api/public.py`
- `app/api/internal.py`
- `app/models/public.py`
- `app/models/internal.py`
- `app/services/telegram_client.py`
- `app/services/backend_forwarder.py`
- `app/security/signing.py`
- `app/core/request_id.py`
- `tests/...`

Equivalent small variations are acceptable.

---

## Required v1 capabilities

AI agents should prioritize these in order:

1. typed config
2. health endpoint
3. public Telegram webhook endpoint
4. internal auth/signature verification
5. backend forwarding service
6. outbound Telegram internal endpoints
7. Telegram client wrapper
8. high-value tests
9. deployment docs
10. integration examples

---

## Required quality bar

Generated code must be:

- typed
- explicit
- easy to review
- operationally boring
- small
- easy to run locally

Code that is clever but harder to trust is not acceptable.

---

## Security requirements

The relay must not expose unauthenticated internal control endpoints.

Minimum internal protection:

- shared secret and/or HMAC signature
- timestamp verification
- constant-time comparison
- explicit error responses for auth failures

Never log:

- bot token
- relay secret
- backend secret
- raw auth headers

---

## HTTP behavior requirements

All external HTTP clients must have:

- explicit timeout configuration
- normalized error handling
- predictable return format
- limited, justified retry behavior

Do not use unbounded retries.

---

## Validation requirements

All inbound and internal request bodies must be validated with typed models where practical.

Malformed input must fail clearly and early.

---

## Logging requirements

Every request path should emit enough metadata for debugging transport issues.

At minimum include:

- request ID
- direction (`telegram_inbound`, `backend_forward`, `telegram_outbound`)
- method / route
- elapsed time
- status
- failure category when applicable

---

## Testing expectations

AI agents should create a focused test suite that covers the most important behavior:

- signature verification
- timestamp expiry
- webhook forwarding
- outbound Telegram call normalization
- auth rejection
- validation failure behavior

Avoid spending early effort on low-value test volume.

---

## Documentation expectations

AI agents should keep markdown documentation aligned with implementation.

Required docs for v1:

- README
- environment variables
- internal auth/signing model
- deployment notes
- minimal backend integration example

---

## Forbidden directions without explicit approval

Do not introduce these without a strong, documented reason:

- databases
- ORM
- Celery / large worker systems
- plugin registries
- multi-tenant control layers
- complex retry orchestration
- event sourcing
- message bus as a hard dependency
- admin frontend
- websocket control plane

The relay must remain small.

---

## Decision filter

Before making a non-trivial change, ask:

1. Does this directly improve Telegram transport reliability?
2. Does this directly improve secure backend communication?
3. Does this preserve small size and deployment speed?
4. Would an operator understand this quickly?

If not, reject or simplify the change.

---

## Style expectations for AI-generated patches

Preferred style:

- concrete function names
- compact modules
- explicit Pydantic models
- direct control flow
- minimal indirection

Avoid:

- deep class hierarchies
- generic service locators
- abstract factory patterns
- large utility files
- unnecessary decorators
- implicit magic

---

## Final instruction

Treat this project like emergency-grade infrastructure:
small, secure, comprehensible, and recoverable.