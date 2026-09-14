# 主 Agent 如何调用资料整理员

第 0 步默认先委派 `report-evidence-agent-v2`，由它整理结构化论据，主 Agent 读取结果后再确认写作输入。子代理使用同包的 `report-evidence-v2` Skill，不需要主 Agent 重述完整清洗规则或先自行解析全部素材。

## 何时调用

- 新报告：在第 0 步委派 `operation=prepare`，少量易读素材也调用，完成后再确认写作输入。
- 先在本项目素材目录查找 `structured_data.json`，不要只查当前工作区。已有文件作为 `previousPath` 一并传入，由资料整理员核验格式、来源与 unresolved；与当前资料一致时返回 `EVIDENCE_UNCHANGED` 并复用，不重新清洗或改写共享文件。不能仅凭文件存在跳过核验。
- 用户新增、替换、撤回材料，纠正数据或指出论据遗漏：已有表就 `operation=update`，没有则 `prepare`。单纯“摘要短些、改标题”不调用。事实更新不是 Memory Capture；同轮还包含写作偏好时，报告修订后只把偏好交给 Memory Agent。

## 调用与结果

传入 `caseId`、主题、准确的 `sourcePaths`、`outputPath` 和 `workDir`：

- `outputPath` 为已确定的 `素材目录/structured_data.json`，保存位置遵循 [统一目录约定](workspace-and-delivery.md)，不追加子目录。
- `workDir` 为本轮报告工作区的 `.report-agent/evidence/r001`，资料更新依次使用未占用的 `r002`。解析文件、更新前副本及本轮快照放在这里。
- 扫描原始素材时排除 `structured_data.json`、`报告/`、指定工作区和解析产物；共享论据只作复用输入，不是新的独立信源。

`caseId` 沿用已有论据表的 `case_id`，首次可用项目文件夹名；同项目后续保持不变。恢复会话时检查已有版本与当前来源，不重复初始化，也不把失败或部分完成的版本冒充完整结果。同一项目的资料更新串行执行，不让两个整理任务覆盖同一输出。

更新另传 `previousPath`（通常等于 `outputPath`）、变化的来源路径及 `changeRequest`。用户直接提供的数据更正，先把对应用户原文保存为 `workDir/user-update.md`，标明来自用户消息、尚未独立核验，并以绝对路径作为显式来源传入；不得把写作假设或主 Agent 的分析保存成“用户事实”。

收到结果后读取返回文件并核对基本格式、case_id、数量与缺口；将本次采用内容保存为 `workDir/structured_data.json` 固定快照（子代理已生成时核对复用，不覆盖不同内容）。后续写作和 Loop 只用该快照；同时记录共享文件及原始素材根目录的绝对路径，快照中的相对 `source_ref` 仍按原始素材根目录解析。完整理解论据后再提出 hypothesis，不只读子代理摘要。

- `EVIDENCE_COMPLETED` / `EVIDENCE_UNCHANGED`：使用返回的合法路径。
- `EVIDENCE_PARTIAL`：向用户说明实质缺口；不影响任务可继续，但不能声称全部素材已清洗。影响关键判断时先确认边界或补资料。
- 子代理不可用 / `EVIDENCE_FAILED`：如实说明，可回到原有主 Agent 解析素材流程；不修改插件、安装配置或伪造成功。不能拿旧版冒充涵盖本次新资料的论据表。

## 与写作、Loop 的衔接

初次清洗完成后，主 Agent 继续第 1 步确认和第 2 步亲自写 V0。Report Loop 输入包传入本轮论据快照的绝对路径及原始来源路径，不直接引用会被后续更新的共享文件；Judge 和 Rewriter 复用快照，不各自重新清洗。

用户更新资料后，先更新论据，再据此修改当前报告；除用户明确要求，不自动重跑完整 Loop。修改后的报告不能沿用旧得分作为新评分。

已有 Loop 正在执行时，不热替换其输入表，也不混合新资料与旧评分。收到资料更新先停止后续派发，保留当前状态和产物，再整理新资料。用户要求按新资料重新评测时使用新的报告运行目录和输入包，重新冻结标准、完整评测；旧 Judgment 不复用。

不为每次数据更新重建所有历史 Evidence，不自动清理旧版。内部论据默认不作为最终报告正文或额外交付文件；用户需要时提供对应 `structured_data.json`。
