# -*- coding: utf-8 -*-
"""llm_client.py — 判分/优化共用的 LLM 调用与 JSON 抽取。

从 server.py 抽出,断开 app→server 循环依赖:判分链路(judge)与优化链路
(optimizer02)都复用同一条 LLM 线。server.py 保留同名薄别名以保证字节等价。
"""

from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request


class LLMClientError(RuntimeError):
    """LLM 配置、网络或响应格式错误。"""


def _timeout_seconds(value=None) -> float:
    raw = (
        os.environ.get("LLM_TIMEOUT_SECONDS", "180")
        if value is None
        else value
    )
    try:
        timeout = float(raw)
    except (TypeError, ValueError) as exc:
        raise LLMClientError("LLM 超时时间必须是数字") from exc
    if timeout <= 0:
        raise LLMClientError("LLM 超时时间必须大于 0")
    return timeout


def _retry_count(value=None) -> int:
    # 判分等通用 LLM 调用默认自动重试 2 次；可用 LLM_RETRIES 覆盖。
    raw = os.environ.get("LLM_RETRIES", "2") if value is None else value
    if isinstance(raw, bool):
        raise LLMClientError("LLM 重试次数必须是非负整数")
    try:
        retries = int(raw)
    except (TypeError, ValueError) as exc:
        raise LLMClientError("LLM 重试次数必须是非负整数") from exc
    if retries < 0:
        raise LLMClientError("LLM 重试次数必须是非负整数")
    return retries


def _ssl_context() -> ssl.SSLContext:
    """构造带可用 CA 的 SSL context。

    Framework 版 Python(尤其 macOS)常缺根证书 -> CERTIFICATE_VERIFY_FAILED。
    优先 certifi;其次常见系统 CA 包(macOS/主流 Linux);都没有再回退默认。
    也尊重环境变量 SSL_CERT_FILE(若已设由 create_default_context 自动采用)。
    """
    if os.environ.get("SSL_CERT_FILE"):
        return ssl.create_default_context()
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    for ca in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
        if os.path.exists(ca):
            return ssl.create_default_context(cafile=ca)
    return ssl.create_default_context()


def call_llm(
    prompt: str,
    timeout_seconds=None,
    retries=None,
) -> str:
    """调 LLM，并把配置、传输与响应错误统一转换为 LLMClientError。"""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LLMClientError(
            "未设置 ANTHROPIC_API_KEY —— 无法调用 LLM，请配置后重启 server"
        )
    base = os.environ.get(
        "ANTHROPIC_BASE_URL",
        "https://api.anthropic.com",
    ).rstrip("/")
    model = os.environ.get("ANTHROPIC_JUDGE_MODEL", "claude-opus-4-8")
    style = os.environ.get("LLM_API_STYLE", "").lower() or (
        "anthropic" if "anthropic.com" in base else "openai"
    )
    if style not in ("openai", "anthropic"):
        raise LLMClientError("LLM_API_STYLE 仅支持 openai 或 anthropic")
    if style == "openai":
        url = base + "/v1/chat/completions"
        headers = {
            "Authorization": "Bearer " + key,
            "content-type": "application/json",
        }
    else:
        url = base + "/v1/messages"
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    try:
        max_tokens = int(os.environ.get("LLM_MAX_TOKENS", "16000"))
    except (TypeError, ValueError) as exc:
        raise LLMClientError("LLM_MAX_TOKENS 必须是整数") from exc
    if max_tokens <= 0:
        raise LLMClientError("LLM_MAX_TOKENS 必须大于 0")
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers=headers,
    )
    timeout = _timeout_seconds(timeout_seconds)
    retry_limit = _retry_count(retries)
    raw = None
    for attempt in range(retry_limit + 1):
        try:
            with urllib.request.urlopen(
                req,
                timeout=timeout,
                context=_ssl_context(),
            ) as resp:
                raw = resp.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            retryable = exc.code in (408, 429, 500, 502, 503, 504)
            if retryable and attempt < retry_limit:
                time.sleep(min(2 ** attempt, 4))
                continue
            raise LLMClientError(
                "上游 LLM 返回 HTTP %s%s" % (
                    exc.code,
                    "（已重试 %d 次）" % attempt if attempt else "",
                )
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt < retry_limit:
                time.sleep(min(2 ** attempt, 4))
                continue
            reason = getattr(exc, "reason", None) or str(exc)
            raise LLMClientError(
                "连接上游 LLM 失败: %s%s" % (
                    reason,
                    "（已重试 %d 次）" % attempt if attempt else "",
                )
            ) from exc

    try:
        payload = json.loads(raw)
        if style == "openai":
            content = payload["choices"][0]["message"]["content"]
        else:
            blocks = payload["content"]
            content = "".join(
                block.get("text", "")
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )
        if not isinstance(content, str):
            raise TypeError("content 不是字符串")
        return content
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise LLMClientError("上游 LLM 响应格式无效") from exc


def _balanced_json_span(text: str):
    """从第一个 '{' 起做栈式括号配平(跳过字符串内内容与转义), 返回配平的 {...} 子串。

    比贪婪的 \\{.*\\} 稳: 正文里若含花括号/引号也能正确框定最外层对象。
    截断(未闭合)时返回 None。
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def extract_json(text: str):
    """从 LLM 文本里稳健抽取一个 JSON 对象。

    依次尝试多个候选并逐个 json.loads, 命中即返回:
      1) ```json ... ``` / ``` ... ``` 代码块内的内容(优先, llm_rewrite 用围栏包裹);
      2) 栈式括号配平框定的最外层 {...}(能容忍正文含花括号/引号);
      3) 贪婪 \\{.*\\}(原逻辑, 兜底)。
    全部失败返回 None。
    """
    if not text:
        return None
    candidates = []
    for m in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.S):
        seg = (m.group(1) or "").strip()
        if seg:
            candidates.append(seg)
    span = _balanced_json_span(text)
    if span:
        candidates.append(span)
    greedy = re.search(r"\{.*\}", text, re.S)
    if greedy:
        candidates.append(greedy.group(0))
    for cand in candidates:
        try:
            return json.loads(cand)
        except Exception:
            continue
    return None
