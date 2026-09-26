"""Prompt-driven extraction with ExtractionGraph.

Needs an LLM: install ``agentcrawl-ai[llm]`` plus the LangChain package for
your provider, set its API key, then choose the model with
``AGENTCRAWL_LLM_MODEL`` as ``provider/model``, for example:

    AGENTCRAWL_LLM_MODEL=openai/gpt-4.1-mini python examples/graph_extraction.py
"""

import os
import sys

from agentcrawl.graphs import ExtractionGraph

model = os.environ.get("AGENTCRAWL_LLM_MODEL")
if not model:
    sys.exit("Set AGENTCRAWL_LLM_MODEL to provider/model, e.g. openai/gpt-4.1-mini.")

graph = ExtractionGraph(
    prompt="What is this package called, what does it do, and what is its latest version?",
    source="https://pypi.org/project/agentcrawl-ai/",
    config={"llm": {"model": model}, "fetcher": "http"},
)

print(graph.run())
