# Examples

These files are example-only integration snippets for a private backend.

They are intentionally small and readable. They are not production-complete.

Before using them in production, add:

- secret management
- retries and circuit-breaking only where justified
- structured application logging
- request authentication middleware around your own backend endpoints
- test coverage for your exact deployment and failure modes

Files:

- [`backend_client.py`](/Users/antonkobcenko/Documents/projects/telegram-edge-relay/examples/backend_client.py): signing helper plus `sendMessage` and `sendPhoto` calls to the relay
- [`backend_receiver.py`](/Users/antonkobcenko/Documents/projects/telegram-edge-relay/examples/backend_receiver.py): small FastAPI endpoint that receives forwarded Telegram updates from the relay
