# -*- coding: utf-8 -*-
"""
store.py — 版本化 Skill Artifact Store (对应架构文档 SKILL ARTIFACT STORE)

保存每一版 skill + 其血缘 + dev/test 分数, 供 dashboard 画曲线、供回溯。
纯内存 + 可选落盘 JSON。
"""
import json
import os
from typing import Any, Dict, List


class ArtifactStore:
    def __init__(self):
        self.versions: List[Dict[str, Any]] = []   # 每项: {skill, dev, test, adopted, proposal}

    def add(self, skill, dev_scores, test_scores=None, adopted=True, proposal=None):
        self.versions.append({
            "version": skill.version,
            "parent": skill.parent_version,
            "changelog": skill.changelog,
            "directives_on": [k for k, v in skill.directives().items() if v],
            "dev": dev_scores,
            "test": test_scores,
            "adopted": adopted,
            "proposal": proposal,
            "skill": skill.to_dict(),
        })

    def latest_adopted(self):
        for v in reversed(self.versions):
            if v["adopted"]:
                return v
        return None

    def adopted_history(self):
        return [v for v in self.versions if v["adopted"]]

    def dump(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.versions, f, ensure_ascii=False, indent=2)
