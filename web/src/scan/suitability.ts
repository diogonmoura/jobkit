import type { SupabaseClient } from "@supabase/supabase-js";
import type { Env, PreferenceProfile } from "../types";
import { completeChat, type ChatMessage } from "../llm";
import { loadExperience } from "../experience";

/** Cheap default for the suitability check; override with profile.suitability_model. */
export const SUITABILITY_MODEL = "google/gemini-2.5-flash-lite";

export type Suitability = { ok: boolean; reason: string; model: string; key: string; checked_at: string };

const norm = (s: string) => s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();

/**
 * Deterministic pre-filter: engineer/developer titles are hands-on technical roles unless the title is clearly a
 * product, programme, operations or leadership role (e.g. "Product Manager, Engineering", "AI Ops Lead").
 */
export function isEngineeringTitle(title: string): boolean {
  const t = norm(title);
  if (!/\b(engineers?|engineering|developers?|programmers?|swe|sde|devops|sre|coder|full[- ]?stack|back[- ]?end|front[- ]?end|data scientist|machine learning|ml)\b/.test(t)) return false;
  if (/\b(engineer|developer|programmer|swe|sde|sre|scientist)s?\b/.test(t)) {
    // An "X engineer" is an engineering role even with "product" or "AI" in front of it.
    return !/\b(product|program|programme|project) (manager|lead|owner|director)\b|\bchief of staff\b|\b(operations|ops|enablement|strategy) (lead|manager|director|head)\b|\bhead of (operations|ops|strategy|enablement)\b/.test(t);
  }
  // "Engineering"/"ML" alone (e.g. "Engineering Manager", "PM, ML Platform"): only exclude hands-on wording.
  return /\b(full[- ]?stack|back[- ]?end|front[- ]?end|devops)\b/.test(t);
}

/** The constraints that drive the check, plus a cache key so a changed rule re-checks cached jobs. */
export function suitabilityRules(profile: PreferenceProfile): { rules: string[]; key: string } {
  const rules = (profile.role_constraints ?? []).map((s) => s.trim()).filter(Boolean);
  const model = profile.suitability_model?.trim() || SUITABILITY_MODEL;
  return { rules, key: `${model}|${rules.join("|")}` };
}

/** A short candidate summary from the newest uploaded experience files (falls back to the master CV). */
export async function candidateSummary(supabase: SupabaseClient, userId: string): Promise<string> {
  const exp = await loadExperience(supabase, userId);
  let text = exp.text;
  if (!text) {
    const { data } = await supabase.from("jobkit_profile").select("master_cv").eq("user_id", userId).maybeSingle();
    text = String(data?.master_cv ?? "");
  }
  return text.replace(/\n{3,}/g, "\n\n").slice(0, 1500);
}

export type SuitabilityInput = { title: string; company: string; text: string };

/** One batched cheap-model call: does each posting break a candidate constraint? Fails open (ok) on errors. */
export async function classifySuitability(
  env: Env,
  userId: string,
  request: Request | null,
  profile: PreferenceProfile,
  summary: string,
  items: SuitabilityInput[],
): Promise<{ results: Suitability[]; costUsd: number }> {
  const { rules, key } = suitabilityRules(profile);
  const model = profile.suitability_model?.trim() || SUITABILITY_MODEL;
  const now = new Date().toISOString();
  const pass = (reason = ""): Suitability => ({ ok: true, reason, model, key, checked_at: now });
  if (!rules.length || !items.length) return { results: items.map(() => pass()), costUsd: 0 };
  const out: Suitability[] = items.map(() => pass());
  let costUsd = 0;
  for (let start = 0; start < items.length; start += 10) {
    const chunk = items.slice(start, start + 10);
    const messages: ChatMessage[] = [
      {
        role: "system",
        content: [
          "You check job postings against a candidate's hard constraints, using the candidate summary for context.",
          "Default to suitable. Mark a posting unsuitable ONLY when the posting text itself clearly states a requirement that breaks a constraint as a core, must-have part of the job (for example: a required number of years of professional software engineering, writing production code daily, or an engineering job title).",
          "Being technical, hands-on, building with AI tools, designing architecture, working with engineers, or a nice-to-have skill do NOT break a coding constraint. Topics unrelated to the constraints never make a role unsuitable.",
          "For each unsuitable job, quote the exact phrase from the posting that proves it in evidence. If you cannot quote one, mark it suitable.",
          'Reply with JSON only: {"items":[{"i":number,"suitable":boolean,"evidence":string,"reason":string}]}. reason: one short English sentence (max 15 words), empty when suitable.',
        ].join("\n"),
      },
      {
        role: "user",
        content: JSON.stringify({
          constraints: rules,
          candidate_summary: summary,
          jobs: chunk.map((it, i) => ({ i, title: it.title, company: it.company, posting: it.text.slice(0, 2500) })),
        }),
      },
    ];
    try {
      const r = await completeChat(env, { request, userId, messages, model, json: true });
      costUsd += r.costUsd;
      const parsed = JSON.parse(r.content) as { items?: { i: number; suitable: boolean; reason?: string; evidence?: string }[] };
      for (const it of parsed.items ?? []) {
        const n = start + Number(it.i);
        const evidence = String((it as { evidence?: string }).evidence ?? "").trim();
        // Only trust a "no" that quotes the posting.
        const quoted = evidence.length > 8 && norm(chunk[Number(it.i)]?.text ?? "").includes(norm(evidence).slice(0, 40));
        if (n >= start && n < start + chunk.length && it.suitable === false && quoted) out[n] = { ok: false, reason: String(it.reason ?? "").trim().slice(0, 200) || "Breaks one of your role constraints.", model, key, checked_at: now };
      }
    } catch {
      /* fail open: the job stays visible */
    }
  }
  return { results: out, costUsd };
}

/** Title pre-filter verdict in the same shape (no AI call). */
export function titleVerdict(profile: PreferenceProfile, title: string): Suitability | null {
  if (!profile.exclude_engineering_titles || !isEngineeringTitle(title)) return null;
  const { key } = suitabilityRules(profile);
  return { ok: false, reason: "Engineering/developer title (hands-on technical role).", model: "title-rule", key, checked_at: new Date().toISOString() };
}

/** Check stored jobs (cached per job; only re-checked when the constraints change or force is set). */
export async function recheckJobs(env: Env, supabase: SupabaseClient, userId: string, request: Request | null, profile: PreferenceProfile, force = false) {
  const { key } = suitabilityRules(profile);
  const { data: jobs, error } = await supabase.from("jobkit_jobs").select("id, company, role, posting_text, suitability").eq("user_id", userId).eq("dismissed", false);
  if (error) throw error;
  const todo = (jobs ?? []).filter((j) => force || (j.suitability as Suitability | null)?.key !== key);
  const verdicts = new Map<string, Suitability>();
  const llm: typeof todo = [];
  for (const j of todo) {
    const v = titleVerdict(profile, String(j.role));
    if (v) verdicts.set(String(j.id), v);
    else llm.push(j);
  }
  let costUsd = 0;
  if (llm.length) {
    const summary = await candidateSummary(supabase, userId);
    const r = await classifySuitability(env, userId, request, profile, summary, llm.map((j) => ({ title: String(j.role), company: String(j.company), text: String(j.posting_text ?? "") })));
    costUsd = r.costUsd;
    llm.forEach((j, i) => verdicts.set(String(j.id), r.results[i]));
  }
  for (const [id, suitability] of verdicts) {
    const { error: e } = await supabase.from("jobkit_jobs").update({ suitability }).eq("user_id", userId).eq("id", id);
    if (e) throw e;
  }
  return { checked: verdicts.size, cached: (jobs ?? []).length - todo.length, hidden: [...verdicts.values()].filter((v) => !v.ok).length, costUsd };
}
