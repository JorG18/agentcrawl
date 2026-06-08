from agentcrawl.graphs import ExtractionGraph

graph_config = {
    "llm": {
        "model": "openai/gpt-4.1-mini",
        "format": "json",
    },
    "verbose": True,
    "headless": True,
}

graph = ExtractionGraph(
    prompt="Extract useful information from the webpage.",
    source="https://example.com",
    config=graph_config,
)

print(graph.run())
