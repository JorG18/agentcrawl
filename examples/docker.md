# Docker example

Use this when you want to run the self-hosted HTTP API without managing a Python environment.

## Run the published image

```bash
docker run --rm -p 8000:8000 \
  -e AGENTCRAWL_API_KEYS="example-development-key" \
  ghcr.io/jorg18/agentcrawl:latest
```

In another terminal:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/v1/scrape \
  -H "authorization: Bearer example-development-key" \
  -H "content-type: application/json" \
  -d '{"url":"https://pypi.org/project/agentcrawl-ai/","formats":["markdown","metadata"]}'
```

## Persistent data

```bash
docker volume create agentcrawl-data
docker run -d --name agentcrawl \
  -p 8000:8000 \
  -e AGENTCRAWL_API_KEYS="example-development-key" \
  -v agentcrawl-data:/data \
  ghcr.io/jorg18/agentcrawl:latest
```

The default image is lightweight and HTTP-first. Browser rendering is optional and not bundled into this image.
