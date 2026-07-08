# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog and this project follows Semantic Versioning.

## [Unreleased]

### Added
- Webhook secret token validation via `TELEGRAM_WEBHOOK_SECRET`.
- Global PTB error handler for uncaught update exceptions.
- Safe callback parsing helpers.
- Full flow for `/agregaringresorecurrente`.
- Base project documentation (README, runbook, security, command/state reference, ADR).

### Changed
- Stricter positive amount validation in monetary flows.
- PTB app initialization protected with `asyncio.Lock`.

