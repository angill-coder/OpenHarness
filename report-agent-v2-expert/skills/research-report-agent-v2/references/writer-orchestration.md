# Writer 调用与续写

初稿、Loop 改写和用户反馈修订统一委派 `report-writer-v2`。主 Agent 确认需求和管理版本，不代写正文。

## 首次写作

需求确认后，先按 [状态约定](state-and-scoring.md#1-单一运行状态) 初始化 drafting 状态，再调用 Writer，传入 `mode=draft`、用户原始请求、三项确认及用户消息原文、篇幅与素材边界，以及 `writing-instructions.md`、`.report-agent/inputs.md`、论据快照、原始素材和 `历史版本/v0-初稿.md` 的绝对路径。Writer 必须先完整读取写作指令，再写 V0；不依赖主 Agent 的完整会话自动传入。

等待 Writer 完成，读取工具返回的真实 `agentId`，保存为现有 `run-state.json` 中的 `writerAgentId`；后台调用的 `taskId` 仅用于等待，不得冒充 agentId。主 Agent 核验 `REPORT_WRITE_COMPLETED`、mode 和输出文件后才进入 Loop。没有可恢复 ID 就保留 null，不编造；后续按下述恢复方式处理。

## 后续修改

同一篇报告优先通过 Agent 工具的 `resume=writerAgentId` 续用原 Writer，而不是新起同名 Agent；一次只派发一个写作任务，等待完成后再进入下一阶段。

- Loop：传 `mode=revise`、历史最佳版本及新候选路径、冻结 Plan、Revision Brief，并明确上一候选被采纳还是拒绝。候选即使刚由该 Writer 写出，被拒绝后也不能作为下一轮基线。
- 用户反馈：传 `mode=feedback`、当前交付报告路径、新反馈原文及必要对话；新增资料先由 Evidence Agent 更新，再传新快照。等待修改成功后再执行 Memory Capture，不自动重跑 Loop，旧评分不代表修订稿的新评分。

每轮追加新要求与反馈，不重复发送完整对话或全部历史 Judge；上下文过长可能被宿主压缩，因此基线、用户要求和修改记录仍以文件为准。不同报告不复用 writerAgentId。

## 恢复与失败

跨会话或旧实验继续时，先核对已有文件和任务状态。若 writerAgentId 缺失，或宿主明确返回无法恢复该 ID，则用已确认输入、写作指令、当前正确基线、冻结 Plan（如有）、Revision Brief 和简短修改历史重建一次 Writer，并保存新的真实 ID。不要把普通写作失败或仍在运行当成恢复失败；先检查目标文件，避免重试覆盖已完成的产物。

已有 V0 不重写；未完成的 V0 若已落文件，先核验是否完整，无法确认时说明情况，不覆盖。新建或恢复仍失败时如实报告，保留现有报告，不由主 Agent 接管写作。Loop 内按 `rewrite_unavailable` 停止；初稿失败不启动 Judge。
