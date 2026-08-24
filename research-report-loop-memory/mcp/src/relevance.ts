export interface RelevanceDecision {
  relevant: boolean;
  reason: string;
  writingText: string;
}

const WRITING_CONTENT = /证据|数据|引用|来源|素材|事实|可靠性|可回溯|编造|外推|口径|信源|访谈|结构|框架|章节|段落|摘要|结论先行|层次|页数|[一二三四五六七八九十0-9]+页|篇幅|字数|标题|排版|表格|图表|分点|bullet|阅读负担|故事线|主线|逻辑|论证|因果|归因|递进|衔接|观点|洞察|判断|结论|建议|启示|行动|决策|价值|覆盖|完整|遗漏|全面|风险|机会|语气|口吻|措辞|句式|文风|表达|简洁|严谨|直接|专业|正式|口语|啰嗦|冗余|术语|中间状态语言/iu;
const REPORT_CONTEXT = /报告|汇报|文章|文稿|摘要|正文|写作|写报告|撰写|改稿|稿件|材料|storyline|ppt|docx/iu;
const FEEDBACK_SIGNAL = /喜欢|偏好|希望|请|应该|应当|应|需要|需|必须|须|不能|不得|要|不要|避免|改成|太.{0,8}|不够|以后|下次|始终|统一|保留|采用|控制在|保持/iu;
const EXPLICIT_NON_WRITING = /吃|喝|米饭|咖啡|运动|旅游|电影|音乐|宠物|住在|生日|星座|编程语言|代码风格|bug|函数|接口|数据库代码/iu;
const BARE_OPERATION = /^(?:修改|修改吧|改一下|改吧|删掉|删除|重写|继续|可以|好的|确认|执行)[吧啊呀嘛么。！!？?\s]*$/iu;

/**
 * Conservative domain gate. It only decides whether an event belongs in the
 * report-writing memory pipeline. Scope and promotion are decided by the
 * Memory Agent from the complete writing episode.
 */
export function classifyWritingFeedback(text: string): RelevanceDecision {
  const normalized = text.trim();
  if (!normalized) return { relevant: false, reason: "empty_feedback", writingText: "" };
  if (BARE_OPERATION.test(normalized)) return { relevant: false, reason: "bare_operation", writingText: "" };

  const hasWritingContent = WRITING_CONTENT.test(normalized);
  const hasWritingContext = REPORT_CONTEXT.test(normalized);
  const hasFeedbackSignal = FEEDBACK_SIGNAL.test(normalized);
  if (!hasWritingContent && !hasWritingContext) return { relevant: false, reason: "no_writing_signal", writingText: "" };
  if (!hasFeedbackSignal) return { relevant: false, reason: "not_explicit_feedback", writingText: "" };
  if (EXPLICIT_NON_WRITING.test(normalized) && !hasWritingContext) return { relevant: false, reason: "non_writing_feedback", writingText: "" };

  const clauses = normalized.split(/(?<=[，。；;！？!?])/u).map((clause) => clause.trim()).filter(Boolean);
  const writingClauses = clauses.filter((clause) => REPORT_CONTEXT.test(clause) || WRITING_CONTENT.test(clause));
  return {
    relevant: true,
    reason: "writing_feedback",
    writingText: (writingClauses.length > 0 ? writingClauses : [normalized]).join(""),
  };
}
