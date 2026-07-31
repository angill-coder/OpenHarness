# -*- coding: utf-8 -*-
"""临时验证脚本: 多数据集分组 + 版本绑定 + 追加 + 快照/恢复兼容。用完即删。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import session as session_mod
import persistence as persist


def _mk_rows(ids, split="dev"):
    return {
        "schema": "openharness-wb/v1",
        "cases": [
            {
                "case_id": cid,
                "split": split,
                "input": {"prompt": "x"},
                "ground_truth": {},
                "input_files": [],
                "interactions": [{"input": "hi"}],
            }
            for cid in ids
        ],
    }


def main():
    sid = "verify-ds-tmp"
    # 清掉可能残留
    try:
        import shutil
        d = os.path.join(HERE, "sessions", sid)
        if os.path.isdir(d):
            shutil.rmtree(d)
    except Exception:
        pass

    s = session_mod.Session(sid, "帮我做调研洞察汇报报告", "research_insight")
    print("[v0] versions:", [v["version"] for v in s.versions])

    # 1) replace 导入数据集A (3 个 case)
    s.import_data(_mk_rows(["A1", "A2", "A3"]), mode="replace", name="数据集A")
    dsA = s.active_dataset_id
    print("[replace A] active=", dsA, "datasets=", list(s.datasets))
    print("  v0.dataset_id=", s.versions[0]["dataset_id"])
    print("  n_cases(all)=", len(s.cases), "cases_for(v0)=", sorted(c['case_id'] for c in s._cases_for(s.versions[0])))
    assert s.versions[0]["dataset_id"] == dsA
    assert len(s.cases) == 3

    # 2) append 数据集B (2 个新 case)
    s.import_data(_mk_rows(["B1", "B2"]), mode="append", name="数据集B")
    dsB = s.active_dataset_id
    print("[append B] active=", dsB, "datasets=", list(s.datasets))
    print("  全集 n_cases=", len(s.cases), "(应=5, 追加不清空)")
    print("  v0.dataset_id=", s.versions[0]["dataset_id"], "(应仍= A, 历史保留)")
    print("  cases_for(v0)=", sorted(c['case_id'] for c in s._cases_for(s.versions[0])))
    assert len(s.cases) == 5, "append 应并入全集"
    assert s.versions[0]["dataset_id"] == dsA, "append 不应改旧版本绑定"
    assert set(c["case_id"] for c in s._cases_for(s.versions[0])) == {"A1", "A2", "A3"}

    # 3) 模拟新版本继承 active(=B): 直接调用 _add_version 校验绑定继承
    from schemas import SkillArtifact
    v_next = SkillArtifact.from_dict({**s.versions[0]["skill"].to_dict(), "version": "v1", "parent_version": "v0"})
    s._add_version(v_next, adopted=True, proposal=None)
    print("[v1 继承 active] v1.dataset_id=", s.versions[-1]["dataset_id"], "(应= B)")
    assert s.versions[-1]["dataset_id"] == dsB, "新版本应继承 active 数据集B"
    assert set(c["case_id"] for c in s._cases_for(s.versions[-1])) == {"B1", "B2"}

    # 4) 快照 + 恢复 roundtrip
    snap = s.to_snapshot()
    assert "datasets" in snap and "active_dataset_id" in snap
    assert all("dataset_id" in v for v in snap["versions"])
    s2 = session_mod.Session.restore(snap)
    print("[restore] datasets=", list(s2.datasets), "active=", s2.active_dataset_id)
    assert s2.versions[0]["dataset_id"] == dsA
    assert s2.versions[-1]["dataset_id"] == dsB
    assert set(c["case_id"] for c in s2._cases_for(s2.versions[0])) == {"A1", "A2", "A3"}
    assert set(c["case_id"] for c in s2._cases_for(s2.versions[-1])) == {"B1", "B2"}

    # 5) 向后兼容: 模拟旧快照(无 datasets / 版本无 dataset_id)
    legacy = s.to_snapshot()
    legacy.pop("datasets", None)
    legacy.pop("active_dataset_id", None)
    for v in legacy["versions"]:
        v.pop("dataset_id", None)
    s3 = session_mod.Session.restore(legacy)
    print("[legacy restore] datasets=", list(s3.datasets), "active=", s3.active_dataset_id)
    assert s3.active_dataset_id == "default"
    assert all(v["dataset_id"] == "default" for v in s3.versions)
    # default 分组含全部 5 个 case
    assert set(c["case_id"] for c in s3._cases_for(s3.versions[0])) == {"A1", "A2", "A3", "B1", "B2"}

    print("\nALL DATASET CHECKS PASSED")


if __name__ == "__main__":
    main()
