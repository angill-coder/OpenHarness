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
  POST /api/advance           {id, llm_backend?, llm_model?}  -> 生成下一版 skill(optimizer+gate)
  POST /api/import_output     {id, case_id, report_text, version?}  -> 存平台跑出的真实报告文本
  POST /api/import_judgment   {id, case_id, scores:{dim:score}, reasoning?, version?}  -> 存平台LLM-judge六维分(覆盖mock)
  POST /api/run_judge_batch   {id, version?, parallel?, judge_strategy?, llm_backend?, llm_model?} -> 并发 Judge 当前版本全部 case
  POST /api/generation/start  {id, idempotency_key?, parallel?, model?} -> 后台调用 WB 并自动批量导入
  GET  /api/generation?id=    -> 查询生成任务
  POST /api/generation/retry  {job_id, parallel?, model?} -> 仅重跑未导入的 case
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
from pathlib import Path
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
import llm_client  # noqa: E402
from generation_jobs import (  # noqa: E402
    GenerationJobError,
    GenerationJobService,
)
from model_config import (  # noqa: E402
    DEFAULT_EVALUATION_WB_MODEL,
    SUPPORTED_WB_MODELS,
)
from judge_batch import (  # noqa: E402
    JUDGE_STRATEGY_PER_DIMENSION,
    judge_cases,
    normalize_judge_strategy,
)
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
    return _pick("dataset")


def _load_evidence_metadata(cases, dataset_path):
    """为 Judge 的临时 case 副本加载同目录 Evidence Metadata。"""
    dataset_root = Path(dataset_path).expanduser().resolve().parent
    prepared = []
    errors = []
    for source_case in cases:
        case = dict(source_case)
        case_id = str(case.get("case_id") or "")
        candidates = set()
        for item in case.get("input_files") or []:
            if not isinstance(item, dict) or not item.get("source"):
                continue
            source = Path(str(item["source"])).expanduser()
            if not source.is_absolute():
                source = (dataset_root / source).resolve()
            if source.name == "source":
                candidates.add(source.parent / "evidence_metadata.json")
        if len(candidates) != 1:
            errors.append(
                "%s: 无法从 input_files.source 唯一定位 evidence metadata"
                % case_id
            )
            continue
        path = next(iter(candidates))
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append("%s: %s (%s)" % (case_id, path, exc))
            continue
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != "openharness-evidence/v1"
            or payload.get("case_id") != case_id
            or not isinstance(payload.get("items"), list)
            or not payload["items"]
        ):
            errors.append("%s: Evidence Metadata 结构或 case_id 不合法" % case_id)
            continue
        case["evidence_metadata"] = payload
        prepared.append(case)
    if errors:
        raise ValueError("Evidence Metadata 预检失败: " + "; ".join(errors))
    return prepared


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
    """组装单维度 Judge 提示词，并按调用方提供的上下文动态分区。"""
    dimensions = rubric.get("dimensions") or []
    L = [
        "你是严格的调研报告评审。",
        "只评价【报告正文】实际呈现的内容；背景和 Evidence Metadata "
        "只用于核验，不得算作报告已经写到。",
        "对本次列出的每一条 check 判 "
        "met(满足)/partial(部分)/miss(不满足)。",
    ]
    if len(dimensions) == 1:
        dimension = dimensions[0]
        L += [
            "",
            "本次是独立维度评审，只评 `%s`（%s）。"
            % (
                dimension.get("name", ""),
                dimension.get("name_zh", ""),
            ),
            "不要推测、补评或平衡其他维度。",
        ]
    context = dict(case_context or {})
    # Judge 不接收 ground truth；即使调用方误传，也在 Prompt 边界丢弃。
    context.pop("ground_truth", None)
    if context.get("background"):
        L.append(
            "背景信息只用于确定任务范围、研究问题和受众，不是事实证据。"
        )
    if context.get("evidence_metadata"):
        L += [
            "Evidence Metadata 是从原始资料提炼的证据索引，不是参考答案。",
            "用它核验事实、冲突、口径、样本边界和异常；"
            "报告论断未被索引覆盖时可以判为无法回溯，"
            "但不得仅据此直接认定为事实编造。",
            "只有与 Evidence Metadata 明确冲突或报告给出无依据的确定性事实时，"
            "才对“不编造·不曲解”降档。",
        ]
    if set(context) <= {"case_id"}:
        L.append("本维度只根据报告正文和 check 本身判断。")
    L += ["", "## 逐条 check（每条都要打分）"]
    check_ids = []
    for d in rubric["dimensions"]:
        for c in d.get("checks", []):
            check_ids.append(str(c["id"]))
            rl = " [红线]" if c.get("redline") else ""
            L.append("- %s(%s·%s%s): %s | 触发降档: %s" % (
                c["id"],
                d.get("name_zh", d.get("name", "")),
                c.get("label", c["id"]),
                rl,
                c.get("desc", ""),
                c.get("effect", ""),
            ))
    for key, title in (
        ("background", "背景信息（round 0–1）"),
        ("evidence_metadata", "Evidence Metadata"),
    ):
        if context.get(key):
            L += [
                "",
                "## " + title,
                json.dumps(context[key], ensure_ascii=False),
            ]
    example = {
        "checks": {check_id: "met" for check_id in check_ids},
        "reasoning": {
            check_id: "一句话说明判定依据"
            for check_id in check_ids
        },
    }
    L += [
        "",
        "## 报告正文",
        report_text or "(空)",
        "",
        "## 输出（只输出严格 JSON，不要多余文字）",
        json.dumps(example, ensure_ascii=False),
    ]
    return "\n".join(L)


# 判分/优化共用的 LLM 调用与 JSON 抽取已抽到 llm_client(断循环依赖)。
# 此处保留同名薄别名,判分链路字节等价。
_call_opus = llm_client.call_llm
_extract_json = llm_client.extract_json


def _judge_parallelism(requested=None):
    value = (
        os.environ.get("OPENHARNESS_JUDGE_PARALLEL", "20")
        if requested is None
        else requested
    )
    if isinstance(value, bool) or (
        isinstance(value, float) and not value.is_integer()
    ):
        raise ValueError("Judge 并发必须是整数")
    try:
        parallel = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Judge 并发必须是整数") from exc
    if parallel < 1:
        raise ValueError("Judge 并发必须至少为 1")
    return parallel


def _llm_selection(payload, purpose):
    prefix = "OPENHARNESS_%s" % purpose.upper()
    backend = llm_client.normalize_backend(
        payload.get("llm_backend")
        or os.environ.get(prefix + "_LLM_BACKEND", "workbuddy")
    )
    model = None
    if backend == llm_client.LLM_BACKEND_WORKBUDDY:
        model = llm_client.normalize_workbuddy_model(
            payload.get("llm_model")
            or os.environ.get(prefix + "_WB_MODEL")
            or DEFAULT_EVALUATION_WB_MODEL
        )
    return backend, model


def _judge_summary(
    results,
    parallel=None,
    strategy=JUDGE_STRATEGY_PER_DIMENSION,
    dimension_count=0,
    llm_backend="workbuddy",
    llm_model=None,
):
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
        "llm_backend": llm_backend,
        "model": (
            llm_model
            if llm_backend == llm_client.LLM_BACKEND_WORKBUDDY
            else os.environ.get(
                "ANTHROPIC_JUDGE_MODEL",
                "claude-opus-4-8",
            )
        ),
        "parallel": _judge_parallelism(parallel),
        "judge_strategy": strategy,
        "model_calls_per_case": (
            dimension_count
            if strategy == JUDGE_STRATEGY_PER_DIMENSION
            else 1
        ),
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
            payload = GENERATION_SERVICE.configuration()
            payload.update(
                {
                    "judge_parallel": _judge_parallelism(),
                    "judge_strategy": normalize_judge_strategy(
                        os.environ.get(
                            "OPENHARNESS_JUDGE_STRATEGY",
                            JUDGE_STRATEGY_PER_DIMENSION,
                        )
                    ),
                    "llm_backends": ["api", "workbuddy"],
                    "evaluation_models": list(SUPPORTED_WB_MODELS),
                    "evaluation_model_default": DEFAULT_EVALUATION_WB_MODEL,
                    "judge_llm_backend": os.environ.get(
                        "OPENHARNESS_JUDGE_LLM_BACKEND",
                        "workbuddy",
                    ),
                    "judge_wb_model": os.environ.get(
                        "OPENHARNESS_JUDGE_WB_MODEL",
                        DEFAULT_EVALUATION_WB_MODEL,
                    ),
                    "optimizer_llm_backend": os.environ.get(
                        "OPENHARNESS_OPTIMIZER_LLM_BACKEND",
                        "workbuddy",
                    ),
                    "optimizer_wb_model": os.environ.get(
                        "OPENHARNESS_OPTIMIZER_WB_MODEL",
                        DEFAULT_EVALUATION_WB_MODEL,
                    ),
                }
            )
            return self._send(200, payload)
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
            rows = _load_sample(product)
            return self._send(200, {"rows": rows, "n": len(rows)})
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
            mode = (b.get("optimizer_mode") or "switch_search").strip() or "switch_search"
            if mode not in ("switch_search", "llm_rewrite"):
                return self._send(400, {"error": "非法 optimizer_mode: %s" % mode})
            v0_strategy = (
                b.get("v0_strategy") or "base_skill"
            ).strip() or "base_skill"
            if v0_strategy not in ("base_skill", "llm_scratch"):
                return self._send(
                    400,
                    {"error": "非法 v0_strategy: %s" % v0_strategy},
                )
            if mode != "llm_rewrite" and v0_strategy != "base_skill":
                return self._send(
                    400,
                    {"error": "llm_scratch 仅适用于 llm_rewrite 模式"},
                )
            stop = b.get("optimizer_stop") or {}
            if not isinstance(stop, dict):
                return self._send(400, {"error": "optimizer_stop 必须是对象"})
            sid = uuid.uuid4().hex[:8]
            try:
                SESSIONS[sid] = session_mod.Session(
                    sid,
                    req,
                    pid,
                    prefer_real=PREFER_REAL,
                    optimizer_mode=mode,
                    optimizer_stop=stop,
                    v0_strategy=v0_strategy,
                )
            except llm_client.LLMClientError as e:
                return self._send(502, {"error": "LLM 起草 v0 失败: %s" % e})
            except ValueError as e:
                return self._send(400, {"error": "生成 v0 失败: %s" % e})
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
                rows = _load_sample(s.rubric.get("product"))
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
                    result = s.import_data(rows, account=acct)
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            return self._send(200, result)

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
            try:
                llm_backend, llm_model = _llm_selection(
                    b,
                    "optimizer",
                )
            except llm_client.LLMClientError as exc:
                return self._send(400, {"error": str(exc)})
            try:
                with _session_lock(s.id):
                    result = s.advance(
                        account=acct,
                        llm_backend=llm_backend,
                        llm_model=llm_model,
                    )
            except llm_client.LLMClientError as exc:
                return self._send(
                    502,
                    {"error": "LLM 改写失败: %s" % exc},
                )
            except Exception as exc:
                print("[advance] 生成下一版失败: %s" % exc)
                return self._send(
                    500,
                    {"error": "生成下一版失败: %s" % exc},
                )
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
                    parallel=b.get("parallel"),
                    model=b.get("model"),
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
                    parallel=b.get("parallel"),
                    model=b.get("model"),
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

        if u.path == "/api/run_judge":
            return self._send(
                410,
                {"error": "单 case Judge 已停用；请使用 /api/run_judge_batch"},
            )

        if u.path == "/api/run_judge_batch":
            s = self._sess(b.get("id"))
            if not s:
                return
            try:
                judge_parallel = _judge_parallelism(
                    b.get("parallel")
                )
                judge_strategy = normalize_judge_strategy(
                    b.get("judge_strategy")
                    or os.environ.get(
                        "OPENHARNESS_JUDGE_STRATEGY",
                        JUDGE_STRATEGY_PER_DIMENSION,
                    )
                )
                judge_llm_backend, judge_llm_model = _llm_selection(
                    b,
                    "judge",
                )
            except (ValueError, llm_client.LLMClientError) as exc:
                return self._send(400, {"error": str(exc)})
            ver = b.get("version") or s._eval_target()["version"]
            if ver != s._eval_target()["version"]:
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
                judge_dimension_count = sum(
                    bool(dimension.get("checks"))
                    for dimension in rubric.get("dimensions", [])
                )
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
                                **_judge_summary(
                                    [],
                                    judge_parallel,
                                    judge_strategy,
                                    judge_dimension_count,
                                    judge_llm_backend,
                                    judge_llm_model,
                                ),
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

                if GENERATION_SERVICE is None:
                    return self._send(
                        503,
                        {"error": "GenerationJobService 尚未初始化"},
                    )
                try:
                    cases = _load_evidence_metadata(
                        cases,
                        GENERATION_SERVICE.settings.dataset_path,
                    )
                except ValueError as exc:
                    return self._send(409, {"error": str(exc)})

                def persist_result(item):
                    if item.get("status") != "judged":
                        return item
                    case_id = item["case_id"]
                    with _session_lock(s.id):
                        if s._eval_target()["version"] != ver:
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
                                    "llm_backend": judge_llm_backend,
                                    "model": (
                                        judge_llm_model
                                        or os.environ.get(
                                            "ANTHROPIC_JUDGE_MODEL",
                                            "claude-opus-4-8",
                                        )
                                    ),
                                }
                            },
                            ver,
                            account=acct,
                            evaluate_now=False,
                        )
                    return item

                def call_judge_model(prompt):
                    return llm_client.call_llm(
                        prompt,
                        backend=judge_llm_backend,
                        model=judge_llm_model,
                    )

                results = judge_cases(
                    cases,
                    reports,
                    rubric,
                    _build_judge_prompt,
                    call_judge_model,
                    _extract_json,
                    parallel=judge_parallel,
                    on_result=persist_result,
                    strategy=judge_strategy,
                )
                with _session_lock(s.id):
                    s.evaluate(acct)
                    s._save()
                    # llm_rewrite:候选判分完成 -> 自动 gate 结算(采纳/回滚)
                    settle = s.settle_pending_candidate(acct)
                    state = s.view(acct)
                summary = _judge_summary(
                    results,
                    judge_parallel,
                    judge_strategy,
                    judge_dimension_count,
                    judge_llm_backend,
                    judge_llm_model,
                )
                summary["remaining_cases"] = len(
                    state["judge_progress"]["pending_judge_case_ids"]
                )
                if summary["remaining_cases"] == 0:
                    summary["status"] = "completed"
                if settle:
                    summary["candidate_settled"] = settle
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
