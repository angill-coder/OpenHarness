# -*- coding: utf-8 -*-
"""
persistence.py — 会话落盘 (append-only 历史 + 快照 + 重启恢复)

每个 session 一个目录: sessions/<sid>/
  meta.json      不可变元信息: sid / requirement(原始需求描述) / product_id / created_at
  events.jsonl   追加式完整历史, 一行一个事件, 每条带 ts + type + payload:
                   created            —— 生成 v0(含 v0 skill + rubric)
                   import_data        —— 导入数据(case 数 / split)
                   edit_rubric        —— rubric 变更(新版本号 + weights/target)
                   version_adopted    —— 采纳新版 skill(version + proposal + 分数)
                   version_rejected   —— 候选被 gate 拒(version + reason)
  state.json     最新完整快照(用于重启快速恢复; 是 events 的物化结果)

设计: events 是"真相流"(可回溯谁在什么时候改了什么); state 是"当前态"(恢复用)。
两者都在每次变更后即时写盘。恢复时优先读 state.json 重建, 再由 Session 重算派生量。

时间戳: 用 time.time() (真实 wall clock)。落盘/恢复不进 harness 的确定性路径, 无碍复现。
"""
import json
import os
import time
from typing import Any, Dict, List, Optional

# 落盘根目录: 默认 app/sessions/；测试/多实例可用环境变量隔离。
_BASE = os.environ.get(
    "OPENHARNESS_SESSIONS_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions"),
)


def _ensure(sid: str) -> str:
    d = os.path.join(_BASE, sid)
    os.makedirs(d, exist_ok=True)
    return d


def _now() -> float:
    return round(time.time(), 3)


def _atomic_write(path: str, obj: Any):
    """先写临时文件再 rename, 避免半截文件。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------- 写 ----------------
def init_session(sid: str, requirement: str, product_id: str):
    d = _ensure(sid)
    meta_path = os.path.join(d, "meta.json")
    if not os.path.exists(meta_path):
        _atomic_write(meta_path, {
            "sid": sid, "requirement": requirement, "product_id": product_id,
            "created_at": _now(),
        })


def append_event(sid: str, etype: str, payload: Dict[str, Any]):
    d = _ensure(sid)
    rec = {"ts": _now(), "type": etype, "payload": payload}
    with open(os.path.join(d, "events.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def save_snapshot(sid: str, snapshot: Dict[str, Any]):
    d = _ensure(sid)
    snapshot = dict(snapshot)
    snapshot["_saved_at"] = _now()
    _atomic_write(os.path.join(d, "state.json"), snapshot)


def append_output(sid: str, version: str, case_id: str, report_text: str):
    """追加一条平台跑出的真实报告文本(按 版本×case 关联)。落在 outputs.jsonl。"""
    d = _ensure(sid)
    rec = {"ts": _now(), "version": version, "case_id": case_id, "report_text": report_text}
    with open(os.path.join(d, "outputs.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def append_outputs_batch(
    sid: str,
    version: str,
    outputs: Dict[str, str],
    generation_id: str = None,
):
    """一次打开文件写入一批报告，避免逐 case 重复打开和中途重评。"""
    if not outputs:
        return
    d = _ensure(sid)
    now = _now()
    with open(os.path.join(d, "outputs.jsonl"), "a", encoding="utf-8") as f:
        for case_id, report_text in outputs.items():
            rec = {
                "ts": now,
                "version": version,
                "case_id": case_id,
                "report_text": report_text,
            }
            if generation_id:
                rec["generation_id"] = generation_id
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def append_judgment(sid: str, version: str, case_id: str, scores: Dict[str, int],
                    reasoning: Dict[str, str] = None, flagged: List[str] = None):
    """追加一条平台 LLM-as-judge 对真实报告的六维评分(按 版本×case)。落在 judgments.jsonl。"""
    d = _ensure(sid)
    rec = {"ts": _now(), "version": version, "case_id": case_id, "scores": scores,
           "reasoning": reasoning or {}, "flagged": flagged or []}
    with open(os.path.join(d, "judgments.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def append_check_judgment(sid: str, version: str, case_id: str,
                          checks: Dict[str, float], reasoning: Dict[str, str] = None):
    """逐 check 的 LLM-judge 判分(Opus)。落在 check_judgments.jsonl。"""
    d = _ensure(sid)
    rec = {"ts": _now(), "version": version, "case_id": case_id,
           "checks": checks, "reasoning": reasoning or {}}
    with open(os.path.join(d, "check_judgments.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def append_check_judgments(sid: str, version: str, judgments: Dict[str, Dict]):
    """一次追加一批模型逐-check判分，减少批量 Judge 的重复文件开关。"""
    if not judgments:
        return
    d = _ensure(sid)
    now = _now()
    with open(os.path.join(d, "check_judgments.jsonl"), "a", encoding="utf-8") as f:
        for case_id, judgment in judgments.items():
            rec = {
                "ts": now,
                "version": version,
                "case_id": case_id,
                "checks": judgment.get("checks", {}),
                "reasoning": judgment.get("reasoning", {}),
                "report_sha256": judgment.get("report_sha256"),
                "rubric_sha256": judgment.get("rubric_sha256"),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def invalidate_check_judgments(
    sid: str,
    version: str,
    case_ids: List[str],
    reason: str,
):
    if not case_ids:
        return
    d = _ensure(sid)
    now = _now()
    with open(
        os.path.join(d, "check_judgments.jsonl"),
        "a",
        encoding="utf-8",
    ) as f:
        for case_id in case_ids:
            f.write(
                json.dumps(
                    {
                        "ts": now,
                        "version": version,
                        "case_id": case_id,
                        "invalidated": True,
                        "reason": reason,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


# ---------------- 读 ----------------
def list_session_ids() -> List[str]:
    if not os.path.isdir(_BASE):
        return []
    out = []
    for name in os.listdir(_BASE):
        if os.path.exists(os.path.join(_BASE, name, "state.json")):
            out.append(name)
    return sorted(out)


def load_snapshot(sid: str) -> Optional[Dict[str, Any]]:
    p = os.path.join(_BASE, sid, "state.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_events(sid: str) -> List[Dict[str, Any]]:
    p = os.path.join(_BASE, sid, "events.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def load_outputs(sid: str) -> Dict[str, Dict[str, str]]:
    """恢复真实报告文本: {version: {case_id: report_text}}(后写的覆盖先写的)。"""
    p = os.path.join(_BASE, sid, "outputs.jsonl")
    if not os.path.exists(p):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    with open(p, encoding="utf-8") as f:
        for l in f:
            if not l.strip():
                continue
            rec = json.loads(l)
            out.setdefault(rec["version"], {})[rec["case_id"]] = rec["report_text"]
    return out


def load_judgments(sid: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """恢复真实报告的 LLM-judge 评分: {version: {case_id: {scores, reasoning, flagged}}}。"""
    p = os.path.join(_BASE, sid, "judgments.jsonl")
    if not os.path.exists(p):
        return {}
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    with open(p, encoding="utf-8") as f:
        for l in f:
            if not l.strip():
                continue
            rec = json.loads(l)
            out.setdefault(rec["version"], {})[rec["case_id"]] = {
                "scores": rec["scores"], "reasoning": rec.get("reasoning", {}),
                "flagged": rec.get("flagged", [])}
    return out


def _load_check_jsonl(sid: str, fname: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """通用: 恢复逐 check 记录 {version:{case_id:{checks, reasoning?}}}(后写覆盖先写)。"""
    p = os.path.join(_BASE, sid, fname)
    if not os.path.exists(p):
        return {}
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    with open(p, encoding="utf-8") as f:
        for l in f:
            if not l.strip():
                continue
            rec = json.loads(l)
            if rec.get("invalidated"):
                out.setdefault(rec["version"], {}).pop(
                    rec["case_id"],
                    None,
                )
                continue
            out.setdefault(rec["version"], {})[rec["case_id"]] = {
                "checks": rec.get("checks", {}),
                "reasoning": rec.get("reasoning", {}),
                "report_sha256": rec.get("report_sha256"),
                "rubric_sha256": rec.get("rubric_sha256"),
            }
    return out


def load_check_judgments(sid: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
    return _load_check_jsonl(sid, "check_judgments.jsonl")


def load_meta(sid: str) -> Optional[Dict[str, Any]]:
    p = os.path.join(_BASE, sid, "meta.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_generation_job(sid: str, job_id: str, payload: Dict[str, Any]):
    """原子保存一个前端真实生成任务。"""
    d = os.path.join(_ensure(sid), "generation_jobs")
    os.makedirs(d, exist_ok=True)
    _atomic_write(os.path.join(d, job_id + ".json"), payload)


def load_generation_job(sid: str, job_id: str) -> Optional[Dict[str, Any]]:
    p = os.path.join(_BASE, sid, "generation_jobs", job_id + ".json")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_generation_jobs(sid: str = None) -> List[Dict[str, Any]]:
    """读取一个或全部会话的生成任务，按创建时间排序。"""
    session_ids = [sid] if sid else list_session_ids()
    jobs = []
    for session_id in session_ids:
        d = os.path.join(_BASE, session_id, "generation_jobs")
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, name), encoding="utf-8") as f:
                    jobs.append(json.load(f))
            except (OSError, json.JSONDecodeError):
                continue
    return sorted(
        jobs,
        key=lambda item: float(item.get("created_at") or 0),
    )


def base_dir() -> str:
    return _BASE
