import assert from "node:assert/strict";
import test from "node:test";
import { classifyWritingFeedback } from "../src/relevance.ts";

test("accepts explicit reusable writing feedback without assigning a dimension", () => {
  const decision = classifyWritingFeedback("以后报告摘要再短一点，给高管看要结论先行");
  assert.equal(decision.relevant, true);
  assert.match(decision.writingText, /报告摘要/u);
});

test("accepts a contextual one-off requirement for L0 consideration", () => {
  const decision = classifyWritingFeedback("这份报告控制在三页");
  assert.equal(decision.relevant, true);
});

test("rejects unrelated personal preference, facts and bare operations", () => {
  for (const text of ["我喜欢吃米饭", "这个项目的数据来自访谈", "修改吧", "删掉", "把这个 bug 修改一下", "重写"]) {
    assert.equal(classifyWritingFeedback(text).relevant, false, text);
  }
});

test("strips unrelated clauses from mixed feedback", () => {
  const decision = classifyWritingFeedback("我喜欢吃米饭，但是以后报告摘要要短一点");
  assert.equal(decision.relevant, true);
  assert.doesNotMatch(decision.writingText, /米饭/u);
  assert.match(decision.writingText, /报告摘要/u);
});

test("accepts evidence-use corrections as writing feedback", () => {
  const decision = classifyWritingFeedback("下次写报告不要把单一访谈写成确定性结论");
  assert.equal(decision.relevant, true);
  assert.match(decision.writingText, /单一访谈/u);
});

test("accepts explicit Chinese requirement forms without requiring 应该 or 不要", () => {
  for (const text of [
    "可靠性说明应放到附录",
    "正文不能出现面向作者的解释",
    "报告须保持措辞严谨",
    "建议中不得放数据口径说明",
  ]) {
    assert.equal(classifyWritingFeedback(text).relevant, true, text);
  }
});

test("keeps supporting clauses available to callers after relevance classification", () => {
  const decision = classifyWritingFeedback(
    "不要大段论述：长段落会给管理层造成阅读负担，一页内容过多时应分点（bullet）呈现，更阅读友好。",
  );
  assert.equal(decision.relevant, true);
  assert.match(decision.writingText, /一页内容过多时应分点/u);
});
