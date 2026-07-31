# 回归看板 — research_insight

## 分数曲线

| 版本 | 父版 | 打开的 directive | dev overall | test overall | 红线失败 |
|------|------|------------------|-------------|--------------|---------|
| v0 | - | - | 2.17 | 2.17 | 2 |
| v1 | v0 | flag_source_conflict | 2.31 | 2.31 | 1 |
| v2 | v1 | flag_source_conflict, honest_on_unsupportable | 2.40 | 2.40 | 0 |
| v3 | v2 | require_source_ref, flag_source_conflict, honest_on_unsupportable | 2.59 | 2.59 | 0 |
| v4 | v3 | require_source_ref, flag_source_conflict, honest_on_unsupportable, require_two_sources | 2.96 | 2.96 | 0 |
| v5 | v4 | require_source_ref, flag_source_conflict, honest_on_unsupportable, require_two_sources, summary_format | 3.11 | 3.11 | 0 |
| v6 | v5 | require_source_ref, flag_source_conflict, honest_on_unsupportable, require_two_sources, summary_format, pyramid_body | 3.26 | 3.26 | 0 |
| v7 | v6 | require_source_ref, flag_source_conflict, honest_on_unsupportable, require_two_sources, summary_format, pyramid_body, mece_sections | 3.49 | 3.49 | 0 |
| v8 | v7 | require_source_ref, flag_source_conflict, honest_on_unsupportable, require_two_sources, summary_format, pyramid_body, mece_sections, ensure_narrative_flow | 3.61 | 3.61 | 0 |
| v9 | v8 | require_source_ref, flag_source_conflict, honest_on_unsupportable, require_two_sources, summary_format, pyramid_body, mece_sections, concept_consistency, ensure_narrative_flow | 3.73 | 3.73 | 0 |
| v10 | v9 | require_source_ref, flag_source_conflict, honest_on_unsupportable, require_two_sources, summary_format, pyramid_body, mece_sections, concept_consistency, ensure_narrative_flow, require_insight_triplet | 4.03 | 4.03 | 0 |
| v11 | v10 | require_source_ref, flag_source_conflict, honest_on_unsupportable, require_two_sources, summary_format, pyramid_body, mece_sections, concept_consistency, ensure_narrative_flow, require_insight_triplet, abstract_cases | 4.18 | 4.18 | 0 |
| v12 | v11 | require_source_ref, flag_source_conflict, honest_on_unsupportable, require_two_sources, summary_format, pyramid_body, mece_sections, concept_consistency, ensure_narrative_flow, require_insight_triplet, abstract_cases, cover_key_claims | 4.26 | 4.26 | 0 |
| v13 | v12 | require_source_ref, flag_source_conflict, honest_on_unsupportable, require_two_sources, summary_format, pyramid_body, mece_sections, concept_consistency, ensure_narrative_flow, require_insight_triplet, abstract_cases, cover_key_claims, ban_bushi_ershi | 4.41 | 4.41 | 0 |
| v14 | v13 | require_source_ref, flag_source_conflict, honest_on_unsupportable, require_two_sources, summary_format, pyramid_body, mece_sections, concept_consistency, ensure_narrative_flow, require_insight_triplet, abstract_cases, cover_key_claims, ban_bushi_ershi, require_charts | 4.56 | 4.56 | 0 |
