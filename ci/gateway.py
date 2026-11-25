"""OpenAI Chat Completions relay with a fail-closed per-attempt budget."""
from __future__ import annotations

import http.server
import json
import os
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


def gmi_request(path, payload=None):
    key = os.environ.get("GMI_API_KEY", "").strip()
    if not key:
        raise ValueError("GMI_API_KEY is missing")
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if organization := os.environ.get("GMI_ORGANIZATION_ID"):
        headers["X-Organization-ID"] = organization
    data = None if payload is None else json.dumps(payload).encode()
    return urllib.request.build_opener(NoRedirect()).open(
        urllib.request.Request(BASE + path, data=data, headers=headers), timeout=180
    )


def inventory():
    with gmi_request("/models") as response:
        data = json.load(response)["data"]
    models = {item["id"]: item for item in data}
    if not models or len(models) != len(data):
        raise ValueError("Empty or ambiguous GMI model inventory")
    return models


class Budget:
    def __init__(self, model, context, max_output, input_rate, output_rate, cap, calls, effort, log):
        self.model = model
        self.context = context
        self.max_output = max_output
        self.input_rate = Decimal(str(input_rate))
        self.output_rate = Decimal(str(output_rate))
        self.cap = Decimal(str(cap))
        self.max_calls = calls
        self.effort = effort
        self.log = log
        self.charged = Decimal(0)
        self.calls = 0
        self.denied = 0
        self.errors = 0
        self.lock = threading.Lock()
        self.reserve = self.price(context, max_output)

    def price(self, prompt, output):
        return (prompt * self.input_rate + output * self.output_rate) / 1_000_000

    def begin(self):
        with self.lock:
            if self.calls >= self.max_calls or self.charged + self.reserve > self.cap:
                self.denied += 1
                return None
            self.calls += 1
            self.charged += self.reserve
            return self.calls

    def finish(self, call, usage, status, tool_names, duration, provider_model=None):
        prompt = (usage or {}).get("prompt_tokens")
        completion = (usage or {}).get("completion_tokens")
        valid = all(type(n) is int and n >= 0 for n in (prompt, completion))
        actual = self.price(prompt, completion) if valid else self.reserve
        with self.lock:
            if valid and prompt <= self.context and completion <= self.max_output:
                self.charged += actual - self.reserve
            elif valid:
                self.charged += max(actual - self.reserve, Decimal(0))
                self.cap = self.charged
            self.errors += status != 200
            with self.log.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "call": call, "status": status, "usage": usage, "returned_model": provider_model,
                    "tool_names": sorted(tool_names), "seconds": round(duration, 3),
                    "estimated_usd_uncached": str(actual) if valid else None,
                    "budget_charge_usd": str(self.charged),
                }) + "\n")


class Relay(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    def __init__(self, socket_path, budget):
        self.budget = budget
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
        start = time.monotonic()
        status, usage, tool_names, provider_model = 502, None, set(), None
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
            with gmi_request("/chat/completions", body) as response:
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
                        message = choice.get("delta") or choice.get("message") or {}
                        for tool in message.get("tool_calls") or []:
                            if name := (tool.get("function") or {}).get("name"):
                                tool_names.add((choice.get("index", 0), tool.get("index", 0), name))
                    self.wfile.write(line)
                    self.wfile.flush()
        except urllib.error.HTTPError as exc:
            status = exc.code
            self.error(status, f"GMI HTTP {status}")
        except (BrokenPipeError, ConnectionResetError, TimeoutError, urllib.error.URLError):
            if status != 200:
                self.error(502, "GMI connection failed")
        finally:
            budget.finish(call, usage, status, tool_names, time.monotonic() - start, provider_model)
