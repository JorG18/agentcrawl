"""Framework adapters: LangChain document loader and LlamaIndex reader.

Each adapter imports its framework lazily, so ``agentcrawl`` never depends on
them; install the framework you use (``langchain-core`` or
``llama-index-core``) next to ``agentcrawl-ai``.
"""
