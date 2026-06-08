# Security Policy

## Reporting

Do not open public issues for suspected vulnerabilities. Until a dedicated security address is published, contact the repository owner privately through the hosting platform.

Include affected versions, reproduction steps, impact, and any suggested mitigation. Do not include scraped private data or active credentials.

## Deployment

- Keep bearer authentication enabled.
- Use long random API keys and rotate exposed keys.
- Put the service behind TLS and network-level request limits.
- Configure `AGENTCRAWL_RATE_LIMIT_PER_MINUTE` for expected traffic.
- Keep `AGENTCRAWL_ALLOW_LOCAL_FILES=false` on network services.
- Keep `AGENTCRAWL_ALLOW_PRIVATE_NETWORK=false` unless the service is isolated and internal access is intentional.
- Treat browser execution as untrusted workload and constrain CPU, memory, and concurrency.
- Update the base image and Python dependencies regularly.

The built-in URL checks reduce SSRF risk but are not a replacement for egress firewall rules.
