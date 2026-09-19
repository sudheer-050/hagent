"""Text embeddings for the memory service's optional semantic lookup.

`config` is any object with embedding_provider, embedding_model, embedding_base_url and embedding_api_key.
"""

import os


def embed_texts(config, texts: list[str], *, query: bool = False) -> list[list[float]]:
    if not texts:
        return []
    import httpx  # deferred: keeps CLI startup fast

    provider, model = config.embedding_provider, config.embedding_model
    base = (config.embedding_base_url or "").rstrip("/")
    key = config.embedding_api_key or (
        os.getenv("GEMINI_API_KEY", "") if provider == "gemini" else os.getenv("OPENAI_API_KEY", "")
    )
    if provider == "ollama":
        endpoint = f"{base or 'http://localhost:11434'}/api/embed"
        response = httpx.post(endpoint, json={"model": model, "input": texts}, timeout=120)
        response.raise_for_status()
        return response.json()["embeddings"]
    if provider == "gemini":
        output = []
        for text in texts:
            endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"
            body = {
                "content": {"parts": [{"text": text}]},
                "embedContentConfig": {"taskType": "RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT"},
            }
            response = httpx.post(endpoint, headers={"x-goog-api-key": key}, json=body, timeout=60)
            response.raise_for_status()
            output.append(response.json()["embedding"]["values"])
        return output
    if provider in {"openai", "openai_compatible"}:
        if provider == "openai_compatible" and not base:
            raise ValueError("Set the embedding service Base URL in memory settings")
        endpoint = f"{base or 'https://api.openai.com/v1'}/embeddings"
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        response = httpx.post(endpoint, headers=headers, json={"model": model, "input": texts}, timeout=90)
        response.raise_for_status()
        data = sorted(response.json()["data"], key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in data]
    raise ValueError(f"Unsupported embedding provider: {provider}")
