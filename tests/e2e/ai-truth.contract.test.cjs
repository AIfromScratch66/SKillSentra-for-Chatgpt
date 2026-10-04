const test = require("node:test");
const assert = require("node:assert/strict");
const truth = require("../../demo-apple/ai-truth.js");

function fixture(overrides = {}) {
  return { id: "synthetic-orchestration", status: "completed", currency: "USD", total_cost: 0.0001,
    runs: ["creator", "evaluator", "safety"].map(role => ({
      id: `fixture-${role}`, role, status: "completed", connection_provider: "openai", model: "fixture-model",
      provider_request_id: `synthetic-${role}`, output: { summary: `Synthetic ${role} output` },
      input_tokens: 12, output_tokens: 8, total_tokens: 20, usage_status: "reported",
      cost_status: "estimated_local_pricebook", ...overrides
    })) };
}

test("truth contract preserves exact provider counts across all three roles", () => {
  const result = truth.resultFromOrchestration(fixture());
  assert.equal(result.text, "Synthetic creator output");
  assert.deepEqual([result.inputTokens, result.outputTokens, result.tokens], [36, 24, 60]);
  assert.equal(result.source, "api");
  assert.equal(result.usageStatus, "reported");
});

test("missing usage is unknown, not zero or a text-length estimate", () => {
  const result = truth.resultFromOrchestration(fixture({ input_tokens: null, output_tokens: null, total_tokens: null, usage_status: "unknown", cost_status: "unknown" }));
  assert.equal(result.tokens, null);
  assert.equal(result.cost, null);
  assert.equal(truth.tokenLabel(result.tokens), "未知");
  assert.equal(truth.tokenLabel(0), "0");
  assert.equal(truth.count("0"), null);
});

test("mock, incomplete, failed and empty output cannot become real proposals", () => {
  for (const mutation of [{ connection_provider: "mock" }, { connection_provider: undefined }, { status: "failed" }, { output: { summary: "" } }]) {
    assert.throws(() => truth.resultFromOrchestration(fixture(mutation)));
  }
  const partial = fixture();
  partial.runs.pop();
  assert.throws(() => truth.resultFromOrchestration(partial));
});

test("failed retry attempts remain in accounting", () => {
  const orchestration = fixture();
  orchestration.attempts = [...orchestration.runs, { id: "failed-attempt", status: "failed", input_tokens: 5, output_tokens: 0, total_tokens: 5, usage_status: "reported" }];
  const result = truth.resultFromOrchestration(orchestration);
  assert.equal(result.tokens, 65);
  assert.equal(result.runs.length, 4);
});

test("host writeback has its own source and cannot claim provider-reported tokens", () => {
  const result = truth.resultFromHost({ id: "host-fixture", status: "proposed", output: { summary: "Synthetic host proposal" }, host_label: "Codex test fixture", input_tokens: 9, output_tokens: 4, total_tokens: 13, usage_status: "host_reported" });
  assert.equal(result.source, "host");
  assert.equal(result.usageStatus, "host_reported");
  assert.equal(result.tokens, 13);
  assert.equal(truth.resultFromHost({ status: "pending" }), null);
});

test("host unknown usage and stale context remain explicit", () => {
  const result = truth.resultFromHost({ id: "host-fixture", status: "proposed", is_stale: true, context_current: false, output: { summary: "Old proposal" }, usage_status: "unknown" });
  assert.equal(result.tokens, null);
  assert.equal(result.usageStatus, "unknown");
  assert.equal(result.stale, true);
});
