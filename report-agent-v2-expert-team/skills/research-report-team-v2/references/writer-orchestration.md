# Writer 调用与续写

初稿、Loop 改写和用户反馈修订统一委派 `report-team-writer-v2`。主 Agent 确认需求和管理版本，不代写正文。

## 首次写作

需求确认后，按 [目录约定](workspace-and-delivery.md) 建立本轮 Loop 目录，保存用户确认原文到 `本轮需求.md`，按 [状态约定](state-and-scoring.md#1-单一运行状态) 初始化 drafting 状态，再调用 Writer。传入 `mode=draft`、用户原始请求、三项确认及用户消息原文、篇幅与素材边界、dataVersion/dataSha256，以及 `writing-instructions.md`、本轮需求、共享论据表、原始素材和 `候选报告/R0.md` 的绝对路径。Writer 必须先完整读取写作指令，再写 R0；不依赖主 Agent 的完整会话自动传入。

按 [团队调用契约](native-agent-contracts.md) 用 Agent 创建 Writer，记录工具返回的真实 agent_id 到现有 run-state.json 的 writerAgentId，并保存实际消息地址到 team.writerName；task_id 不得冒充 agent_id。等待 Writer 的 SendMessage，核验 REPORT_WRITE_COMPLETED、mode 和输出文件后才进入 Loop。工具没有返回的字段保留 null，不编造；后续按下述恢复方式处理。

## 后续修改

每次 Writer 成功落盘后，主 Agent 按 [统一篇幅统计](report-length.md) 运行计数脚本；后续修改传入所选基线的 `reportStats`，不要追加要求 Writer 人工逐字计数的内部循环。

同一篇报告通过 SendMessage 向 team.writerName 发送下一任务，续用原 Writer，而不是新起同名 Agent 或使用 resume；一次只派发一个写作任务，等待完成后再进入下一阶段。

- Loop：传 `mode=revise`、历史最佳版本及新候选路径、冻结 Plan、Revision Brief，并明确上一候选被采纳还是拒绝。候选即使刚由该 Writer 写出，被拒绝后也不能作为下一轮基线。
- 用户反馈：传 `mode=feedback`、当前交付报告基线、报告目录中未占用的 `<报告主题>-vN.md` 目标、新反馈原文及必要对话；新增资料先由 Evidence Agent 更新，再传共享论据表与新 dataVersion/dataSha256。写作前后核验数据绑定，核验成功后在报告目录的 `版本说明.md` 登记版本、依据数据、时间、用户要求和修改内容（未跑 Judge 则标未评测），再执行 Memory Capture。不复制无版本号终稿，不建反馈修订目录。旧 Loop 的数据绑定和分数不改写，旧评分不代表修订稿的新评分。

资料更新后按 [资料更新后的修订](evidence-orchestration.md#资料更新后如何继续) 默认走上述 feedback，不先询问是否启动 Loop；用户明确要求“只更新论据”或仅检查时不调用 Writer。同一报告明确要求“更新并重新评测”时，可续用原 Writer，但传 `mode=draft`、`baselineReportPath`（旧报告）、沿用及更新后的需求与新论据、全新的 R0 目标路径；它是在旧稿基础上生成新轮初稿，不按旧 Judge 修补，也不写回旧稿。将实际 Writer ID 保存到新运行状态，后续改写只依据本轮的新评分；与此前不同主题的新报告仍新建 Writer。原 Writer 不可恢复时按下文重建，传入新轮输入，不恢复旧 Loop 状态。

每轮追加新要求与反馈，不重复发送完整对话或全部历史 Judge；上下文过长可能被宿主压缩，因此基线、用户要求和修改记录仍以文件为准。不同报告不复用 writerAgentId。

初稿和 Loop 候选使用本轮 `候选报告/RN.md`，数据版本等记录在本轮运行状态；用户交付使用报告目录的 `<报告主题>-vN.md` 和版本说明，两套序号分别递增，规则见 [保存位置与交付](workspace-and-delivery.md)。用户反馈后的未评测稿不能冒充旧 Loop 的历史最佳；跨会话可从版本说明定位原 Loop 的 writerAgentId，无法恢复时重建 Writer，不为反馈新建 Loop。

## 恢复与失败

跨会话或旧实验继续时，先核对已有文件和任务状态。若当前团队没有可达的原 Writer，则用已确认输入、写作指令、当前正确基线、冻结 Plan（如有）、Revision Brief 和简短修改历史重建一次 Writer，并保存新的真实 ID 和消息地址。不要把普通写作失败、idle 或仍在运行当成恢复失败；先检查目标文件，避免重试覆盖已完成的产物。

已有 R0 不重写；未完成的 R0 若已落文件，先核验是否完整，无法确认时说明情况，不覆盖。新建或恢复仍失败时如实报告，保留现有报告，不由主 Agent 接管写作。Loop 内按 `rewrite_unavailable` 停止；初稿失败不启动 Judge。
