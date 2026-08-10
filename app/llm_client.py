# -*- coding: utf-8 -*-
"""llm_client.py — 判分/优化共用的 LLM 调用与 JSON 抽取。

从 server.py 抽出,断开 app→server 循环依赖:判分链路(judge)与优化链路
(optimizer02)都复用同一条 LLM 线。server.py 保留同名薄别名以保证字节等价。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parent / "harness"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from model_config import (  # noqa: E402
    DEFAULT_CODEX_REASONING_EFFORT,
    DEFAULT_EVALUATION_API_MODEL,
    DEFAULT_EVALUATION_CODEX_MODEL,
    DEFAULT_EVALUATION_WB_MODEL,
    SUPPORTED_CODEX_MODELS,
    SUPPORTED_CODEX_REASONING_EFFORTS,
    SUPPORTED_WB_MODELS,
)
from workbuddy_batch.adapter import (  # noqa: E402
    discover_command,
    infer_product_config,
)
from workbuddy_batch.events import EventCollector, assistant_text  # noqa: E402


class LLMClientError(RuntimeError):
    """LLM 配置、网络或响应格式错误。"""


LLM_BACKEND_API = "api"
LLM_BACKEND_WORKBUDDY = "workbuddy"
LLM_BACKEND_CODEX = "codex"
LLM_BACKENDS = (
    LLM_BACKEND_API,
    LLM_BACKEND_WORKBUDDY,
    LLM_BACKEND_CODEX,
)


def normalize_backend(value=None) -> str:
    backend = str(value or LLM_BACKEND_API).strip().lower()
    aliases = {
        "wb": LLM_BACKEND_WORKBUDDY,
        "workbuddy_cli": LLM_BACKEND_WORKBUDDY,
        "codex_cli": LLM_BACKEND_CODEX,
    }
    backend = aliases.get(backend, backend)
    if backend not in LLM_BACKENDS:
        raise LLMClientError(
            "LLM 调用方式仅支持 api、workbuddy 或 codex"
        )
    return backend


def normalize_workbuddy_model(value=None) -> str:
    model = str(value or DEFAULT_EVALUATION_WB_MODEL).strip()
    if model not in SUPPORTED_WB_MODELS:
        raise LLMClientError("不支持的 WorkBuddy 模型: %s" % model)
    return model


def normalize_api_model(value=None) -> str:
    """API 模型允许使用预设值，也允许用户输入中转服务支持的模型名。"""
    model = str(
        value
        or os.environ.get("ANTHROPIC_JUDGE_MODEL")
        or DEFAULT_EVALUATION_API_MODEL
    ).strip()
    if not model:
        raise LLMClientError("API 模型不能为空")
    return model


def normalize_codex_model(value=None) -> str:
    model = str(value or DEFAULT_EVALUATION_CODEX_MODEL).strip()
    if model not in SUPPORTED_CODEX_MODELS:
        raise LLMClientError("不支持的 Codex 模型: %s" % model)
    return model


def normalize_codex_reasoning_effort(value=None) -> str:
    effort = str(value or DEFAULT_CODEX_REASONING_EFFORT).strip().lower()
    if effort not in SUPPORTED_CODEX_REASONING_EFFORTS:
        raise LLMClientError(
            "不支持的 Codex 推理力度: %s；可选值为 %s"
            % (effort, ", ".join(SUPPORTED_CODEX_REASONING_EFFORTS))
        )
    return effort


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


def _call_api(
    prompt: str,
    timeout_seconds=None,
    retries=None,
    model=None,
) -> str:
    """通过原有 Anthropic/OpenAI-compatible API 调用 LLM。"""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LLMClientError(
            "未设置 ANTHROPIC_API_KEY —— 无法调用 LLM，请配置后重启 server"
        )
    base = os.environ.get(
        "ANTHROPIC_BASE_URL",
        "https://api.anthropic.com",
    ).rstrip("/")
    selected_model = normalize_api_model(model)
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
        "model": selected_model,
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


def _workbuddy_environment(command: tuple[str, ...]) -> dict[str, str]:
    environment = dict(os.environ)
    # WorkBuddy 的 Auto Memory 与相关性筛选是独立机制；显式关闭读取与写入。
    environment["CODEBUDDY_DISABLE_AUTO_MEMORY"] = "1"
    environment["CODEBUDDY_MEMORY_RELEVANCE_DISABLED"] = "1"
    environment["CODEBUDDY_MEMORY_EXTRACTION_DISABLED"] = "1"
    environment["CODEBUDDY_TEAM_MEMORY_ENABLED"] = "0"
    workbuddy_home = os.environ.get("OPENHARNESS_WB_HOME")
    if workbuddy_home:
        environment["CODEBUDDY_CONFIG_DIR"] = str(
            Path(workbuddy_home).expanduser()
        )
    product_config = os.environ.get("OPENHARNESS_WB_PRODUCT_CONFIG")
    inferred = infer_product_config(command)
    if product_config:
        environment["ACC_PRODUCT_CONFIG_PATH"] = str(
            Path(product_config).expanduser()
        )
    elif inferred:
        environment["ACC_PRODUCT_CONFIG_PATH"] = str(inferred)
    return environment


def _parse_workbuddy_output(raw: str) -> str:
    collector = EventCollector("openharness-llm")
    for line in (raw or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            collector.consume(event, 0, 0)
    content = assistant_text(collector.assistant_messages, 0)
    if not content and isinstance(collector.result, dict):
        result = collector.result.get("result")
        if isinstance(result, str):
            content = result.strip()
    if not content:
        raise LLMClientError("WorkBuddy CLI 未返回可用的 assistant 文本")
    return content


def _call_workbuddy(
    prompt: str,
    model=None,
    timeout_seconds=None,
    retries=None,
) -> str:
    selected_model = normalize_workbuddy_model(model)
    try:
        command = discover_command(
            os.environ.get("OPENHARNESS_WB_CLI_PATH") or None
        )
    except FileNotFoundError as exc:
        raise LLMClientError(str(exc)) from exc
    args = [
        *command,
        "-p",
        "--output-format",
        "stream-json",
        "--model",
        selected_model,
        "--permission-mode",
        "bypassPermissions",
        "--tools",
        "",
        "--setting-sources",
        "",
        "--no-session-persistence",
        prompt,
    ]
    timeout = _timeout_seconds(timeout_seconds)
    retry_limit = _retry_count(retries)
    for attempt in range(retry_limit + 1):
        try:
            completed = subprocess.run(
                args,
                cwd=tempfile.gettempdir(),
                env=_workbuddy_environment(command),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            if attempt < retry_limit:
                time.sleep(min(2 ** attempt, 4))
                continue
            raise LLMClientError(
                "WorkBuddy CLI 调用超时%s"
                % ("（已重试 %d 次）" % attempt if attempt else "")
            ) from exc
        except OSError as exc:
            raise LLMClientError("无法启动 WorkBuddy CLI: %s" % exc) from exc
        if completed.returncode == 0:
            return _parse_workbuddy_output(completed.stdout)
        if attempt < retry_limit:
            time.sleep(min(2 ** attempt, 4))
            continue
        detail = (completed.stderr or completed.stdout or "").strip()[-800:]
        raise LLMClientError(
            "WorkBuddy CLI 返回退出码 %d%s%s"
            % (
                completed.returncode,
                "（已重试 %d 次）" % attempt if attempt else "",
                ": " + detail if detail else "",
            )
        )
    raise LLMClientError("WorkBuddy CLI 调用失败")


def _discover_codex_command() -> tuple[str, ...]:
    configured = (os.environ.get("OPENHARNESS_CODEX_CLI_PATH") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise LLMClientError("Codex CLI 不存在: %s" % path)
        return (str(path),)
    executable = shutil.which("codex")
    if not executable:
        raise LLMClientError(
            "找不到 Codex CLI。请安装 codex 或设置 OPENHARNESS_CODEX_CLI_PATH"
        )
    return (executable,)


def codex_configuration() -> dict:
    try:
        command = _discover_codex_command()
        return {"ready": True, "error": None, "command": command[0]}
    except LLMClientError as exc:
        return {"ready": False, "error": str(exc), "command": None}


def _call_codex(
    prompt: str,
    model=None,
    reasoning_effort=None,
    timeout_seconds=None,
    retries=None,
) -> str:
    """通过无状态 Codex CLI 调用模型，不加载用户配置或项目规则。"""
    selected_model = normalize_codex_model(model)
    selected_effort = normalize_codex_reasoning_effort(reasoning_effort)
    command = _discover_codex_command()
    timeout = _timeout_seconds(timeout_seconds)
    retry_limit = _retry_count(retries)
    for attempt in range(retry_limit + 1):
        with tempfile.TemporaryDirectory(prefix="openharness-codex-") as tmp:
            output_path = Path(tmp) / "last-message.txt"
            args = [
                *command,
                "--sandbox",
                "read-only",
                "--ask-for-approval",
                "never",
                "--model",
                selected_model,
                "--config",
                'model_reasoning_effort="%s"' % selected_effort,
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--color",
                "never",
                "--output-last-message",
                str(output_path),
                "-",
            ]
            try:
                completed = subprocess.run(
                    args,
                    cwd=tmp,
                    env=dict(os.environ),
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                if attempt < retry_limit:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                raise LLMClientError(
                    "Codex CLI 调用超时%s"
                    % ("（已重试 %d 次）" % attempt if attempt else "")
                ) from exc
            except OSError as exc:
                raise LLMClientError("无法启动 Codex CLI: %s" % exc) from exc
            if completed.returncode == 0:
                content = (
                    output_path.read_text(encoding="utf-8").strip()
                    if output_path.is_file()
                    else ""
                )
                if not content:
                    content = (completed.stdout or "").strip()
                if content:
                    return content
                raise LLMClientError("Codex CLI 未返回可用的最终文本")
            if attempt < retry_limit:
                time.sleep(min(2 ** attempt, 4))
                continue
            detail = (completed.stderr or completed.stdout or "").strip()[-800:]
            raise LLMClientError(
                "Codex CLI 返回退出码 %d%s%s"
                % (
                    completed.returncode,
                    "（已重试 %d 次）" % attempt if attempt else "",
                    ": " + detail if detail else "",
                )
            )
    raise LLMClientError("Codex CLI 调用失败")


def call_llm(
    prompt: str,
    timeout_seconds=None,
    retries=None,
    backend=None,
    model=None,
    reasoning_effort=None,
) -> str:
    """通过 API、WorkBuddy CLI 或 Codex CLI 调用 LLM。"""
    selected_backend = normalize_backend(backend)
    if selected_backend == LLM_BACKEND_WORKBUDDY:
        return _call_workbuddy(
            prompt,
            model=model,
            timeout_seconds=timeout_seconds,
            retries=retries,
        )
    if selected_backend == LLM_BACKEND_CODEX:
        return _call_codex(
            prompt,
            model=model,
            reasoning_effort=reasoning_effort,
            timeout_seconds=timeout_seconds,
            retries=retries,
        )
    return _call_api(
        prompt,
        timeout_seconds=timeout_seconds,
        retries=retries,
        model=model,
    )


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
