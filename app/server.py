#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server.py — OpenHarness 标注/迭代平台 Web 服务 (stdlib http.server, 无依赖)

启动:  python3 server.py            # 默认 http://127.0.0.1:8底口 见下
       python3 server.py --port 8000 --real

API:
  GET  /                      -> index.html
  POST /api/session           {requirement, product_id?}  -> 建会话, 生成 v0 skill+rubric
  GET  /api/session?id=       -> 当前会话完整状态
  POST /api/data              {id, rows?, use_sample?, use_configured?} -> 导入数据
  POST /api/rubric            {id, weights?, target?}  -> 编辑 rubric(存新版本)
  POST /api/advance           {id}  -> 生成下一版 skill(optimizer+gate)
  POST /api/import_output     {id, case_id, report_text, version?}  -> 存平台跑出的真实报告文本
  POST /api/import_judgment   {id, case_id, scores:{dim:score}, reasoning?, version?}  -> 存平台LLM-judge六维分(覆盖mock)
  POST /api/run_judge_batch   {id, version?} -> 并发 Judge 当前版本全部 case
  POST /api/generation/start  {id, idempotency_key?} -> 后台调用 WB 并自动批量导入
  GET  /api/generation?id=    -> 查询生成任务
  POST /api/generation/retry  {job_id} -> 仅重跑未导入的 case
  POST /api/generation/cancel {job_id} -> 请求取消
  GET  /api/sample_data       -> 返回内置样例数据集(供页面一键导入)
"""
import argparse
import base64
from contextlib import nullcontext
import hashlib
import io
import json
import os
import re
import sys
import threading
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import session as session_mod  # noqa: E402
import persistence as persist  # noqa: E402
from generation_jobs import (  # noqa: E402
    GenerationJobError,
    GenerationJobService,
)
from judge_batch import judge_cases  # noqa: E402
from workbuddy_batch.dataset import load_openharness_rows  # noqa: E402
# import auth as auth_mod  # [鉴权已临时关闭·本地测试] 恢复时取消注释

ROOT = os.path.dirname(HERE)
DATA_DIRS = {
    "report-assistant": os.path.join(ROOT, "data", "report_assistant"),
    "research_insight": os.path.join(ROOT, "data", "research_assistant"),
}

SESSIONS = {}          # sid -> Session
PREFER_REAL = False
GENERATION_SERVICE = None
_JUDGE_ACTIVE = set()
_JUDGE_ACTIVE_LOCK = threading.Lock()


def _session_lock(sid):
    if GENERATION_SERVICE is None:
        return nullcontext()
    return GENERATION_SERVICE.session_lock(sid)


def _active_generation(sid):
    if GENERATION_SERVICE is None:
        return None
    return GENERATION_SERVICE.active_for_session(sid)


def _claim_judge(sid):
    with _JUDGE_ACTIVE_LOCK:
        if sid in _JUDGE_ACTIVE:
            return False
        _JUDGE_ACTIVE.add(sid)
        return True


def _release_judge(sid):
    with _JUDGE_ACTIVE_LOCK:
        _JUDGE_ACTIVE.discard(sid)


def _active_judge(sid):
    with _JUDGE_ACTIVE_LOCK:
        return sid in _JUDGE_ACTIVE


def _load_sample(product="report-assistant"):
    """按产品载入内置样例数据集。优先 dataset.jsonl, 回退 *.sample.jsonl。"""
    d = DATA_DIRS.get(product, DATA_DIRS["report-assistant"])
    def _pick(base):
        for name in (base + ".jsonl", base + ".sample.jsonl"):
            p = os.path.join(d, name)
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    return [json.loads(l) for l in f if l.strip()]
        return []
    return _pick("dataset"), _pick("human_labels")


# ---------------- 文件解析 / LLM-judge 调用 ----------------
def _parse_report(filename: str, raw: bytes) -> str:
    """按扩展名把上传的报告文件解析成文本。md/txt 直读;pdf 用 pypdf;docx 用 zipfile+xml。"""
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        import pypdf
        r = pypdf.PdfReader(io.BytesIO(raw))
        return "\n".join((p.extract_text() or "") for p in r.pages)
    if name.endswith(".docx"):
        import zipfile
        import xml.etree.ElementTree as ET
        z = zipfile.ZipFile(io.BytesIO(raw))
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        root = ET.fromstring(z.read("word/document.xml").decode("utf-8", "ignore"))
        return "\n".join("".join(t.text or "" for t in p.iter(ns + "t"))
                         for p in root.iter(ns + "p"))
    return raw.decode("utf-8", "ignore")   # md / txt / 其它当纯文本


def _build_judge_prompt(rubric, report_text, case_context) -> str:
    """组装逐 check 判分提示词:列出所有 check,要求对每条判 met/partial/miss + 理由。"""
    L = ["你是严格的调研报告评审。根据【任务信息】【报告正文】和下面逐条 check，",
         "对**每一条 check** 判 met(满足)/partial(部分)/miss(不满足)。",
         "只能依据报告中呈现的证据、引用和内部一致性判断；"
         "不得把未提供的信息当成已核实事实。",
         "", "## 逐条 check(每条都要打分)"]
    for d in rubric["dimensions"]:
        for c in d.get("checks", []):
            rl = " [红线]" if c.get("redline") else ""
            L.append("- %s(%s·%s%s): %s | 触发降档: %s" % (
                c["id"],
                d.get("name_zh", d.get("name", "")),
                c.get("label", c["id"]),
                rl,
                c.get("desc", ""),
                c.get("effect", ""),
            ))
    L += ["", "## 任务信息", json.dumps(case_context, ensure_ascii=False),
          "", "## 报告正文", report_text or "(空)",
          "", "## 输出(只输出严格 JSON,不要多余文字):",
          '{"checks":{"T1":"met","T2":"miss", ...每条 check 都要},',
          ' "reasoning":{"T1":"一句话","T2":"一句话", ...}}']
    return "\n".join(L)


def _call_opus(prompt: str) -> str:
    """调 LLM 判分。支持直连 Anthropic 或第三方中转(new-api/bianxie 等 OpenAI 兼容)。
    环境变量:
      ANTHROPIC_API_KEY     必填,key
      ANTHROPIC_BASE_URL    base url(默认 https://api.anthropic.com;第三方填其 url)
      ANTHROPIC_JUDGE_MODEL 模型 id(默认 claude-opus-4-8)
      LLM_API_STYLE         openai | anthropic(不填则:非 anthropic.com 域名自动用 openai)
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("未设置 ANTHROPIC_API_KEY —— 无法在页面直调。请先 export 后重启 server。")
    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    model = os.environ.get("ANTHROPIC_JUDGE_MODEL", "claude-opus-4-8")
    style = os.environ.get("LLM_API_STYLE", "").lower() or ("anthropic" if "anthropic.com" in base else "openai")
    if style == "openai":
        url = base + "/v1/chat/completions"
        headers = {"Authorization": "Bearer " + key, "content-type": "application/json"}
    else:
        url = base + "/v1/messages"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    body = json.dumps({"model": model, "max_tokens": 2000,
                       "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=90) as resp:
        j = json.loads(resp.read().decode("utf-8"))
    if style == "openai":
        return j["choices"][0]["message"]["content"]
    return "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")


def _extract_json(text: str):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _judge_parallelism():
    try:
        return max(
            1,
            min(
                int(
                    os.environ.get(
                        "OPENHARNESS_JUDGE_PARALLEL",
                        "20",
                    )
                ),
                20,
            ),
        )
    except ValueError:
        return 20


def _judge_summary(results):
    counts = {
        "judged": 0,
        "failed": 0,
        "missing_report": 0,
        "stale_report": 0,
    }
    for item in results:
        status = item.get("status")
        counts[status] = counts.get(status, 0) + 1
    success = counts["judged"]
    total = len(results)
    return {
        "status": (
            "completed"
            if total and success == total
            else ("partial" if success else "failed")
        ),
        "total_cases": total,
        "judged_cases": success,
        "failed_cases": total - success,
        "missing_report_cases": counts["missing_report"],
        "stale_report_cases": counts["stale_report"],
        "model": os.environ.get("ANTHROPIC_JUDGE_MODEL", "claude-opus-4-8"),
        "parallel": _judge_parallelism(),
    }


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_sha256(value) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _text_sha256(raw)


def _restore_all():
    """启动时从磁盘恢复所有已落盘的 session。"""
    ids = persist.list_session_ids()
    ok = 0
    for sid in ids:
        snap = persist.load_snapshot(sid)
        if not snap:
            continue
        try:
            SESSIONS[sid] = session_mod.Session.restore(snap, prefer_real=PREFER_REAL)
            ok += 1
        except Exception as e:
            print("[restore] 跳过 %s: %s" % (sid, e))
    if ids:
        print("[restore] 从磁盘恢复 %d/%d 个会话" % (ok, len(ids)))
    return ok


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # 静默

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _sess(self, sid):
        s = SESSIONS.get(sid)
        if not s:
            self._send(404, {"error": "会话不存在: %s" % sid})
            return None
        return s

    def _account(self):
        """[鉴权已临时关闭·本地测试] 跳过 iOA 校验, 统一用本地账号。
        恢复 iOA 鉴权: 取消下方注释块 + 顶部 `import auth as auth_mod` + 删掉本地兜底两行。"""
        # --- iOA 鉴权(临时注释, 本地测试用) ---
        # ident = auth_mod.current_user(self.headers)
        # acct = auth_mod.account_of(ident)
        # if not acct:
        #     self._send(401, {"error": "未登录或身份校验失败，请经 iOA 网关访问", "need_auth": True})
        #     return None
        # self._identity = ident
        # return acct
        self._identity = {"LoginName": "local", "DisplayName": "本地测试", "Email": ""}
        return "local"

    # ---------------- GET ----------------
    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            path = os.path.join(HERE, "index.html")
            with open(path, encoding="utf-8") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if u.path == "/app.js":          # 前端逻辑(公开静态, 与 index.html 一致不鉴权)
            path = os.path.join(HERE, "app.js")
            with open(path, encoding="utf-8") as f:
                return self._send(200, f.read(), "text/javascript; charset=utf-8")
        # 其余 /api/* 一律需要 iOA 身份
        acct = self._account()
        if not acct:
            return
        if u.path == "/api/me":
            ident = getattr(self, "_identity", {}) or {}
            return self._send(200, {"login_name": acct,
                                    "display_name": ident.get("DisplayName", acct),
                                    "email": ident.get("Email", "")})
        if u.path == "/api/session":
            q = parse_qs(u.query)
            sid = (q.get("id") or [None])[0]
            s = self._sess(sid)
            if s:
                with _session_lock(sid):
                    self._send(200, s.view(acct))
            return
        if u.path == "/api/generation/config":
            if GENERATION_SERVICE is None:
                return self._send(
                    503,
                    {"error": "GenerationJobService 尚未初始化"},
                )
            return self._send(
                200,
                GENERATION_SERVICE.configuration(),
            )
        if u.path == "/api/generation":
            if GENERATION_SERVICE is None:
                return self._send(
                    503,
                    {"error": "GenerationJobService 尚未初始化"},
                )
            q = parse_qs(u.query)
            job_id = (q.get("id") or [None])[0]
            sid = (q.get("session_id") or [None])[0]
            try:
                if job_id:
                    return self._send(
                        200,
                        GENERATION_SERVICE.get(job_id).to_dict(),
                    )
                if sid:
                    latest = GENERATION_SERVICE.latest_for_session(sid)
                    return self._send(
                        200,
                        {
                            "job": latest.to_dict() if latest else None,
                            "jobs": [
                                item.to_dict()
                                for item in GENERATION_SERVICE.list_for_session(
                                    sid
                                )[:20]
                            ],
                        },
                    )
                return self._send(
                    400,
                    {"error": "缺少 id 或 session_id"},
                )
            except GenerationJobError as exc:
                return self._send(404, {"error": str(exc)})
        if u.path == "/api/sample_data":
            q = parse_qs(u.query)
            sid = (q.get("id") or [None])[0]
            s = SESSIONS.get(sid)
            product = s.rubric.get("product") if s else (q.get("product") or ["report-assistant"])[0]
            rows, labels = _load_sample(product)
            return self._send(200, {"rows": rows, "labels": labels, "n": len(rows)})
        if u.path == "/api/sessions":
            out = []
            for sid, s in SESSIONS.items():
                meta = persist.load_meta(sid) or {}
                with _session_lock(sid):
                    out.append({"id": sid, "product_id": s.product_id,
                                "requirement": s.requirement,
                                "current_version": s._current()["version"],
                                "n_versions": len(s.versions), "n_cases": len(s.cases),
                                "created_at": meta.get("created_at")})
            out.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
            return self._send(200, {"sessions": out})
        return self._send(404, {"error": "not found"})

    # ---------------- POST ----------------
    def do_POST(self):
        u = urlparse(self.path)
        b = self._body()

        # 所有写接口一律需要 iOA 身份
        acct = self._account()
        if not acct:
            return

        if u.path == "/api/session":
            req = (b.get("requirement") or "").strip()
            if not req:
                return self._send(400, {"error": "缺少 requirement(需求描述)"})
            pid = (b.get("product_id") or "custom-skill").strip() or "custom-skill"
            sid = uuid.uuid4().hex[:8]
            try:
                SESSIONS[sid] = session_mod.Session(sid, req, pid, prefer_real=PREFER_REAL)
            except Exception as e:
                return self._send(500, {"error": "生成 v0 失败: %s" % e})
            return self._send(200, SESSIONS[sid].view(acct))

        if u.path == "/api/data":
            s = self._sess(b.get("id"))
            if not s:
                return
            if _active_judge(s.id):
                return self._send(
                    409,
                    {"error": "批量 Judge 进行中，暂不能替换数据集"},
                )
            active = _active_generation(s.id)
            if active:
                return self._send(
                    409,
                    {
                        "error": "真实报告生成中，暂不能替换数据集",
                        "job_id": active.job_id,
                    },
                )
            rows = b.get("rows")
            if b.get("use_sample"):
                rows, _legacy_labels = _load_sample(s.rubric.get("product"))
            if b.get("use_configured"):
                if GENERATION_SERVICE is None:
                    return self._send(
                        503,
                        {"error": "GenerationJobService 尚未初始化"},
                    )
                try:
                    rows = load_openharness_rows(
                        GENERATION_SERVICE.settings.dataset_path
                        .expanduser()
                        .resolve()
                    )
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    return self._send(
                        400,
                        {"error": "配置数据集无法导入: %s" % exc},
                    )
            if not rows:
                return self._send(
                    400,
                    {
                        "error": (
                            "无数据行; 传 rows、use_sample=true "
                            "或 use_configured=true"
                        )
                    },
                )
            try:
                with _session_lock(s.id):
                    result = s.import_data(rows, None, account=acct)
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            return self._send(200, result)

        if u.path == "/api/labels":
            return self._send(
                410,
                {"error": "人工评分入口已停用；请使用批量模型 Judge"},
            )

        if u.path == "/api/rubric":
            s = self._sess(b.get("id"))
            if not s:
                return
            if _active_judge(s.id):
                return self._send(
                    409,
                    {"error": "批量 Judge 进行中，暂不能修改 Rubric"},
                )
            active = _active_generation(s.id)
            if active:
                return self._send(
                    409,
                    {
                        "error": "真实报告生成中，暂不能修改 Rubric",
                        "job_id": active.job_id,
                    },
                )
            with _session_lock(s.id):
                result = s.edit_rubric(
                    {k: b[k] for k in ("weights", "target") if k in b},
                    account=acct,
                )
            return self._send(200, result)

        if u.path == "/api/advance":
            s = self._sess(b.get("id"))
            if not s:
                return
            if _active_judge(s.id):
                return self._send(
                    409,
                    {"error": "批量 Judge 进行中，暂不能推进 Skill 版本"},
                )
            active = _active_generation(s.id)
            if active:
                return self._send(
                    409,
                    {
                        "error": "真实报告生成中，暂不能推进 Skill 版本",
                        "job_id": active.job_id,
                    },
                )
            with _session_lock(s.id):
                result = s.advance(account=acct)
            return self._send(200, result)

        if u.path == "/api/generation/start":
            if GENERATION_SERVICE is None:
                return self._send(
                    503,
                    {"error": "GenerationJobService 尚未初始化"},
                )
            s = self._sess(b.get("id"))
            if not s:
                return
            if _active_judge(s.id):
                return self._send(
                    409,
                    {"error": "批量 Judge 进行中，暂不能启动新一轮报告生成"},
                )
            try:
                job, reused = GENERATION_SERVICE.start(
                    s.id,
                    acct,
                    case_ids=b.get("case_ids"),
                    idempotency_key=(
                        b.get("idempotency_key")
                        or self.headers.get("Idempotency-Key")
                    ),
                )
            except (GenerationJobError, OSError, ValueError) as exc:
                return self._send(400, {"error": str(exc)})
            return self._send(
                200 if reused else 202,
                {"reused": reused, "job": job.to_dict()},
            )

        if u.path == "/api/generation/retry":
            if GENERATION_SERVICE is None:
                return self._send(
                    503,
                    {"error": "GenerationJobService 尚未初始化"},
                )
            try:
                job, reused = GENERATION_SERVICE.retry(
                    b.get("job_id") or "",
                    acct,
                    idempotency_key=(
                        b.get("idempotency_key")
                        or self.headers.get("Idempotency-Key")
                    ),
                )
            except (GenerationJobError, OSError, ValueError) as exc:
                return self._send(400, {"error": str(exc)})
            return self._send(
                200 if reused else 202,
                {"reused": reused, "job": job.to_dict()},
            )

        if u.path == "/api/generation/cancel":
            if GENERATION_SERVICE is None:
                return self._send(
                    503,
                    {"error": "GenerationJobService 尚未初始化"},
                )
            try:
                job = GENERATION_SERVICE.cancel(
                    b.get("job_id") or ""
                )
            except GenerationJobError as exc:
                return self._send(404, {"error": str(exc)})
            return self._send(202, {"job": job.to_dict()})

        if u.path == "/api/import_output":
            s = self._sess(b.get("id"))
            if not s:
                return
            active = _active_generation(s.id)
            if active:
                return self._send(
                    409,
                    {
                        "error": "自动生成导入中，请等待任务结束后再手工导入",
                        "job_id": active.job_id,
                    },
                )
            case_id = b.get("case_id")
            report_text = b.get("report_text") or ""
            version = b.get("version")   # 缺省用当前版本
            with _session_lock(s.id):
                r = s.import_output(
                    case_id,
                    report_text,
                    version,
                    account=acct,
                )
            if "error" in r:
                return self._send(400, r)
            return self._send(200, r)

        if u.path == "/api/import_judgment":
            s = self._sess(b.get("id"))
            if not s:
                return
            with _session_lock(s.id):
                r = s.import_judgment(
                    b.get("case_id"),
                    b.get("scores") or {},
                    b.get("reasoning"),
                    b.get("version"),
                    account=acct,
                )
            if "error" in r:
                return self._send(400, r)
            return self._send(200, r)

        if u.path == "/api/upload_report":
            s = self._sess(b.get("id"))
            if not s:
                return
            active = _active_generation(s.id)
            if active:
                return self._send(
                    409,
                    {
                        "error": "自动生成导入中，请等待任务结束后再上传",
                        "job_id": active.job_id,
                    },
                )
            try:
                raw = base64.b64decode(b.get("content_b64", ""))
            except Exception:
                return self._send(400, {"error": "文件解码失败"})
            try:
                text = _parse_report(b.get("filename", ""), raw)
            except Exception as e:
                return self._send(400, {"error": "解析文件失败: %s" % e})
            if not (text or "").strip():
                return self._send(400, {"error": "未解析出文本(可能是扫描件/加密/空文件)"})
            with _session_lock(s.id):
                result = s.import_output(
                    b.get("case_id"),
                    text,
                    b.get("version"),
                    account=acct,
                )
            return self._send(200, result)

        if u.path == "/api/submit_check_labels":
            return self._send(
                410,
                {"error": "人工 Check 标注已停用；请使用批量模型 Judge"},
            )

        if u.path == "/api/run_judge":
            return self._send(
                410,
                {"error": "单 case Judge 已停用；请使用 /api/run_judge_batch"},
            )

        if u.path == "/api/run_judge_batch":
            s = self._sess(b.get("id"))
            if not s:
                return
            ver = b.get("version") or s._current()["version"]
            if ver != s._current()["version"]:
                return self._send(409, {"error": "只能批量 Judge 当前 Skill 版本"})
            if not _claim_judge(s.id):
                return self._send(409, {"error": "该 Session 已有批量 Judge 正在执行"})
            try:
                if _active_generation(s.id):
                    return self._send(
                        409,
                        {"error": "真实报告生成中，请等待批量导入完成后再 Judge"},
                    )
                with _session_lock(s.id):
                    cases = [dict(case) for case in s.cases]
                    reports = dict(s.report_outputs.get(ver, {}))
                    rubric = dict(s.rubric)
                    existing = dict(s.judge_checks.get(ver, {}))
                if not cases:
                    return self._send(400, {"error": "尚未导入评测 case"})
                missing_reports = [
                    case["case_id"]
                    for case in cases
                    if not (reports.get(case["case_id"]) or "").strip()
                ]
                if missing_reports:
                    return self._send(
                        409,
                        {
                            "error": "请先补齐全部 case 报告；当前缺少: %s"
                            % ", ".join(missing_reports)
                        },
                    )

                rubric_sha256 = _json_sha256(rubric)
                force_all = bool(b.get("force_all"))
                if not force_all:
                    cases = [
                        case
                        for case in cases
                        if case["case_id"] not in existing
                    ]
                if not cases:
                    with _session_lock(s.id):
                        state = s.view(acct)
                    return self._send(
                        200,
                        {
                            "summary": {
                                **_judge_summary([]),
                                "status": "completed",
                                "total_cases": 0,
                                "judged_cases": 0,
                                "failed_cases": 0,
                                "remaining_cases": 0,
                            },
                            "results": [],
                            "state": state,
                        },
                    )

                def persist_result(item):
                    if item.get("status") != "judged":
                        return item
                    case_id = item["case_id"]
                    with _session_lock(s.id):
                        if s._current()["version"] != ver:
                            return {
                                **item,
                                "status": "stale_report",
                                "error": (
                                    "Judge 期间 Skill 版本已变化，结果未写入"
                                ),
                            }
                        current_report = (
                            s.report_outputs.get(ver, {}).get(case_id)
                        )
                        if current_report != reports.get(case_id):
                            return {
                                **item,
                                "status": "stale_report",
                                "error": (
                                    "Judge 期间报告已变化，结果未写入"
                                ),
                            }
                        if _json_sha256(s.rubric) != rubric_sha256:
                            return {
                                **item,
                                "status": "stale_report",
                                "error": (
                                    "Judge 期间 Rubric 已变化，结果未写入"
                                ),
                            }
                        s.set_judge_checks_batch(
                            {
                                case_id: {
                                    "checks": item["checks"],
                                    "reasoning": (
                                        item.get("reasoning") or {}
                                    ),
                                    "report_sha256": _text_sha256(
                                        current_report
                                    ),
                                    "rubric_sha256": rubric_sha256,
                                }
                            },
                            ver,
                            account=acct,
                            evaluate_now=False,
                        )
                    return item

                results = judge_cases(
                    cases,
                    reports,
                    rubric,
                    _build_judge_prompt,
                    _call_opus,
                    _extract_json,
                    parallel=_judge_parallelism(),
                    on_result=persist_result,
                )
                with _session_lock(s.id):
                    s.evaluate(acct)
                    s._save()
                    state = s.view(acct)
                summary = _judge_summary(results)
                summary["remaining_cases"] = len(
                    state["judge_progress"]["pending_judge_case_ids"]
                )
                if summary["remaining_cases"] == 0:
                    summary["status"] = "completed"
                return self._send(
                    200,
                    {
                        "summary": summary,
                        "results": results,
                        "state": state,
                    },
                )
            finally:
                _release_judge(s.id)

        return self._send(404, {"error": "not found"})


def main():
    global PREFER_REAL, GENERATION_SERVICE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--real", action="store_true", help="有 API key 时用真实 Claude 生成/执行")
    args = ap.parse_args()
    PREFER_REAL = args.real

    _restore_all()
    GENERATION_SERVICE = GenerationJobService(SESSIONS)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("OpenHarness 平台已启动: http://%s:%d" % (args.host, args.port))
    print("  backend=%s  (加 --real 且有 ANTHROPIC_API_KEY 时启用真实 Claude)" % (
        "claude?" if PREFER_REAL else "mock"))
    print("  Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
