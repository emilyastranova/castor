"""LLM providers (Phase 3e): one small HTTP adapter per API family, and model chains with
fallbacks.

Families: OpenAI chat completions (OpenAI, Azure OpenAI and every OpenAI-compatible server:
OpenRouter, Groq, Mistral, DeepSeek, xAI, Together, LM Studio, vLLM…), Anthropic messages,
Gemini generateContent (API key or Vertex AI with a service account) and Ollama. No SDKs:
each is a JSON request over httpx, so adding one is a few lines and the server stays light.

`complete` walks the chain for a purpose ("default", "resolver") and returns the first
answer that works; every attempt is an `ai_calls` row. Keys live in the vault, per field.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from . import vault

TIMEOUT = httpx.Timeout(90.0, connect=10.0)


class AiError(Exception):
    pass


class NotConfigured(AiError):
    pass


@dataclass
class Field:
    name: str
    label: str
    type: str = "text"  # text | url | password | textarea
    secret: bool = False
    required: bool = True
    placeholder: str = ""
    help: str = ""
    default: str = ""


@dataclass
class Kind:
    kind: str
    label: str
    family: str  # openai | azure | anthropic | gemini | vertex | ollama
    fields: list[Field]
    models: list[str] = field(default_factory=list)  # suggestions; "List models" asks the server
    help: str = ""


def _key(required: bool = True, help: str = "") -> Field:
    return Field("api_key", "API key", "password", secret=True, required=required, help=help)


def _base(default: str, required: bool = False) -> Field:
    return Field("base_url", "Base URL", "url", required=required, default=default, placeholder=default)


def _compatible(kind: str, label: str, base: str, models: list[str], key_required: bool = True, help: str = "") -> Kind:
    return Kind(kind, label, "openai", [_base(base, True), _key(key_required)], models, help)


KINDS: dict[str, Kind] = {k.kind: k for k in [
    Kind("openai", "OpenAI", "openai", [_key(), _base("https://api.openai.com/v1")],
         ["gpt-4.1", "gpt-4.1-mini", "gpt-4o-mini", "o4-mini"]),
    Kind("anthropic", "Anthropic", "anthropic", [_key(), _base("https://api.anthropic.com")],
         ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"]),
    Kind("gemini", "Google Gemini (API key)", "gemini", [_key(help="From Google AI Studio.")],
         ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"]),
    Kind("vertex", "Google Gemini on Vertex AI", "vertex", [
        Field("project", "Project id", placeholder="my-gcp-project"),
        Field("location", "Location", default="us-central1", placeholder="us-central1 or global"),
        Field("service_account", "Service account key (JSON)", "textarea", secret=True,
              help="A key for a service account with the Vertex AI User role."),
    ], ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"]),
    Kind("azure_openai", "Azure OpenAI", "azure", [
        Field("endpoint", "Endpoint", "url", placeholder="https://my-resource.openai.azure.com"),
        _key(),
        Field("api_version", "API version", default="2024-10-21"),
    ], [], "The model is your deployment's name."),
    Kind("ollama", "Ollama (self-hosted)", "ollama", [_base("http://localhost:11434", True),
                                                       _key(False, "Only for a proxy or Ollama's cloud.")],
         ["llama3.1", "qwen2.5", "mistral", "gemma3"]),
    _compatible("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", ["openai/gpt-4.1-mini", "anthropic/claude-sonnet-5-5"]),
    _compatible("groq", "Groq", "https://api.groq.com/openai/v1", ["llama-3.3-70b-versatile"]),
    _compatible("mistral", "Mistral", "https://api.mistral.ai/v1", ["mistral-large-latest", "mistral-small-latest"]),
    _compatible("deepseek", "DeepSeek", "https://api.deepseek.com/v1", ["deepseek-chat", "deepseek-reasoner"]),
    _compatible("xai", "xAI", "https://api.x.ai/v1", ["grok-3", "grok-3-mini"]),
    _compatible("together", "Together AI", "https://api.together.xyz/v1", []),
    _compatible("lmstudio", "LM Studio (self-hosted)", "http://localhost:1234/v1", [], False),
    _compatible("vllm", "vLLM (self-hosted)", "http://localhost:8000/v1", [], False),
    _compatible("openai_compatible", "Other OpenAI-compatible", "", [], False,
                "Anything that speaks the OpenAI chat completions API (LiteLLM, LocalAI, a gateway…)."),
]}


def kinds_out() -> list[dict[str, Any]]:
    return [{"kind": k.kind, "label": k.label, "help": k.help, "models": k.models, "fields": [f.__dict__ for f in k.fields]}
            for k in KINDS.values()]


def split_fields(kind: str, given: dict[str, Any], old: m.AiProvider | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """Validate a provider's fields: (plain, vault secrets); a blank secret keeps the old one."""
    spec = KINDS.get(kind)
    if spec is None:
        raise AiError(f"Unknown provider kind {kind}")
    plain: dict[str, str] = {}
    secrets: dict[str, str] = {}
    for f in spec.fields:
        value = str(given.get(f.name) or "").strip()
        if f.secret:
            if value:
                if f.name == "service_account":
                    _service_account(value)  # refuse a key that won't work, now
                secrets[f.name] = vault.encrypt(value.encode())
            elif old is not None and f.name in (old.secrets or {}):
                secrets[f.name] = old.secrets[f.name]
            elif f.required:
                raise AiError(f"{f.label}: required")
            continue
        value = value or f.default
        if f.required and not value:
            raise AiError(f"{f.label}: required")
        if f.type == "url" and value and not re.match(r"^https?://", value):
            raise AiError(f"{f.label}: an http(s) URL")
        plain[f.name] = value.rstrip("/") if f.type == "url" else value
    return plain, secrets


def _values(p: m.AiProvider) -> dict[str, str]:
    out = dict(p.fields or {})
    for name, token in (p.secrets or {}).items():
        out[name] = vault.decrypt(token).decode()
    return out


def _service_account(text: str) -> dict[str, Any]:
    try:
        info = json.loads(text)
    except ValueError as exc:
        raise AiError("Service account key: not JSON") from exc
    if not all(info.get(k) for k in ("client_email", "private_key")):
        raise AiError("Service account key: needs client_email and private_key")
    return info


# ---------- one call ----------


@dataclass
class Answer:
    text: str
    model: str
    provider: str
    input_tokens: int | None = None
    output_tokens: int | None = None


_vertex_tokens: dict[str, tuple[str, float]] = {}


async def _vertex_token(client: httpx.AsyncClient, info: dict[str, Any]) -> str:
    cached = _vertex_tokens.get(info["client_email"])
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    now = int(time.time())
    token_uri = info.get("token_uri") or "https://oauth2.googleapis.com/token"
    assertion = jwt.encode({"iss": info["client_email"], "scope": "https://www.googleapis.com/auth/cloud-platform",
                            "aud": token_uri, "iat": now, "exp": now + 3600}, info["private_key"], algorithm="RS256")
    r = await client.post(token_uri, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
    _raise(r, "Google sign-in")
    data = r.json()
    _vertex_tokens[info["client_email"]] = (data["access_token"], now + int(data.get("expires_in", 3600)))
    return data["access_token"]


def _raise(r: httpx.Response, what: str) -> None:
    if r.status_code >= 400:
        try:
            detail = r.json()
            detail = detail.get("error", detail)
            if isinstance(detail, dict):
                detail = detail.get("message") or json.dumps(detail)[:300]
        except ValueError:
            detail = r.text[:300]
        raise AiError(f"{what}: HTTP {r.status_code}: {str(detail)[:300]}")


def _gemini_body(system: str, messages: list[dict[str, str]], want_json: bool, max_tokens: int, temperature: float) -> dict[str, Any]:
    config: dict[str, Any] = {"temperature": temperature, "maxOutputTokens": max_tokens}
    if want_json:
        config["responseMimeType"] = "application/json"
    return {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "model" if mm["role"] == "assistant" else "user", "parts": [{"text": mm["content"]}]} for mm in messages],
        "generationConfig": config,
    }


def _gemini_answer(data: dict[str, Any], model: str, provider: str) -> Answer:
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason")
        raise AiError(f"no answer{f' (blocked: {reason})' if reason else ''}")
    text = "".join(p.get("text", "") for p in (candidates[0].get("content") or {}).get("parts", []))
    usage = data.get("usageMetadata") or {}
    return Answer(text, model, provider, usage.get("promptTokenCount"), usage.get("candidatesTokenCount"))


async def call(provider: m.AiProvider, model: str, system: str, messages: list[dict[str, str]], *, want_json: bool = False,
               max_tokens: int = 4000, temperature: float = 0.2, client: httpx.AsyncClient | None = None) -> Answer:
    """One request to one model. Raises AiError with the provider's own message."""
    spec = KINDS.get(provider.kind)
    if spec is None:
        raise AiError(f"Unknown provider kind {provider.kind}")
    v = _values(provider)
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT)
    try:
        if spec.family in ("openai", "azure"):
            body: dict[str, Any] = {"model": model, "messages": [{"role": "system", "content": system}, *messages], "temperature": temperature}
            headers = {"Content-Type": "application/json"}
            if spec.family == "azure":
                url = f"{v['endpoint'].rstrip('/')}/openai/deployments/{model}/chat/completions?api-version={v.get('api_version') or '2024-10-21'}"
                headers["api-key"] = v["api_key"]
                body["max_completion_tokens"] = max_tokens
            else:
                url = f"{(v.get('base_url') or 'https://api.openai.com/v1').rstrip('/')}/chat/completions"
                if v.get("api_key"):
                    headers["Authorization"] = f"Bearer {v['api_key']}"
                # OpenAI's newer models take max_completion_tokens; compatible servers know max_tokens.
                body["max_completion_tokens" if provider.kind == "openai" else "max_tokens"] = max_tokens
            if want_json and provider.kind in ("openai", "azure_openai"):
                body["response_format"] = {"type": "json_object"}
            if provider.kind == "openai" and re.match(r"^o\d", model):
                body.pop("temperature")  # reasoning models take the default only
            r = await client.post(url, json=body, headers=headers)
            _raise(r, provider.name)
            data = r.json()
            choice = (data.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""
            usage = data.get("usage") or {}
            return Answer(text, model, provider.name, usage.get("prompt_tokens"), usage.get("completion_tokens"))
        if spec.family == "anthropic":
            # Current Claude models reject sampling parameters (temperature/top_p/top_k), and
            # their thinking counts against max_tokens, so give them room even for short answers.
            r = await client.post(
                f"{(v.get('base_url') or 'https://api.anthropic.com').rstrip('/')}/v1/messages",
                json={"model": model, "max_tokens": max(max_tokens, 16000), "system": system, "messages": messages},
                headers={"x-api-key": v["api_key"], "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
            )
            _raise(r, provider.name)
            data = r.json()
            if data.get("stop_reason") == "refusal":
                category = (data.get("stop_details") or {}).get("category")
                raise AiError(f"{provider.name}: the model declined{f' ({category})' if category else ''}")
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            if not text.strip():
                raise AiError(f"{provider.name}: empty answer (stop reason {data.get('stop_reason')})")
            usage = data.get("usage") or {}
            return Answer(text, model, provider.name, usage.get("input_tokens"), usage.get("output_tokens"))
        if spec.family == "gemini":
            r = await client.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                  json=_gemini_body(system, messages, want_json, max_tokens, temperature),
                                  headers={"x-goog-api-key": v["api_key"]})
            _raise(r, provider.name)
            return _gemini_answer(r.json(), model, provider.name)
        if spec.family == "vertex":
            info = _service_account(v["service_account"])
            token = await _vertex_token(client, info)
            loc = v.get("location") or "us-central1"
            host = "aiplatform.googleapis.com" if loc == "global" else f"{loc}-aiplatform.googleapis.com"
            url = f"https://{host}/v1/projects/{v['project']}/locations/{loc}/publishers/google/models/{model}:generateContent"
            r = await client.post(url, json=_gemini_body(system, messages, want_json, max_tokens, temperature),
                                  headers={"Authorization": f"Bearer {token}"})
            _raise(r, provider.name)
            return _gemini_answer(r.json(), model, provider.name)
        if spec.family == "ollama":
            headers = {"Authorization": f"Bearer {v['api_key']}"} if v.get("api_key") else {}
            body = {"model": model, "messages": [{"role": "system", "content": system}, *messages], "stream": False,
                    "options": {"temperature": temperature, "num_predict": max_tokens}}
            if want_json:
                body["format"] = "json"
            r = await client.post(f"{v['base_url'].rstrip('/')}/api/chat", json=body, headers=headers)
            _raise(r, provider.name)
            data = r.json()
            return Answer((data.get("message") or {}).get("content", ""), model, provider.name, data.get("prompt_eval_count"), data.get("eval_count"))
        raise AiError(f"no adapter for {spec.family}")
    except httpx.TimeoutException as exc:
        raise AiError(f"{provider.name}: timed out") from exc
    except httpx.HTTPError as exc:
        raise AiError(f"{provider.name}: {exc.__class__.__name__}: {exc}") from exc
    except (KeyError, vault.VaultError) as exc:
        raise AiError(f"{provider.name}: missing setting {exc}") from exc
    finally:
        if own:
            await client.aclose()


async def list_models(provider: m.AiProvider) -> list[str]:
    """What the server offers, where it can say (falls back to the kind's suggestions)."""
    spec = KINDS[provider.kind]
    v = _values(provider)
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        try:
            if spec.family == "openai":
                headers = {"Authorization": f"Bearer {v['api_key']}"} if v.get("api_key") else {}
                r = await client.get(f"{v.get('base_url') or 'https://api.openai.com/v1'}/models", headers=headers)
                _raise(r, provider.name)
                return sorted(x["id"] for x in r.json().get("data", []))
            if spec.family == "anthropic":
                r = await client.get(f"{v.get('base_url') or 'https://api.anthropic.com'}/v1/models?limit=100",
                                     headers={"x-api-key": v["api_key"], "anthropic-version": "2023-06-01"})
                _raise(r, provider.name)
                return [x["id"] for x in r.json().get("data", [])]
            if spec.family == "gemini":
                r = await client.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200", headers={"x-goog-api-key": v["api_key"]})
                _raise(r, provider.name)
                return sorted(x["name"].removeprefix("models/") for x in r.json().get("models", [])
                              if "generateContent" in x.get("supportedGenerationMethods", []))
            if spec.family == "ollama":
                r = await client.get(f"{v['base_url']}/api/tags")
                _raise(r, provider.name)
                return sorted(x["name"] for x in r.json().get("models", []))
        except httpx.HTTPError as exc:
            raise AiError(f"{provider.name}: {exc}") from exc
    return list(spec.models)


# ---------- chains ----------


def parse_json(text: str) -> dict[str, Any]:
    """The JSON object in an answer (fences and chatter around it are ignored)."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise AiError("the answer had no JSON object")
    try:
        value = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise AiError(f"the answer's JSON doesn't parse ({exc})") from exc
    if not isinstance(value, dict):
        raise AiError("the answer's JSON isn't an object")
    return value


async def chain_for(db: AsyncSession, purpose: str) -> list[tuple[m.AiProvider, str]]:
    from .app_settings import load

    chains = (await load(db)).ai_chains
    choices = chains.get(purpose) or chains.get("default") or []
    providers = {p.id: p for p in (await db.execute(select(m.AiProvider).where(m.AiProvider.enabled.is_(True)))).scalars()}
    out = []
    for c in choices:
        try:
            p = providers.get(uuid.UUID(c.provider))
        except ValueError:
            p = None
        if p is not None:
            out.append((p, c.model))
    return out


async def enabled(db: AsyncSession, purpose: str = "default") -> bool:
    return bool(await chain_for(db, purpose))


async def complete(db: AsyncSession, purpose: str, system: str, messages: list[dict[str, str]], *, want_json: bool = False,
                   user: m.User | None = None, max_tokens: int = 4000, check=None) -> tuple[Answer, Any]:
    """Ask the chain for `purpose`, model by model, until one gives a usable answer.
    `check(answer)` may raise AiError to reject an answer (bad JSON…) and move to the next
    model; its return value comes back with the answer. Commits the call log."""
    chain = await chain_for(db, purpose)
    if not chain:
        raise NotConfigured("No AI model is set up (Settings → AI)")
    errors = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for provider, model in chain:
            started = time.monotonic()
            row = m.AiCall(purpose=purpose, provider_id=provider.id, provider_name=provider.name, model=model, user_id=user.id if user else None)
            try:
                answer = await call(provider, model, system, messages, want_json=want_json, max_tokens=max_tokens, client=client)
                row.input_tokens, row.output_tokens = answer.input_tokens, answer.output_tokens
                result = check(answer) if check else None
                row.ok = True
                return answer, result
            except AiError as exc:
                row.error = str(exc)[:1000]
                errors.append(f"{provider.name} / {model}: {exc}")
            finally:
                row.ms = int((time.monotonic() - started) * 1000)
                db.add(row)
                await db.commit()
    raise AiError("Every model failed: " + " · ".join(errors))
