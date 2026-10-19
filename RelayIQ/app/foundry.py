"""Model clients: Foundry Local (on-device) for judge + local tier, Azure for cloud tier.

Also handles endpoint discovery and lazy model loading, and measures the bytes each call
would send/receive so the app can show real data savings.
"""
from __future__ import annotations
from datetime import date
import logging
import re
import subprocess
from functools import lru_cache

import requests
from openai import OpenAI

from . import config

_logger = logging.getLogger(__name__)
_CANDIDATE_PORTS = [63166]


def _validate(endpoint: str) -> bool:
    try:
        r = requests.get(f"{endpoint}/v1/models", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


@lru_cache(maxsize=1)
def discover_endpoint() -> str:
    """Find the running Foundry Local OpenAI-compatible endpoint."""
    if config.FOUNDRY_ENDPOINT and _validate(config.FOUNDRY_ENDPOINT):
        return config.FOUNDRY_ENDPOINT

    for cmd in (
        ["foundry", "server", "status", "--output", "json"],
        ["foundry", "service", "status"],
        ["foundry", "model", "list"],
    ):
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", timeout=60,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            _logger.warning(
                "Foundry endpoint discovery command failed",
                extra={"failure_type": type(exc).__name__},
            )
            continue
        if result.returncode != 0:
            _logger.warning(
                "Foundry endpoint discovery command was unsuccessful",
                extra={"returncode": result.returncode},
            )
            continue
        for m in re.finditer(r"http://127\.0\.0\.1:\d+", result.stdout):
            ep = m.group(0)
            if _validate(ep):
                return ep

    for port in _CANDIDATE_PORTS:
        ep = f"http://127.0.0.1:{port}"
        if _validate(ep):
            return ep

    raise RuntimeError(
        "Could not find a running Foundry Local endpoint. Run `foundry server start` "
        "or set FOUNDRY_ENDPOINT to the endpoint shown by `foundry server status`."
    )


@lru_cache(maxsize=1)
def _local_client() -> OpenAI:
    return OpenAI(base_url=f"{discover_endpoint()}/v1", api_key="not-needed")


def _loaded_models() -> list[str]:
    r = requests.get(f"{discover_endpoint()}/v1/models", timeout=10)
    return [m["id"] for m in r.json().get("data", [])]


@lru_cache(maxsize=8)
def ensure_model(alias: str) -> str:
    """Ensure a model alias is loaded; return its concrete model id."""
    norm = alias.lower()

    def _match() -> str | None:
        for mid in _loaded_models():
            if mid.lower().startswith(norm):
                return mid
        return None

    hit = _match()
    if hit:
        return hit

    subprocess.run(
        ["foundry", "model", "load", alias],
        capture_output=True, text=True, encoding="utf-8", timeout=600,
    )
    hit = _match()
    if hit:
        return hit
    raise RuntimeError(f"Model '{alias}' could not be loaded on Foundry Local.")


def _bytes(text: str) -> int:
    return len(text.encode("utf-8"))


# --- Cloud client (real Azure, or simulated with a local model) ---
@lru_cache(maxsize=1)
def _azure_client():
    kwargs = {"base_url": config.azure_openai_base_url()}
    if config._use_aad():
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        kwargs["api_key"] = get_bearer_token_provider(
            DefaultAzureCredential(), "https://ai.azure.com/.default"
        )
    else:
        kwargs["api_key"] = config.AZURE_OPENAI_API_KEY
    return OpenAI(**kwargs)


def chat_local(
    alias: str,
    messages: list[dict],
    max_tokens: int = 400,
    temperature: float = 0.2,
    *,
    timeout_seconds: float | None = None,
):
    """Call an on-device Foundry Local model. Returns (text, wire_bytes)."""
    model_id = ensure_model(alias)
    client = _local_client()
    if timeout_seconds is not None:
        client = client.with_options(timeout=timeout_seconds, max_retries=0)
    r = client.chat.completions.create(
        model=model_id, messages=messages, max_tokens=max_tokens, temperature=temperature
    )
    text = r.choices[0].message.content or ""
    wire = _bytes("".join(m["content"] for m in messages)) + _bytes(text)
    return text, wire


def chat_cloud(
    messages: list[dict],
    max_tokens: int = 600,
    temperature: float = 0.3,
    use_web_search: bool = False,
):
    """Call the cloud tier. Real Azure if configured, else simulate with a bigger local model.

    Returns (text, wire_bytes, is_real).
    """
    if config.cloud_is_real():
        request = {
            "model": config.AZURE_OPENAI_DEPLOYMENT,
            "input": messages,
            "max_output_tokens": max_tokens,
        }
        if use_web_search:
            request["tools"] = [{"type": "web_search"}]
            request["tool_choice"] = "required"
            request["instructions"] = (
                f"The current date is {date.today():%B %d, %Y}. Search the web before answering. "
                "Prefer the latest authoritative source, verify whether the event has already "
                "occurred as of today, and cite the source in the answer."
            )
        r = _azure_client().responses.create(
            **request,
        )
        text = r.output_text or ""
        wire = _bytes("".join(m["content"] for m in messages)) + _bytes(text)
        return text, wire, True

    # Simulated cloud: use a local model but still count the bytes as "cloud data".
    text, wire = chat_local(config.CLOUD_SIM_MODEL, messages, max_tokens=max_tokens, temperature=temperature)
    return text, wire, False
