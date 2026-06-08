# AgentCrawl Roadmap

AgentCrawl Community focuses on local and single-node web extraction for AI agents. The roadmap below is directional and may change as the project matures.

## Current Focus

- Reliable crawl jobs with durable checkpoints, retries, cancellation, and pagination.
- HTTP-first extraction with optional browser fallback.
- Clear Python, CLI, HTTP API, and MCP interfaces.
- Safe self-hosted operation with authentication, SSRF protection, backups, and restore checks.
- Installation and diagnostic flows that work for both humans and coding agents.

## Next Areas

### Document Support

- PDF and common office document ingestion.
- Better plain text, XML, JSON, and Markdown handling.
- Optional OCR for image-only documents.

### Extraction Quality

- Versioned fixture corpus for repeatable quality checks.
- Better main-content extraction for documentation, news, commerce, forums, tables, and code.
- More structured metadata and provenance.

### Browser Workflows

- Bounded wait, click, scroll, type, and capture actions.
- Session and cookie handling for authorized workflows.
- Strong cleanup for browser processes, tabs, timeouts, and failed jobs.

### Operations

- Key lifecycle improvements: names, expiry, rotation, and revocation.
- More detailed usage and operational metrics.
- Upgrade and rollback guidance for persistent deployments.
- Container and packaging release automation.

## Release Standard

A release is ready when a new user can install, run, diagnose, back up, restore, and connect AgentCrawl to an agent client without private instructions from the maintainers.

Public performance or quality claims should be backed by reproducible tests. Benchmarks are optional unless the project makes those claims.
