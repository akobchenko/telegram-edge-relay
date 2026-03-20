# CODING_RULES.md

## General philosophy

This project must stay small, readable, and explicit.

The relay is infrastructure code, not product code. Reliability, clarity, and predictability are more important than clever abstractions.

Code should feel boring in a good way.

---

## Primary goals

1. correctness
2. operational simplicity
3. explicit behavior
4. ease of audit
5. minimal dependency footprint
6. maintainability under incident pressure

---

## Language and stack

Preferred stack:

- Python 3.11+
- FastAPI
- Pydantic
- httpx
- uvicorn

Avoid unnecessary libraries.

Before adding a dependency, ask:

- does the standard library already solve this?
- is the dependency small and well maintained?
- does it reduce code or increase hidden complexity?

---

## Project size discipline

The codebase should remain intentionally compact.

Rules:

- avoid framework-like abstractions
- avoid premature plugin systems
- avoid generic transport hierarchies unless there is a real second implementation
- avoid utility modules that become dumping grounds
- avoid deep nesting and over-factored micro-functions

Small service, small code.

---

## File organization

Prefer a simple structure.

Example:

- `app/main.py`
- `app/config.py`
- `app/api/public.py`
- `app/api/internal.py`
- `app/services/telegram_client.py`
- `app/services/forwarder.py`
- `app/security/signing.py`
- `app/models/...`
- `app/logging.py`

Do not create many layers unless they clearly improve clarity.

---

## Naming rules

Names must be concrete and boring.

Good:

- `forward_update_to_backend`
- `send_message_to_telegram`
- `verify_internal_signature`
- `TelegramSendMessageRequest`

Bad:

- `process_payload`
- `handle_data`
- `dispatch_thing`
- `BaseManagerFactory`

Prefer explicit domain names over generic abstractions.

---

## API design rules

All endpoints must have:

- clear request models
- clear response models when practical
- explicit status codes
- explicit error shape

Do not return ad hoc JSON blobs unless unavoidable.

---

## Validation rules

Validate all external input.

Required validation targets:

- Telegram webhook payload shape
- internal API request shape
- timestamps
- signature headers
- supported outbound methods
- file metadata
- callback payload sizes where relevant

Reject invalid input early and clearly.

---

## Error handling rules

Never hide transport errors.

Rules:

- distinguish network errors from application errors
- distinguish internal auth errors from payload errors
- distinguish Telegram API error responses from local relay failures
- log enough context to debug, but never log secrets

Use explicit exceptions where helpful, but do not build large exception hierarchies.

---

## Logging rules

Logging must be structured and useful during incidents.

Always log:

- request ID
- route
- method
- target system
- elapsed time
- outcome
- important status codes

Never log:

- bot token
- shared secrets
- raw Authorization headers
- signed header secrets
- sensitive user content unless debug mode explicitly allows it

Redact aggressively.

---

## Security rules

Security is not optional.

Required rules:

- use constant-time comparison for secrets/signatures
- validate timestamp windows
- reject stale signed requests
- make secret names explicit
- never hardcode secrets
- avoid insecure debug defaults
- fail closed, not open

All internal authentication logic must be easy to read and easy to test.

---

## HTTP client rules

The Telegram client and backend forward client must be deterministic.

Rules:

- define explicit timeouts
- define retry policy explicitly
- do not use unbounded retries
- set sensible connect/read/write limits
- normalize error surfaces

Retries must be limited and only used where safe.

---

## Async rules

Use async where it directly improves I/O behavior.

Do not introduce concurrency complexity without need.

Avoid:

- background task sprawl
- unbounded fan-out
- subtle async side effects
- hidden retry loops

The relay should remain operationally obvious.

---

## State rules

The relay should be stateless by default.

Do not introduce persistent state unless there is a very strong reason.

If any state is added later, it must be:

- justified in `ARCHITECTURE_DECISIONS.md`
- isolated
- optional where possible

---

## Testing rules

Tests must focus on behavior that matters operationally.

Priority order:

1. signature verification
2. timestamp validation
3. webhook forwarding
4. Telegram outbound request normalization
5. file upload flow
6. error mapping
7. health endpoints

Prefer a compact but high-value test suite over a huge shallow suite.

---

## Code style rules

Use:

- type hints everywhere practical
- short functions where it improves readability
- docstrings only where behavior is non-obvious
- explicit imports
- small request/response models
- clear constants for header names and time windows

Avoid:

- giant classes
- magic helper registries
- metaprogramming
- hidden side effects in decorators
- generic base classes with one implementation

---

## Pydantic rules

Use Pydantic models for all external request and response contracts.

Rules:

- keep models small
- prefer composition over inheritance
- avoid overly generic payload models
- use field descriptions where useful
- keep Telegram-specific models close to the Telegram transport layer

---

## Dependency injection rules

Keep dependency injection simple.

Use FastAPI dependency functions where needed for:

- config access
- auth/signature verification
- request ID context

Do not build an elaborate container framework.

---

## Versioning rules

The public and internal APIs must be stable and boring.

If breaking changes are required:

- document them
- gate them with clear versioning
- prefer additive changes first

Do not break the adapter contract casually.

---

## Documentation rules

Every non-trivial behavior must be documented near the code or in markdown.

Required docs:

- environment variables
- signing model
- request/response examples
- deployment assumptions
- integration expectations for backend adapters

---

## Review rules

Every proposed change should be reviewed with these questions:

1. Does this improve reliability?
2. Does this preserve simplicity?
3. Does this belong in the relay?
4. Can an operator understand this quickly during an incident?
5. Does this add avoidable coupling?

If the answer is unclear, simplify.

---

## Final standard

Write code that an on-call engineer can trust at 3 AM with minimal context.
