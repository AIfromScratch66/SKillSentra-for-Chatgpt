(function (root) {
  "use strict";

  const count = value => Number.isInteger(value) && value >= 0 ? value : null;
  const sumKnown = (runs, field) => runs.length && runs.every(run => count(run[field]) !== null)
    ? runs.reduce((sum, run) => sum + run[field], 0) : null;
  const tokenLabel = value => count(value) === null ? "未知" : value.toLocaleString();

  function resultFromOrchestration(orchestration) {
    const runs = orchestration?.runs || [];
    const required = ["creator", "evaluator", "safety"];
    if (orchestration?.status !== "completed" || required.some(role => !runs.some(run =>
      run.role === role && run.status === "completed" && ["openai", "openai-compatible"].includes(run.connection_provider)
      && typeof run.output?.summary === "string" && run.output.summary.trim()
    ))) throw new Error("真实 AI 协作尚未完整完成，请查看调用台账");
    const creator = runs.find(run => run.role === "creator");
    const attempts = orchestration.attempts?.length ? orchestration.attempts : runs;
    return {
      text: creator.output.summary,
      decision: "pending",
      requestId: orchestration.id,
      source: "api",
      provider: creator.connection_provider,
      model: creator.provider_model || creator.model,
      providerRequestId: creator.provider_request_id || "",
      timestamp: creator.created_at || "",
      durationMs: attempts.reduce((sum, run) => sum + (Number(run.duration_ms) || 0), 0),
      inputTokens: sumKnown(attempts, "input_tokens"),
      outputTokens: sumKnown(attempts, "output_tokens"),
      tokens: sumKnown(attempts, "total_tokens"),
      usageStatus: attempts.every(run => run.usage_status === "reported") ? "reported" : "unknown",
      cost: attempts.every(run => run.cost_status === "estimated_local_pricebook") ? orchestration.total_cost : null,
      currency: orchestration.currency || "",
      evaluator: runs.find(run => run.role === "evaluator").output.summary,
      safety: runs.find(run => run.role === "safety").output.summary,
      runs: attempts.map(run => ({ id: run.id, role: run.role, status: run.status,
        provider: run.connection_provider, model: run.provider_model || run.model,
        providerRequestId: run.provider_request_id || "", usageStatus: run.usage_status || "unknown",
        inputTokens: count(run.input_tokens), outputTokens: count(run.output_tokens),
        totalTokens: count(run.total_tokens), fallbackUsed: Boolean(run.fallback_used) }))
    };
  }

  function resultFromHost(request) {
    if (request?.status !== "proposed" || !request.output?.summary?.trim()) return null;
    const usage = request.usage || request;
    return {
      text: request.output.summary, decision: "pending", requestId: request.id,
      source: "host", provider: request.host_label || "ChatGPT / Codex", model: request.model || "未知",
      timestamp: request.updated_at || request.created_at || "",
      inputTokens: count(usage.input_tokens), outputTokens: count(usage.output_tokens),
      tokens: count(usage.total_tokens), usageStatus: request.usage_status || usage.status || "unknown",
      cost: null, runs: [], hostRequestId: request.id,
      stale: request.is_stale !== false || request.context_current !== true
    };
  }

  const api = { count, sumKnown, tokenLabel, resultFromOrchestration, resultFromHost };
  root.SkillSentraAITruth = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
