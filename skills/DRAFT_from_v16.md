# research-report skill 修订草案（对照 research-run 终版 v16）

> 本文是**评审+起草草案**，不改动 `SKILL.md` / `references/instructions.md` 母本。
> 结论先行：**v16 不构成"精简 directive"的依据；母本（全量安全集）保持，唯一该落地的是 L2 风格范例。** 理由见下。
> 数据源：`app/sessions/research-run/final_skill_v16.json`（导出自 state.json，current_idx=17）。

## 一、v16 是什么
- 血统：`v0(全关) → 逐版采纳 15 个质量 directive → v15 → v16（L1 耗尽后自动升 L2，注入 few-shot `style_exemplar`）`。
- = **L1 15 个质量 directive 全开** + **L2 一个 `style_exemplar` few-shot 槽** + 关 8 个。
- 结构（6 步 flow / 5 subagent / memory_schema）**全程未变** —— 符合"结构定上限、optimizer 只翻 directive"。
- 优化器达标即收敛；本轮判分用的是**未校准 judge**（app advance 不卡 0.85 门槛）。

## 二、逐 directive 对照母本 + 真实是否采纳

### L1 开启的 15 个 → 母本 v-full 已全含，✓ 保留
`honest_on_unsupportable, summary_format, pyramid_body, mece_sections, concept_consistency, ensure_narrative_flow, require_insight_triplet, abstract_cases, drop_noise, mark_extrapolation_confidence, crosscheck_outliers, cover_key_claims, ban_bushi_ershi, require_charts, match_exec_length`
—— 每条在 `instructions.md` 硬规则里都有对应条目（1/6/7/8/8a/9/10/11/12/12a/13/14/15…），无需改动。

### 关闭的 8 个 —— 逐一判定"真实该不该删"（**关键**）

| directive | 优化器为何"关" | 对应 rubric | 真实是否删 |
|---|---|---|---|
| `require_source_ref` | **提议过被拒**（"目标维度未涨"）——口径已改为"正文不印出处但可回溯"（§9④），信号对该维不再加分 | T1 | **不删**：语义已迁移，母本现状（可回溯不印号）正确 |
| `flag_source_conflict` | **从未被提议**：本轮 3 case 无 `source_conflict` tag，信号 inert | T3 冲突不混用 🔴 | **必留**：红线，数据未覆盖≠该删 |
| `verify_no_fabrication` | **从未被提议**：无 `fabrication_risk` tag，信号 inert | T2 不编造 🔴 | **必留**：最硬红线 |
| `require_two_sources` | 信号活跃却未被提议（达标收敛/该维已达标未轮到） | T4 单源降级 🔴 | **必留** |
| `note_metric_caveat` | 同上（case 有 metric_caveat tag，信号活跃，未轮到） | T（口径 5a） | **必留** |
| `disclose_sample_bias` | 同上（case3 有 selection_bias tag，活跃，未轮到） | T（样本 5b） | **必留** |
| `require_rigorous_wording` | **设计上不入 mock 评分**（真实-only），mock 永远打不开；其真实效果改由 **L2 `style_exemplar` 替代实现** | E5 表达 | **真实必留** + 落实 L2 范例 |
| `buzzword_emphasis` | FORBIDDEN reward-hack，gate 拒 | E4 风格禁令 🔴 | **该关**（母本禁令 14 正确） |

**一句话**：8 个"关"里——1 个语义迁移（source_ref）、1 个本就该关（buzzword）、1 个真实-only 靠 L2 补（rigorous_wording）、其余 5 个全是**可回溯性红线**，只因本轮数据缺 `source_conflict`/`fabrication_risk` 场景 + 达标即收敛而"没轮到"，**没有一个能解读为真实该删**。

## 三、起草结论（该怎么落地到母本）

1. **directive 层：母本保持不变**，不按 v16 削减。v16 证明了 15 个质量 directive 全开有效，但**无权删红线**（数据未覆盖 + judge 未校准）。
2. **唯一新增：落实 L2 `style_exemplar`。** v16 的收敛信号明确指向"表达/措辞需风格范例（指令级已无修法）"。做法：从高分真实报告截一段作"照此语气/精炼度写"的样例，加进 `instructions.md`。
   - 建议取材：`data/research_assistant/` 对应案的 `vF报告_正文.md` 中 **overall 最高**的（校准里 Surge 4.85 / 留存 4.63）——取其**核心摘要 + 1 条核心发现**，约 8–12 行，体现"结论先行、含归因、无注水、数据入表"。
   - 拟新增章节（草稿，待填正文）：
     ```markdown
     ## 风格范例（照此语气与精炼度写，勿照抄内容）
     <从高分报告截取的 摘要 + 1 条发现>
     ——注意：结论先行、归因内嵌、无"不是…而是"、关键数字入表、≤1.5 页量级。
     ```
3. **门槛动作（先决条件）**：要真正谈"精简 directive"，先
   - 给 `research-run` 或数据集补 `source_conflict` / `fabrication_risk` 场景 case（让那两个红线 directive 有信号可被评估）；
   - 把 `research-calib` 人工分填到校准 **≥0.85**（否则优化的是未校准 judge，删任何东西都不可信）。

## 四、交付物
- `app/sessions/research-run/final_skill_v16.json` —— 终版 v16 完整快照（可读）。
- 本文件 —— 对照评审 + 落地建议（不动母本）。
