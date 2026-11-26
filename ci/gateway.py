"""OpenAI Chat Completions relay with a fail-closed per-attempt budget."""
from __future__ import annotations

import http.server
import json
import os
import re
import socketserver
import threading
import time
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path

BASE = "https://api.gmi-serving.com/v1"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def gmi_request(path, payload=None, *, timeout=180):
    key = os.environ.get("GMI_API_KEY", "").strip()
    if not key:
        raise ValueError("GMI_API_KEY is missing")
    headers = {
        "Authorization": "Bearer " + key,
        "Accept": "text/event-stream" if payload and payload.get("stream") else "application/json",
        "Content-Type": "application/json",
        "User-Agent": "HarnessTwin-Hard/1.0 (+https://github.com/himanshu748/harnesstwin-hard)",
    }
    if organization := os.environ.get("GMI_ORGANIZATION_ID"):
        headers["X-Organization-ID"] = organization
    data = None if payload is None else json.dumps(payload).encode()
    return urllib.request.build_opener(NoRedirect()).open(
        urllib.request.Request(BASE + path, data=data, headers=headers), timeout=timeout
    )


def inventory():
    with gmi_request("/models") as response:
        data = json.load(response)["data"]
    models = {item["id"]: item for item in data}
    if not models or len(models) != len(data):
        raise ValueError("Empty or ambiguous GMI model inventory")
    return models


def inventory_failure(exc):
    """Classify a refusal without including provider response text or headers."""
    raw = exc.read(8192).decode("utf-8", errors="ignore").lower()
    content_type = exc.headers.get_content_type()
    reasons = {
        "edge_challenge": ("cf-challenge", "just a moment", "challenge-platform"),
        "network_or_region_policy": ("proxy denied", "network policy", "region is not supported", "access from your location", "blocked by"),
        "org_or_project_scope": ("x-organization-id", "project id", "organization id", "not authorized for this organization"),
        "invalid_credential": ("invalid api key", "invalid authorization", "invalid token", "authentication failed", "key is disabled"),
        "account_gate": ("insufficient", "credits", "payment required", "billing"),
        "permission_denied": ("permission", "forbidden", "access denied", "not authorized"),
    }
    category = next((name for name, terms in reasons.items() if any(term in raw for term in terms)), "unclassified")
    if exc.headers.get("cf-mitigated", "").lower() == "challenge":
        category = "edge_challenge"
    error_code = re.search(r"\berror code\s*[: ]\s*(1[0-9]{3})\b", raw)
    result = {
        "http_status": exc.code,
        "response_format": {
            "application/json": "json", "text/html": "html", "text/plain": "text",
        }.get(content_type, "other"),
        "cloudflare_ray_present": bool(exc.headers.get("cf-ray")),
        "category": category,
    }
    if error_code and result["cloudflare_ray_present"]:
        result["cloudflare_error_code"] = int(error_code.group(1))
    return result


class Budget:
    def __init__(self, model, context, max_output, input_rate, output_rate, cap, calls, effort, log, *,
                 cache_rate=None, upstream_timeout=180, agent_deadline=None):
        self.model = model
        self.context = context
        self.max_output = max_output
        self.input_rate = Decimal(str(input_rate))
        self.output_rate = Decimal(str(output_rate))
        self.cache_rate = Decimal(str(cache_rate)) if cache_rate is not None else self.input_rate
        self.cap = Decimal(str(cap))
        self.max_calls = calls
        self.effort = effort
        self.log = log
        self.upstream_timeout = upstream_timeout
        self.agent_deadline = agent_deadline
        self.charged = Decimal(0)
        self.calls = 0
        self.denied = 0
        self.errors = 0
        self.lock = threading.Lock()
        self.reserve = self.price(context, max_output)

    def price(self, prompt, output, cached=0):
        return ((prompt - cached) * self.input_rate + cached * self.cache_rate + output * self.output_rate) / 1_000_000

    def begin(self):
        with self.lock:
            expired = self.agent_deadline is not None and time.monotonic() >= self.agent_deadline
            if expired or self.calls >= self.max_calls or self.charged + self.reserve > self.cap:
                self.denied += 1
                return None
            self.calls += 1
            self.charged += self.reserve
            return self.calls

    def request_timeout(self):
        if self.agent_deadline is None:
            return self.upstream_timeout
        return max(1, min(self.upstream_timeout, self.agent_deadline - time.monotonic()))

    def finish(self, call, usage, status, tool_names, duration, provider_model=None, finish_reasons=()):
        prompt = (usage or {}).get("prompt_tokens")
        completion = (usage or {}).get("completion_tokens")
        valid = all(type(n) is int and n >= 0 for n in (prompt, completion))
        cached = ((usage or {}).get("prompt_tokens_details") or {}).get("cached_tokens")
        if not valid or type(cached) is not int or not 0 <= cached <= prompt:
            cached = 0
        settled = self.price(prompt, completion, cached) if valid else self.reserve
        with self.lock:
            if valid and prompt <= self.context and completion <= self.max_output:
                self.charged += settled - self.reserve
            elif valid:
                self.charged += max(settled - self.reserve, Decimal(0))
                self.cap = self.charged
            self.errors += status != 200
            with self.log.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "call": call, "status": status, "usage": usage, "returned_model": provider_model,
                    "requested_max_completion_tokens": self.max_output,
                    "finish_reasons": sorted(finish_reasons),
                    "tool_names": sorted(tool_names), "seconds": round(duration, 3),
                    "estimated_usd_uncached": str(self.price(prompt, completion)) if valid else None,
                    "estimated_usd_list_with_reported_cache": str(settled) if valid else None,
                    "budget_charge_usd": str(self.charged),
                }) + "\n")


class Relay(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    def __init__(self, socket_path, budget, *, probe=False):
        self.budget = budget
        self.probe = probe
        super().__init__(str(socket_path), Handler)
        Path(socket_path).chmod(0o666)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def error(self, status, message):
        payload = json.dumps({"error": {"message": message, "type": "harnesstwin_relay"}}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.error(404, "Only Chat Completions is available")
            return
        budget = self.server.budget
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8_000_000:
                raise ValueError()
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict) or body.get("model") != budget.model:
                raise ValueError()
            if any(tool.get("type") != "function" for tool in (body.get("tools") or [])):
                raise ValueError()
        except (AttributeError, TypeError, ValueError):
            self.error(400, "Invalid request, model or tool type")
            return
        call = budget.begin()
        if call is None:
            self.error(429, "Benchmark request or spending cap reached")
            return
        if self.server.probe:
            budget.finish(call, None, 418, set(), 0)
            self.error(418, "Offline request observed. No upstream model call was made.")
            return
        start = time.monotonic()
        status, usage, tool_names, provider_model = 502, None, set(), None
        finish_reasons = set()
        body.pop("max_completion_tokens", None)
        body.pop("reasoning_effort", None)
        body.pop("thinking", None)
        body["max_tokens"] = budget.max_output
        body["context_length_exceeded_behavior"] = "error"
        body["n"] = 1
        if budget.effort:
            body["reasoning_effort"] = budget.effort
        if body.get("stream"):
            body["stream_options"] = {"include_usage": True}
        try:
            with gmi_request("/chat/completions", body, timeout=budget.request_timeout()) as response:
                status = response.status
                self.send_response(status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                self.end_headers()
                for line in response if body.get("stream") else (response.read(),):
                    packet = line[5:].strip() if body.get("stream") and line.startswith(b"data:") else line
                    try:
                        event = json.loads(packet)
                    except ValueError:
                        event = {}
                    usage = event.get("usage") or usage
                    provider_model = event.get("model") or provider_model
                    for choice in event.get("choices") or []:
                        if isinstance(reason := choice.get("finish_reason"), str) and reason:
                            finish_reasons.add(reason)
                        message = choice.get("delta") or choice.get("message") or {}
                        for index, tool in enumerate(message.get("tool_calls") or []):
                            if name := (tool.get("function") or {}).get("name"):
                                tool_names.add((choice.get("index", 0), tool.get("index", index), name))
                    self.wfile.write(line)
                    self.wfile.flush()
        except urllib.error.HTTPError as exc:
            status = exc.code
            self.error(status, f"GMI HTTP {status}")
        except (BrokenPipeError, ConnectionResetError, TimeoutError, urllib.error.URLError):
            if status != 200:
                self.error(502, "GMI connection failed")
        finally:
            budget.finish(call, usage, status, tool_names, time.monotonic() - start, provider_model, finish_reasons)
