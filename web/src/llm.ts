import Anthropic from "@anthropic-ai/sdk";
import type { Env } from "./types";
import { createServiceSupabase } from "./auth";

/**
 * LLM access for jobkit. The provider, model and API key are chosen in the setup wizard and stored in
 * `jobkit_settings` (service-role only; the key is AES-256-GCM encrypted with SETTINGS_ENC_KEY, "enc:v1:" prefix).
 * Fallbacks, in order: LLM_KEY_SOURCE (a key another app stores) and OPENROUTER_API_KEY in the Worker env.
 */

export type ChatMessage = { role: "system" | "user" | "assistant"; content: string };

export type ChatResult = {
  content: string;
  model: string;
  promptTokens: number;
  completionTokens: number;
  costUsd: number;
};

export type LlmProvider = "openrouter" | "openai" | "anthropic" | "custom";

export const PROVIDERS: Record<LlmProvider, { label: string; baseUrl: string; defaultModel: string }> = {
  openrouter: { label: "OpenRouter", baseUrl: "https://openrouter.ai/api/v1", defaultModel: "google/gemini-2.5-flash" },
  openai: { label: "OpenAI", baseUrl: "https://api.openai.com/v1", defaultModel: "gpt-4o-mini" },
  // Claude through the Anthropic API (an API key, not a Claude subscription). Models come from claudeModel().
  anthropic: { label: "Claude", baseUrl: "https://api.anthropic.com", defaultModel: "" },
  custom: { label: "OpenAI-compatible", baseUrl: "", defaultModel: "" },
};

export type LlmSettings = { provider: LlmProvider; baseUrl: string; model: string; apiKey: string };

/** Rough $/1M token estimates for cost logging (scan budget). */
const MODEL_RATES: Record<string, { in: number; out: number }> = {
  "google/gemini-2.5-flash-lite": { in: 0.1, out: 0.4 },
  "google/gemini-2.5-flash": { in: 0.3, out: 2.5 },
  "openai/gpt-4o-mini": { in: 0.15, out: 0.6 },
  "openai/gpt-4o-mini:online": { in: 0.15, out: 0.6 },
  "gpt-4o-mini": { in: 0.15, out: 0.6 },
  "claude-haiku-5-5": { in: 0.1, out: 0.5 },
  "claude-sonnet-5-5": { in: 2, out: 10 },
  "claude-opus-5-5": { in: 4, out: 20 },
};

function estimateCost(model: string, prompt: number, completion: number): number {
  const rates = MODEL_RATES[model] ?? { in: 0.2, out: 0.8 };
  return (prompt * rates.in + completion * rates.out) / 1_000_000;
}

function b64ToBytes(b64: string): Uint8Array {
  const std = b64.replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(std.length % 4 ? std + "=".repeat(4 - (std.length % 4)) : std);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function bytesToB64url(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function hexToBytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

const PREFIX = "enc:v1:";

async function aesKey(env: Env, usage: "encrypt" | "decrypt"): Promise<CryptoKey> {
  const raw = (env.SETTINGS_ENC_KEY ?? "").trim();
  if (!raw) throw new Error("SETTINGS_ENC_KEY is not set");
  const keyBytes = /^[0-9a-f]{64}$/i.test(raw) ? hexToBytes(raw) : b64ToBytes(raw);
  if (keyBytes.length < 32) throw new Error("SETTINGS_ENC_KEY must be 32 bytes (base64) or 64 hex characters");
  return crypto.subtle.importKey("raw", keyBytes.slice(0, 32) as unknown as ArrayBuffer, { name: "AES-GCM" }, false, [usage]);
}

export async function encryptSecret(env: Env, plain: string): Promise<string> {
  const key = await aesKey(env, "encrypt");
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(
    await crypto.subtle.encrypt({ name: "AES-GCM", iv: iv as unknown as ArrayBuffer }, key, new TextEncoder().encode(plain)),
  );
  const packed = new Uint8Array(iv.length + ct.length);
  packed.set(iv);
  packed.set(ct, iv.length);
  return PREFIX + bytesToB64url(packed);
}

export async function decryptSecret(env: Env, stored: string): Promise<string> {
  if (!stored.startsWith(PREFIX)) return stored.trim();
  const key = await aesKey(env, "decrypt");
  const packed = b64ToBytes(stored.slice(PREFIX.length));
  const plain = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: packed.slice(0, 12) as unknown as ArrayBuffer },
    key,
    packed.slice(12) as unknown as ArrayBuffer,
  );
  return new TextDecoder().decode(plain).trim();
}

export async function loadLlmSettings(env: Env, userId: string): Promise<LlmSettings | null> {
  if (env.SUPABASE_SERVICE_ROLE_KEY) {
    const admin = createServiceSupabase(env);
    const { data } = await admin
      .from("jobkit_settings")
      .select("llm_provider, llm_base_url, llm_model, llm_key_enc")
      .eq("user_id", userId)
      .maybeSingle();
    if (data?.llm_key_enc) {
      try {
        const provider = (data.llm_provider as LlmProvider) in PROVIDERS ? (data.llm_provider as LlmProvider) : "openrouter";
        return {
          provider,
          baseUrl: String(data.llm_base_url || PROVIDERS[provider].baseUrl),
          model: String(data.llm_model || ""),
          apiKey: await decryptSecret(env, String(data.llm_key_enc)),
        };
      } catch {
        /* fall back to env */
      }
    }
  }
  const external = await loadExternalKey(env, userId);
  if (external) return { provider: "openrouter", baseUrl: PROVIDERS.openrouter.baseUrl, model: "", apiKey: external };
  const envKey = env.OPENROUTER_API_KEY?.trim();
  return envKey ? { provider: "openrouter", baseUrl: PROVIDERS.openrouter.baseUrl, model: "", apiKey: envKey } : null;
}

/** LLM_KEY_SOURCE="table.column": an OpenRouter key another app stores per user (encrypted with the same SETTINGS_ENC_KEY). */
async function loadExternalKey(env: Env, userId: string): Promise<string | null> {
  const m = (env.LLM_KEY_SOURCE ?? "").trim().match(/^([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)$/i);
  if (!m || !env.SUPABASE_SERVICE_ROLE_KEY) return null;
  const [, table, column] = m;
  try {
    const { data } = await createServiceSupabase(env).from(table).select(column).eq("user_id", userId).maybeSingle();
    const stored = data ? String((data as unknown as Record<string, unknown>)[column] ?? "") : "";
    return stored ? (await decryptSecret(env, stored)) || null : null;
  } catch {
    return null;
  }
}

/** Cheap call that proves a key works: lists models (no tokens spent). */
export async function testLlmKey(s: LlmSettings): Promise<{ ok: boolean; detail: string }> {
  if (s.provider === "anthropic") {
    try {
      await new Anthropic({ apiKey: s.apiKey }).models.list({ limit: 1 });
      return { ok: true, detail: "Anthropic key accepted" };
    } catch (error) {
      if (error instanceof Anthropic.APIError) return { ok: false, detail: `anthropic answered HTTP ${error.status}` };
      throw error;
    }
  }
  const base = s.baseUrl.replace(/\/+$/, "");
  const url = s.provider === "openrouter" ? `${base}/key` : `${base}/models`;
  const res = await fetch(url, { headers: { Authorization: `Bearer ${s.apiKey}` } });
  if (!res.ok) return { ok: false, detail: `${s.provider} answered HTTP ${res.status}` };
  return { ok: true, detail: s.provider === "openrouter" ? "OpenRouter key accepted" : "Key accepted" };
}

function modelFor(s: LlmSettings, requested: string): string {
  if (s.provider === "anthropic") return claudeModel(requested, s.model);
  if (s.model) return s.model;
  if (s.provider === "openrouter") return requested;
  return PROVIDERS[s.provider].defaultModel || requested.replace(/:online$/, "").split("/").pop() || requested;
}

/**
 * Callers ask for OpenRouter-style models by tier: "flash"/"flash-lite" for cheap work (scoring, checks),
 * "pro" for CVs and application answers, and ":online" for web-backed answers (salary). On Claude the cheap
 * tier runs on Haiku and the rest on Opus. A model saved in setup overrides this.
 */
export function claudeModel(requested: string, override: string): string {
  if (override) return override;
  return /-pro\b|:online$/.test(requested) ? "claude-opus-5-5" : "claude-haiku-5-5";
}

const JSON_ONLY = "Reply with only a single JSON object: no prose and no code fences.";
// Refusal fallbacks re-run a declined request on another model in the same call. Haiku has none.
const FALLBACK_MODELS = new Set(["claude-opus-5-5", "claude-sonnet-5-5"]);

export function anthropicParams(
  model: string,
  messages: ChatMessage[],
  jsonMode: boolean,
  online: boolean,
): Anthropic.Beta.Messages.MessageCreateParamsNonStreaming {
  const system = [...messages.filter((m) => m.role === "system").map((m) => m.content), ...(jsonMode ? [JSON_ONLY] : [])];
  return {
    model,
    max_tokens: 16000,
    ...(system.length ? { system: system.join("\n\n") } : {}),
    messages: messages
      .filter((m) => m.role !== "system")
      .map((m) => ({ role: m.role as "user" | "assistant", content: m.content })),
    ...(online ? { tools: [{ type: "web_search_20260209" as const, name: "web_search" as const, max_uses: 5 }] } : {}),
    ...(FALLBACK_MODELS.has(model) ? { betas: ["server-side-fallback-2026-07-01"], fallbacks: "default" as const } : {}),
  };
}

export function anthropicText(content: readonly { type: string; text?: string }[]): string {
  return content
    .filter((b) => b.type === "text")
    .map((b) => b.text ?? "")
    .join("")
    .trim();
}

async function claudeMessages(
  s: LlmSettings,
  model: string,
  messages: ChatMessage[],
  jsonMode: boolean,
  online: boolean,
): Promise<ChatResult> {
  const client = new Anthropic({ apiKey: s.apiKey });
  const params = anthropicParams(model, messages, jsonMode, online);
  let response = await client.beta.messages.create(params);
  let promptTokens = response.usage.input_tokens;
  let completionTokens = response.usage.output_tokens;
  // Web search can pause a long turn; sending the turn back lets it finish.
  for (let i = 0; i < 3 && response.stop_reason === "pause_turn"; i++) {
    response = await client.beta.messages.create({
      ...params,
      messages: [...params.messages, { role: "assistant", content: response.content }],
    });
    promptTokens += response.usage.input_tokens;
    completionTokens += response.usage.output_tokens;
  }
  if (response.stop_reason === "refusal") throw new Error("Claude declined this request");
  const content = anthropicText(response.content);
  return { content, model: response.model, promptTokens, completionTokens, costUsd: estimateCost(model, promptTokens, completionTokens) };
}

async function chatCompletions(
  env: Env,
  s: LlmSettings,
  model: string,
  messages: ChatMessage[],
  jsonMode: boolean,
): Promise<ChatResult> {
  const res = await fetch(`${s.baseUrl.replace(/\/+$/, "")}/chat/completions`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${s.apiKey}`,
      "Content-Type": "application/json",
      ...(s.provider === "openrouter" ? { "HTTP-Referer": env.SITE_ORIGIN || "http://localhost", "X-Title": "jobkit" } : {}),
    },
    body: JSON.stringify({ model, messages, ...(jsonMode ? { response_format: { type: "json_object" } } : {}) }),
  });
  const json = (await res.json().catch(() => ({}))) as {
    choices?: { message?: { content?: string } }[];
    usage?: { prompt_tokens?: number; completion_tokens?: number };
    error?: { message?: string };
  };
  if (!res.ok) throw new Error(`LLM ${res.status}: ${json?.error?.message || ""}`.trim());
  const content = json.choices?.[0]?.message?.content?.trim() ?? "";
  const promptTokens = json.usage?.prompt_tokens ?? Math.ceil(JSON.stringify(messages).length / 4);
  const completionTokens = json.usage?.completion_tokens ?? Math.ceil(content.length / 4);
  return { content, model, promptTokens, completionTokens, costUsd: estimateCost(model, promptTokens, completionTokens) };
}

export async function completeChat(
  env: Env,
  opts: { request?: Request | null; userId: string; messages: ChatMessage[]; model?: string; json?: boolean },
): Promise<ChatResult> {
  const settings = await loadLlmSettings(env, opts.userId);
  if (!settings) throw new Error("No LLM key configured: add one in Setup (/setup) or set OPENROUTER_API_KEY on the Worker.");
  const requested = opts.model ?? "google/gemini-2.5-flash-lite";
  const model = modelFor(settings, requested);
  if (settings.provider === "anthropic") {
    return stripFences(await claudeMessages(settings, model, opts.messages, opts.json ?? false, requested.endsWith(":online")));
  }
  return stripFences(await chatCompletions(env, settings, model, opts.messages, opts.json ?? false));
}

function stripFences(r: ChatResult): ChatResult {
  const m = r.content.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/);
  return m ? { ...r, content: m[1] } : r;
}
