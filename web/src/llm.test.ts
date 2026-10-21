import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { PROVIDERS, anthropicParams, anthropicText, claudeModel } from "./llm";

describe("claudeModel", () => {
  it("uses Haiku for the cheap tiers and Opus for the strong and web-search ones", () => {
    assert.equal(claudeModel("google/gemini-2.5-flash-lite", ""), "claude-haiku-5-5");
    assert.equal(claudeModel("google/gemini-2.5-flash", ""), "claude-haiku-5-5");
    assert.equal(claudeModel("google/gemini-2.5-pro", ""), "claude-opus-5-5");
    assert.equal(claudeModel("openai/gpt-4o-mini:online", ""), "claude-opus-5-5");
  });

  it("lets a model chosen in setup win", () => {
    assert.equal(claudeModel("google/gemini-2.5-pro", "claude-sonnet-5-5"), "claude-sonnet-5-5");
  });
});

describe("anthropicParams", () => {
  const messages = [
    { role: "system" as const, content: "You score jobs." },
    { role: "user" as const, content: "Score this." },
  ];

  it("moves system messages to the system field", () => {
    const p = anthropicParams("claude-haiku-5-5", messages, false, false);
    assert.equal(p.system, "You score jobs.");
    assert.deepEqual(p.messages, [{ role: "user", content: "Score this." }]);
    assert.equal(p.model, "claude-haiku-5-5");
    assert.equal(p.max_tokens, 16000);
  });

  it("asks for bare JSON in JSON mode", () => {
    const p = anthropicParams("claude-haiku-5-5", messages, true, false);
    assert.match(String(p.system), /only a single JSON object/);
  });

  it("adds web search only for web-search requests", () => {
    assert.equal(anthropicParams("claude-opus-5-5", messages, false, false).tools, undefined);
    const p = anthropicParams("claude-opus-5-5", messages, false, true);
    assert.deepEqual(p.tools, [{ type: "web_search_20260209", name: "web_search", max_uses: 5 }]);
  });

  it("turns on refusal fallbacks for Opus but not Haiku", () => {
    const opus = anthropicParams("claude-opus-5-5", messages, false, false);
    assert.equal(opus.fallbacks, "default");
    assert.deepEqual(opus.betas, ["server-side-fallback-2026-07-01"]);
    const haiku = anthropicParams("claude-haiku-5-5", messages, false, false);
    assert.equal(haiku.fallbacks, undefined);
    assert.equal(haiku.betas, undefined);
  });
});

describe("anthropicText", () => {
  it("joins text blocks and skips thinking and tool blocks", () => {
    const text = anthropicText([
      { type: "thinking", thinking: "", signature: "s" },
      { type: "text", text: "Hello" },
      { type: "server_tool_use", id: "t", name: "web_search", input: {} },
      { type: "text", text: " world" },
    ]);
    assert.equal(text, "Hello world");
  });
});

describe("PROVIDERS", () => {
  it("lists Claude", () => {
    assert.equal(PROVIDERS.anthropic.label, "Claude");
  });
});
