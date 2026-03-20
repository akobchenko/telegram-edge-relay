# Telegram Edge Relay

A tiny Telegram gateway that receives webhooks on a foreign edge and securely relays updates and outbound Bot API calls to your private backend.

## Overview

**Telegram Edge Relay** is a minimal transport layer for Telegram bots running in restricted, unstable, or regionally blocked network environments.

It is designed for setups where your main backend cannot reliably communicate with Telegram directly, but you still want to keep your core business logic, databases, queues, and AI workloads on your own infrastructure.

The relay runs on a public or foreign server, accepts Telegram webhooks, forwards updates to your backend over an encrypted channel, and can also send outbound Bot API requests on behalf of your backend.

This project is intentionally small, focused, and easy to deploy. It is **not** a bot framework and **not** a replacement for your application. Its only job is to provide a secure and reliable Telegram-facing edge.

---

## Why this project exists

In some environments, one or both of the following become unreliable:

- Telegram cannot reach your backend webhook endpoint
- your backend cannot reach the Telegram Bot API

When that happens, even a well-designed bot can stop functioning.

**Telegram Edge Relay** solves this by moving all Telegram-facing traffic to a lightweight external service while keeping your main application architecture unchanged.

Typical architecture:

```text
Telegram <-> Telegram Edge Relay <-> Private Backend
