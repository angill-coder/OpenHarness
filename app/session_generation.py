# -*- coding: utf-8 -*-
"""真实报告批量导入能力（SessionGeneration mixin）。"""

from __future__ import annotations

import hashlib
from typing import Any, Dict

import persistence as persist
import iteration_trace


class SessionGeneration:
    """把同一 generation 的有效报告幂等地批量导入 Session。"""

    def import_generated_outputs(
        self,
        outputs: Dict[str, str],
        version: str,
        generation_id: str,
        account=None,
        traces: Dict[str, Dict[str, Any]] = None,
    ):
        if not generation_id:
            return {"error": "缺少 generation_id"}
        version_entry = next(
            (
                item
                for item in self.versions
                if item.get("version") == version
            ),
            None,
        )
        if version_entry is None:
            return {"error": "Skill 版本不存在: %s" % version}

        eligible_case_ids = self._case_ids_for(version_entry)
        out_of_scope = sorted(set(outputs or {}) - eligible_case_ids)
        if out_of_scope:
            return {
                "error": "报告包含当前 Skill 版本绑定数据集之外的 case: "
                + ", ".join(out_of_scope)
            }

        imported_for_job = self.generation_imports.setdefault(
            generation_id,
            {},
        )
        clean = {}
        skipped = []
        for case_id, report_text in (outputs or {}).items():
            text = (report_text or "").strip()
            if not text:
                continue
            digest = hashlib.sha256(
                text.encode("utf-8")
            ).hexdigest()
            if imported_for_job.get(case_id) == digest:
                skipped.append(case_id)
                continue
            clean[case_id] = text
            imported_for_job[case_id] = digest

        if clean:
            changed_judged = [
                case_id
                for case_id, text in clean.items()
                if (
                    self.report_outputs.get(version, {}).get(case_id)
                    not in (None, text)
                )
            ]
            if changed_judged:
                self._invalidate_judge_checks(
                    version,
                    changed_judged,
                    "generated_report_changed",
                )
            self.report_outputs.setdefault(version, {}).update(clean)
            persist.append_outputs_batch(
                self.id,
                version,
                clean,
                generation_id=generation_id,
                traces=traces,
            )
            dialogue_trace = iteration_trace.record_dialogue_contract(
                self,
                version,
                clean,
                traces=traces,
                source="generation",
            )
            persist.append_event(
                self.id,
                "generation_import",
                {
                    "generation_id": generation_id,
                    "version": version,
                    "case_ids": sorted(clean),
                    "n_cases": len(clean),
                    "skipped_case_ids": skipped,
                    "iteration_id": dialogue_trace.get("iteration_id"),
                },
            )
            # 批量只重评和保存一次，避免 N 个 case 导入造成 N 次 Mock 跑分。
            result = self.evaluate(account)
            self._save()
        else:
            result = self.view(account)

        result["generation_import"] = {
            "generation_id": generation_id,
            "version": version,
            "imported_case_ids": sorted(clean),
            "skipped_case_ids": sorted(skipped),
        }
        return result
