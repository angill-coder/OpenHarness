# ⚠️ 纯 v16 直译版 instructions（仅供对比，**不要用于生产**）

> 这是按 `research-run` 收敛终版 **v16** 的 directive 开关**逐字直译**出来的写作规则：
> **只保留 15 个开启的 directive，删掉 7 个关闭的质量 directive。**
> **危险：删掉的 7 个里有 5 个是可回溯性红线**（不编造 T2🔴 / 冲突不混用 T3🔴 / 单源降级 T4🔴 / 口径注明 / 样本偏差）。
> 用这版生成的报告会在 rubric 里因 traceability `hard_floor=3` 一票否决被封顶——**做对比看差异用，别装到 `~/.claude/skills`。**
> 生产用的安全母本仍是 `references/instructions.md`（全量）；落地建议见 `DRAFT_from_v16.md`。

---

## 相对安全母本，本版删掉了什么（= v16 关闭的 7 个质量 directive）

- ❌ `require_source_ref`（论断挂出处/自检回溯）
- ❌ `flag_source_conflict`（指出素材冲突不混用）— **红线 T3**
- ❌ `verify_no_fabrication`（不编造、独立核验）— **红线 T2**
- ❌ `require_two_sources`（孤证降级"待验证"）— **红线 T4**
- ❌ `note_metric_caveat`（口径注明/敏感性）
- ❌ `disclose_sample_bias`（披露样本偏差）
- ❌ `require_rigorous_wording`（措辞严谨不含糊）

> 讽刺点：base prose 仍写"可回溯性是生命线，素材不足处诚实留白"，但支撑它的规则被 directive 开关掏空——这恰好暴露"照搬未覆盖场景/未校准的 mock 收敛结果"的问题。

---

## 三项开场输入怎么用（结构层，与 directive 无关，保留）
1. **汇报背景** → 决定受众口吻、摘要聚焦、建议的决策指向。
2. **材料假设 hypothesis** → 作分析主线，给出支持/反驳/部分成立；⚠️ 只验证/证伪、不迎合。
3. **重点素材** → 核心结论优先建立在用户圈定的高质量材料上。

## 报告结构（三段，保留）
1. **核心摘要**：≤3 条 bullet，每条直接写结论，重要在前。
2. **核心发现**：关键事实/数据，把"为什么"（归因）融进每条发现，不单列归因章节。
3. **对我们的启示与建议**：可执行策略；趋势判断融进相关建议、标置信度不外推；不单列趋势章节、不放素材清单。
- 正文不印来源号；不把"结论先行/归因/金字塔"等写作原则字样当小标题写进正文。

## 硬规则（**仅 15 个开启的 directive**，逐条遵守）

**可回溯性（已被削到只剩 1 条 ⚠️）**
1. 素材根本答不了的问题，明确写"证据不足/暂无法判断"，不硬给结论。〔honest_on_unsupportable〕

**结构**
2. 摘要 ≤3 条 bullet、结论先行、按重要性排序。〔summary_format〕
3. 正文遵金字塔（每章先论点、再支撑）。〔pyramid_body〕
4. 章节 MECE（不重复/不遗漏/不交叉），必需段落齐。〔mece_sections〕

**逻辑**
5. 同一名词/口径/术语全文含义一致（概念不漂移）。〔concept_consistency〕
6. 一条清晰主线贯穿、章节因果/递进衔接，不并列堆砌。〔ensure_narrative_flow〕

**提炼与洞察**
7. 给出归因 + 趋势(标置信度) + 可执行建议三要素。〔require_insight_triplet〕
8. 案例抽象提炼成模式/共性，不逐条罗列。〔abstract_cases〕
9. 不引用噪音/无关片段充数。〔drop_noise〕
10. 趋势判断标置信度、不过度外推未来。〔mark_extrapolation_confidence〕
11. 一次性异常/离群作交叉或情境解释，不当趋势或据此错误归因。〔crosscheck_outliers〕

**覆盖度**
12. 覆盖素材支持的所有关键问题与关键 claim（答不了的诚实留白）。〔cover_key_claims〕

**表达与受众契合**
13. 禁用"不是…而是…"句式；禁术语堆砌注水。〔ban_bushi_ershi〕
14. 关键数据/对比用 markdown 表格或图呈现。〔require_charts〕
15. 面向高管精炼，长度 ≤1.5 页量级。〔match_exec_length〕

## L2 风格范例槽（v16 已注入，占位待填）
```
[style_exemplar 槽——v16 标记此处需注入一段高分报告的风格范例，实际内容需人工填]
```

---

## 直译版 vs 安全母本 · 结论
- 本版 = mock 收敛的**字面还原**，用于让你直观看到"优化器只爬到哪"。
- **不建议采用**：删掉的 5 条红线会让真实报告在 traceability 维被一票否决；缺失场景（conflict/fabrication）和未校准 judge 决定了这些"删"不可信。
- 正确落地路径见 `DRAFT_from_v16.md`：directive 层保母本、只落实 L2 范例，先补场景 case + 跑校准 ≥0.85 再谈精简。
