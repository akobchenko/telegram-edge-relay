# Examples

These files are example-only integration snippets for a private backend.

They are intentionally small and readable. They are not production-complete.

Use them to adapt an existing backend so it talks to `telegram-edge-relay` instead of talking to Telegram directly.

Before using them in production, add:

- secret management
- retries and circuit-breaking only where justified
- structured application logging
- request authentication middleware around your own backend endpoints
- test coverage for your exact deployment and failure modes

Files:

- `backend_receiver.py`: one FastAPI endpoint that verifies forwarded Telegram updates from the relay and hands the parsed update into your backend
- `backend_client.py`: one tiny outbound adapter with explicit HMAC signing plus `sendMessage` and `sendPhoto` helpers

Flow:

1. Telegram sends a webhook to the relay.
2. The relay forwards the raw update JSON to your private backend.
3. `backend_receiver.py` verifies the relay signature before your backend trusts the update.
4. Your backend responds to users by calling the relay internal API with the helpers from `backend_client.py`.
