import { langName } from "./i18n";
import type { Db } from "./db/types";
import type { Env } from "./types";
import { completeChat, type ChatMessage } from "./llm";
import { loadExperience } from "./experience";
import { echoCheck, titleEcho } from "../public/jobkit-echo.js";
// Shared with the browser so server and client tailor CVs with the same rules.
import { buildTailorCvMessages } from "../public/jobkit-cv-prompt.js";

const SPLIT = "<!--ANALYSIS-->";

/**
 * Generate a tailored CV for a scanned job exactly once and persist it.
 * Returns the stored CV without any AI call when one exists, unless force is true.
 */
export async function generateJobCv(
  env: Env,
  supabase: Db,
  userId: string,
  request: Request,
  opts: { jobId: string; force?: boolean; instructions?: string; postingText?: string },
): Promise<{ cv_md: string; cv_analysis: string; cached: boolean; costUsd: number }> {
  const { data: job, error } = await supabase
    .from("jobkit_jobs")
    .select("id, company, role, posting_text, cv_md, cv_analysis")
    .eq("user_id", userId)
    .eq("id", opts.jobId)
    .maybeSingle();
  if (error) throw error;
  if (!job) throw new Error("Job not found");
  if (String(job.cv_md ?? "").trim() && !opts.force) {
    return { cv_md: String(job.cv_md), cv_analysis: String(job.cv_analysis ?? ""), cached: true, costUsd: 0 };
  }

  const { data: prof } = await supabase.from("jobkit_profile").select("master_cv").eq("user_id", userId).maybeSingle();
  const masterCv = String(prof?.master_cv ?? "");
  if (!masterCv.trim()) throw new Error("Add your master CV first");
  const postingText = (opts.postingText ?? "").trim() || String(job.posting_text ?? "");

  const experience = (await loadExperience(supabase, userId)).text;
  const { data: pp } = await supabase.from("jobkit_preference_profile").select("profile").eq("user_id", userId).maybeSingle();
  const writingRules = String((pp?.profile as { writing_rules?: string } | null)?.writing_rules ?? "").trim();
  const messages = buildTailorCvMessages({ masterCv, postingText, company: job.company, role: job.role, experience }) as ChatMessage[];
  const extra = [
    opts.instructions?.trim() ? `Additional instructions from the candidate for this version: ${opts.instructions.trim()}` : "",
    writingRules ? `Writing rules from the candidate (follow them):\n${writingRules}` : "",
    `After the CV Markdown, output a line containing only ${SPLIT} and then 3 to 5 short bullets in ${langName(env)} on fit, gaps and what to emphasise in the interview. Nothing after the bullets.`,
  ].filter(Boolean).join("\n\n");
  messages.push({ role: "user", content: extra });

  const result = await completeChat(env, { request, userId, messages, model: "google/gemini-2.5-pro" });
  const [cvRaw, analysisRaw = ""] = result.content.split(SPLIT);
  let cv_md = cvRaw.replace(/^```(?:markdown|md)?\s*/i, "").replace(/```\s*$/, "").trim();
  if (!/^##\s*Professional Profile\s*$/im.test(cv_md)) {
    // The profile is a fixed part of the template: ask once for just that paragraph.
    const fix = await completeChat(env, {
      request, userId, model: "google/gemini-2.5-flash",
      messages: [...messages, { role: "assistant", content: cv_md }, { role: "user", content: "Write only the Professional Profile paragraph for this CV: 45–70 words, plain text, no heading." }],
    });
    cv_md = insertProfile(cv_md, fix.content.trim());
  }
  cv_md = clampProfile(cv_md, 70);

  // Anti-echo: one targeted rewrite when sentences reuse distinctive 4-grams from the posting or the target title.
  const own = `${masterCv}\n${experience}`;
  const before = echoCheck(cv_md, postingText);
  const tBefore = titleEcho(cv_md.split(/^##\s*Professional Experience/im)[0], job.role, own);
  let after = before, tAfter = tBefore;
  if (before.hits.length || tBefore) {
    const list = before.hits.map((h) => `- "${h.sentence}" (copied phrases: ${h.grams.map((g) => `"${g}"`).join(", ")})`).join("\n");
    const rw = await completeChat(env, {
      request, userId, model: "google/gemini-2.5-flash",
      messages: [
        ...messages.slice(0, 2),
        { role: "assistant", content: cv_md },
        { role: "user", content: [
          "Rewrite ONLY the sentences below so they no longer reuse wording from the job posting. Express the same true facts in the candidate's own voice with concrete specifics. Keep every other line exactly as it is. Same facts, no new claims, profile still 45-70 words.",
          list,
          tBefore ? `Also: the profile calls the candidate "${tBefore}", a title they have not held. Describe them by their real roles instead.` : "",
          "Return the full CV Markdown only.",
        ].filter(Boolean).join("\n\n") },
      ],
    });
    const fixed = rw.content.replace(/^```(?:markdown|md)?\s*/i, "").replace(/```\s*$/, "").split(SPLIT)[0].trim();
    if (fixed.startsWith("#") && /^##\s*Professional Profile/im.test(fixed)) {
      cv_md = clampProfile(fixed, 70);
      after = echoCheck(cv_md, postingText);
      tAfter = titleEcho(cv_md.split(/^##\s*Professional Experience/im)[0], job.role, own);
    }
  }
  const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
  const echoLine = `- Overlap with the posting (distinctive 4-grams): ${pct(before.score)} (${before.hits.length} sentence(s)${tBefore ? ", title copied" : ""})` +
    (after !== before ? ` → ${pct(after.score)} (${after.hits.length} sentence(s)${tAfter ? ", title copied" : ""}) after rewrite` : "");
  const cv_analysis = [analysisRaw.trim(), echoLine].filter(Boolean).join("\n");
  if (!cv_md.startsWith("#")) throw new Error("Generation did not return a valid CV");

  const { error: upErr } = await supabase
    .from("jobkit_jobs")
    .update({
      cv_md,
      cv_analysis,
      cv_generated_at: new Date().toISOString(),
      cv_instructions: String(opts.instructions ?? "").slice(0, 2000),
      posting_text: postingText.slice(0, 50000),
      is_new: false,
      updated_at: new Date().toISOString(),
    })
    .eq("user_id", userId)
    .eq("id", opts.jobId);
  if (upErr) throw upErr;
  await supabase.from("jobkit_job_feedback").insert({ user_id: userId, job_id: opts.jobId, signal: "process", reason: opts.force ? "regenerate" : "" });
  return { cv_md, cv_analysis, cached: false, costUsd: result.costUsd };
}

function insertProfile(md: string, para: string): string {
  const lines = md.split("\n");
  const firstSection = lines.findIndex((l) => /^##\s/.test(l));
  const at = firstSection < 0 ? lines.length : firstSection;
  lines.splice(at, 0, "## Professional Profile", para, "");
  return lines.join("\n");
}

/** Keep the profile within maxWords by dropping trailing sentences. */
export function clampProfile(md: string, maxWords: number): string {
  return md.replace(/(^##\s*Professional Profile\s*\n)([\s\S]*?)(?=\n##\s|$)/im, (_m, head: string, body: string) => {
    const text = body.replace(/\s+/g, " ").trim();
    if (text.split(" ").length <= maxWords) return head + text + "\n";
    let out = "";
    for (const sn of text.split(/(?<=[.!?])\s+/)) {
      if (out && (out + " " + sn).trim().split(/\s+/).length > maxWords) break;
      out = (out + " " + sn).trim();
    }
    return head + out.trim() + "\n";
  });
}
