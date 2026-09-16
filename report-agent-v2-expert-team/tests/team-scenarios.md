# 桌面端验收（待执行，Mac / Windows 分别记录）

使用独立测试项目和明确的测试 memoryRoot，不污染已有写作记忆。不为测试要求真实用户改造全局配置。

1. **完整新报告**：主理人 TeamCreate，Evidence 完成后确认输入，Writer 写 R0。空记忆走 Base-only，全部维度返回后聚合，必要时消息续用 Writer，最终只交付最佳 vN 与简短分数摘要。
2. **8 维计划**：由 Resolution 返回含两个 Memory 维度的合法 Plan。R0 创建 8 个独立实例，最多 6 个同时评测；R1 通过 SendMessage 续用原 8 个成员，无重复创建。逐轮读取新报告、检查全部 Check，结果乱序仍准确对应；旧轮结果不得补算新轮，仅成员不可用时重建并补充历史。
3. **连续 Writer**：R1 被拒绝后仍向原 Writer 发 R2，以历史最佳 R0 为基线；不从被拒绝的 R1 继续，不新建 Writer。用户反馈直改后才 Capture，不自动新建 Loop。
4. **来源核验**：Resolution 请求一次 L1，主理人经 Memory inspect_sources 获取，回到原 Resolution 完成冻结；第二次请求走原有失败回退。
5. **恢复 / 迟到消息**：恢复 judging 状态时不重复活跃分配，只补缺失维度。旧 RN、旧 assignmentId 的消息和 idle 不覆盖本轮结果；新会话不可达 Writer 才重建。
6. **失败 / 取消**：模型不可用、成员创建失败、重复实例不支持、Check 不完整、用户取消、数据指纹变化均如实终止或按原重试上限处理；不虚构结果，不无限等待，不由主理人代写。
7. **工具与数据**：仅 Lead/Evidence 执行沿用的只读计数/素材脚本；Judge 不写报告、Writer 不写记忆；真实记录请求与可验证的模型信息。
8. **回传消息遗漏**：两个 Judge 在主理人继续派发时已保存 resultPath，但通知被漏看；主理人准备等待时按当前分配核对文件，收齐全部有效结果后直接聚合并续用 Writer。旧 RN/旧 attempt 文件不得补算本轮；写入失败不得报告 JUDGE_RESULT_SAVED。

9. **记忆来源**：用户仅说“用 R2 作为最佳版本”，即使 Agent 附带充分的改写理由，也不触发 Capture；误派给 Memory 时返回 unchanged，全部记忆文件与 revision 不变。用户明确说“以后摘要只留核心结论”可 Capture；“本次摘要压缩到两行”可保留本次语境，但不能自动变成长期标准。Reflection 不将 Agent 推断当作新的用户证据。

这些是待验证行为，不是自动化测试已经通过的结论。重点核验 host 实际返回的 Agent 身份、SendMessage 收发与模型，而不只看展示名称。
