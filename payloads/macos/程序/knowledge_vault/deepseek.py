from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import VaultConfig


class DeepSeekError(RuntimeError):
    pass


Transport = Callable[[str, dict[str, str], bytes, int], tuple[int, bytes]]


def load_api_key(service: str, label: str = "DeepSeek") -> str:
    result = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-a", os.environ.get("USER", ""), "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    key = result.stdout.strip()
    if result.returncode or not key:
        raise DeepSeekError(f"{label} 密钥不可用；请检查钥匙串服务 {service}")
    return key


def _urllib_transport(url: str, headers: dict[str, str], body: bytes, timeout: int) -> tuple[int, bytes]:
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()
    except URLError as exc:
        raise DeepSeekError(f"网络错误：{exc.reason}") from exc


class OpenAICompatibleClient:
    """OpenAI-compatible chat/completions adapter.

    `name` must be "deepseek" (built-in defaults) or a key in
    `config.api_providers` (custom base_url / model / keychain service).
    """

    def __init__(
        self,
        config: VaultConfig,
        name: str,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.name = name
        self.transport = transport or _urllib_transport
        self.sleep = sleep
        if name == "deepseek":
            self.base_url = "https://api.deepseek.com/chat/completions"
            self.model = config.deepseek_model
            self.keychain_service = config.keychain_service
            self.timeout_seconds = config.deepseek_timeout_seconds
            self.thinking = config.deepseek_thinking
        else:
            spec = config.api_providers.get(name) or {}
            base = spec.get("base_url", "").rstrip("/")
            self.base_url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
            self.model = spec.get("model", "")
            self.keychain_service = spec.get("keychain_service") or f"knowledge-vault-{name}"
            self.timeout_seconds = int(spec.get("timeout_seconds", 120))
            self.thinking = bool(spec.get("thinking", True))

    def json_completion(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        max_tokens: int = 6000,
        attempts: int = 3,
    ) -> dict[str, Any]:
        key = load_api_key(self.keychain_service, self.config.provider_label(self.name))
        payload = {
            "model": model or self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        if not self.thinking:
            payload["thinking"] = {"type": "disabled"}
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        label = self.config.provider_label(self.name)
        last_error = ""
        for attempt in range(attempts):
            status, raw = self.transport(
                self.base_url, headers, encoded, self.timeout_seconds
            )
            if status == 200:
                try:
                    envelope = json.loads(raw)
                    content = envelope["choices"][0]["message"]["content"].strip()
                    if not content:
                        raise ValueError("模型返回空内容")
                    parsed = json.loads(content)
                    if not isinstance(parsed, dict):
                        raise ValueError("JSON 根节点不是对象")
                    return parsed
                except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    last_error = f"{label} 返回无法解析：{exc}"
            elif status == 429 or status >= 500:
                last_error = f"{label} 暂时不可用（HTTP {status}）"
            else:
                try:
                    detail = json.loads(raw).get("error", {}).get("message", "")
                except Exception:
                    detail = ""
                raise DeepSeekError(f"{label} 请求失败（HTTP {status}）{': ' + detail if detail else ''}")
            if attempt + 1 < attempts:
                self.sleep(2**attempt)
        raise DeepSeekError(last_error or f"{label} 请求失败")


class DeepSeekClient(OpenAICompatibleClient):
    """Backwards-compatible name for the built-in DeepSeek provider."""

    def __init__(
        self,
        config: VaultConfig,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        super().__init__(config, "deepseek", transport=transport, sleep=sleep)
