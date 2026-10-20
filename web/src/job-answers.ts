import type { SupabaseClient } from "@supabase/supabase-js";
import type { Env } from "./types";
import { completeChat, type ChatMessage } from "./llm";
import { loadCandidateProfile } from "./preferences";
import { loadExperience } from "./experience";
import { EXPERIENCE_HEADING } from "../public/jobkit-experience.js";
import { ANTI_ECHO_RULES, echoCheck } from "../public/jobkit-echo.js";

export type QuestionType = "text" | "longtext" | "number" | "yesno" | "select" | "multiselect" | "file" | "other";
export interface AppQuestion {
  id: string;
  label: string;
  type: QuestionType;
  required: boolean;
  options: string[];
  trivial: boolean;
  answer: string;
  needs_input: boolean;
  note: string;
}
export interface JobAnswers {
  source: string;
  questions: AppQuestion[];
  generated_at: string;
}

// Whole-label match only, so "Name one business metric…" is a real question.
const TRIVIAL = /^(?:your\s+)?(name|first name|last name|full name|preferred name|e-?mail( address)?|phone( number)?|mobile( number)?|telephone|resume|cv|curriculum vitae|resume\/cv|cv\/resume|linkedin( profile)?( url)?|cover letter)\s*\*?$/i;

function q(id: string, label: string, type: QuestionType, required: boolean, options: string[] = []): AppQuestion {
  const clean = label.replace(/\s+/g, " ").trim();
  return {
    id, label: clean, type, required, options: options.map((o) => o.trim()).filter(Boolean),
    trivial: type === "file" || TRIVIAL.test(clean) || /^_systemfield_(name|email|phone|resume|linkedin)/.test(id),
    answer: "", needs_input: false, note: "",
  };
}

const ASHBY_TYPES: Record<string, QuestionType> = {
  String: "text", LongText: "longtext", Email: "text", Phone: "text", Number: "number", Boolean: "yesno",
  ValueSelect: "select", MultiValueSelect: "multiselect", File: "file", Date: "text", Location: "text", SocialLink: "text",
};

/** Read the public application form for Ashby, Greenhouse or Lever postings. */
export async function fetchApplicationQuestions(url: string): Promise<{ source: string; questions: AppQuestion[] }> {
  let m = url.match(/jobs\.ashbyhq\.com\/([^/?#]+)\/([0-9a-f-]{36})/i);
  if (m) {
    const res = await fetch("https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        operationName: "ApiJobPosting",
        variables: { organizationHostedJobsPageName: decodeURIComponent(m[1]), jobPostingId: m[2] },
        query: "query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) { jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) { applicationForm { sections { fieldEntries { ... on FormFieldEntry { id isRequired field } } } } } }",
      }),
    });
    if (!res.ok) throw new Error(`Ashby ${res.status}`);
    const d = (await res.json()) as any;
    const out: AppQuestion[] = [];
    for (const s of d?.data?.jobPosting?.applicationForm?.sections ?? []) {
      for (const e of s.fieldEntries ?? []) {
        const f = e.field ?? {};
        const type = ASHBY_TYPES[f.type] ?? "other";
        const multi = f.type === "ValueSelect" && /select all/i.test(f.title ?? "");
        out.push(q(String(f.path || e.id), String(f.title ?? ""), multi ? "multiselect" : type, !!e.isRequired,
          (f.selectableValues ?? []).map((v: any) => String(v.label ?? v.value ?? ""))));
      }
    }
    return { source: "ashby", questions: out };
  }
  m = url.match(/greenhouse\.io\/(?:embed\/job_app\?for=)?([^/?#&]+)\/jobs\/(\d+)/i) || url.match(/greenhouse\.io\/embed\/job_app\?for=([^&]+)&token=(\d+)/i);
  if (m) {
    const res = await fetch(`https://boards-api.greenhouse.io/v1/boards/${m[1]}/jobs/${m[2]}?questions=true`);
    if (!res.ok) throw new Error(`Greenhouse ${res.status}`);
    const d = (await res.json()) as any;
    const out: AppQuestion[] = [];
    for (const g of d.questions ?? []) {
      const f = (g.fields ?? [])[0] ?? {};
      const t = String(f.type ?? "");
      const type: QuestionType = t === "input_file" ? "file" : t === "textarea" ? "longtext" : t === "multi_value_single_select" ? "select" : t === "multi_value_multi_select" ? "multiselect" : "text";
      out.push(q(String(f.name ?? g.label), String(g.label ?? ""), type, !!g.required, (f.values ?? []).map((v: any) => String(v.label ?? ""))));
    }
    return { source: "greenhouse", questions: out };
  }
  if (/jobs\.lever\.co\//i.test(url)) {
    // Lever's public postings API does not expose custom questions; the candidate adds them manually.
    return { source: "lever", questions: [q("name", "Full name", "text", true), q("email", "Email", "text", true), q("resume", "Resume/CV", "file", true)] };
  }
  return { source: "manual", questions: [] };
}

function parseJsonArray(text: string): any[] {
  const s = text.replace(/^```(?:json)?\s*/i, "").replace(/```\s*$/, "").trim();
  const a = s.indexOf("["), b = s.lastIndexOf("]");
  return JSON.parse(a >= 0 ? s.slice(a, b + 1) : s);
}

/**
 * Draft answers to the job's application questions once and store them on the job.
 * Returns the stored answers without any AI call unless force is true.
 */
export async function generateJobAnswers(
  env: Env, supabase: SupabaseClient, userId: string, request: Request,
  opts: { jobId: string; force?: boolean; instructions?: string; manualQuestions?: string[] },
): Promise<JobAnswers & { cached: boolean }> {
  const { data: job, error } = await supabase
    .from("jobkit_jobs")
    .select("id, company, role, posting, posting_text, location, cv_md, answers")
    .eq("user_id", userId).eq("id", opts.jobId).maybeSingle();
  if (error) throw error;
  if (!job) throw new Error("Job not found");
  const saved = job.answers as JobAnswers | null;
  if (saved?.questions?.length && !opts.force) return { ...saved, cached: true };

  let { source, questions } = await fetchApplicationQuestions(String(job.posting ?? ""));
  // Keep manual questions (added earlier or now) on regenerate.
  const manual = [
    ...(saved?.questions ?? []).filter((x) => x.id.startsWith("manual-")).map((x) => x.label),
    ...(opts.manualQuestions ?? []),
  ];
  for (const label of [...new Set(manual.map((x) => x.trim()).filter(Boolean))]) {
    if (!questions.some((x) => x.label === label)) questions.push(q(`manual-${questions.length}`, label, "longtext", false));
  }
  if (!questions.length) throw new Error("No questions found for this job. Add them manually.");

  const [{ data: prof }, candidate, { data: pp }, exp] = await Promise.all([
    supabase.from("jobkit_profile").select("master_cv").eq("user_id", userId).maybeSingle(),
    loadCandidateProfile(supabase, userId),
    supabase.from("jobkit_preference_profile").select("profile").eq("user_id", userId).maybeSingle(),
    loadExperience(supabase, userId),
  ]);
  const notice = String((pp?.profile as any)?.notice_period ?? "").trim();
  const targetComp = String((pp?.profile as any)?.target_comp ?? "").trim();
  const home = (pp?.profile as any)?.home_base ?? {};
  const homeLabel = [home.city, home.country].filter(Boolean).join(", ");
  const workAuth = String((pp?.profile as any)?.work_authorization ?? "").trim();
  const real = questions.filter((x) => !x.trivial);

  const messages: ChatMessage[] = [
    {
      role: "system",
      content: [
        "You draft answers to job application form questions for the candidate, in the first person.",
        "Be strictly truthful: use only facts from the detailed experience notes (richest source), candidate profile, master CV and tailored CV. Never invent experience, numbers, tools or employers. If the facts needed are missing, give the closest honest answer and set needs_input true with a short note.",
        "Every claim must be traceable to a specific line in the sources. Do NOT describe processes, frameworks, metrics, before/after numbers, team sizes, revenue figures or durations that are not written in the sources — not even plausible generic ones.",
        "If a question asks for specifics the sources lack (e.g. a number, experiment count, P&L size), answer only with what is true, write the gap as a bracketed placeholder like [number of users] for the candidate to fill, and set needs_input true.",
        "Never leave an answer empty: use a short draft or a placeholder such as [to confirm].",
        ...ANTI_ECHO_RULES,
        "Be concise: short questions get one line; open questions 60–150 words. Answer in the language of the question.",
        "For select questions answer with the exact option label(s) from the list (comma-separated for multi-select).",
        homeLabel || workAuth
          ? `Location and right to work: the candidate is based in ${homeLabel || "(not set)"}${workAuth ? `; work authorization: ${workAuth}` : ""}. For right-to-work questions answer only from this; set needs_input true if other countries are involved and unclear.`
          : "Location and right to work: not set in the profile; answer with a placeholder and set needs_input true.",
        targetComp ? `Salary expectation: use the candidate's target (${targetComp}); keep needs_input true so the candidate confirms it.` : "Salary expectation: no target is set; leave a placeholder and set needs_input true.",
        notice ? `Notice period: ${notice}.` : "Notice period: only state it if the materials say so; otherwise needs_input true.",
        'Output ONLY a JSON array: [{"id": "...", "answer": "...", "needs_input": false, "note": ""}] with one item per question id given. The note (English) says concretely what is missing or must be checked (not a translation of the question).',
      ].join("\n"),
    },
    {
      role: "user",
      content: [
        `Job: ${job.role} at ${job.company} (${job.location ?? ""})`,
        `<posting>\n${String(job.posting_text ?? "").slice(0, 12000)}\n</posting>`,
        `<candidate_profile>\n${candidate.slice(0, 12000)}\n</candidate_profile>`,
        `<master_cv>\n${String(prof?.master_cv ?? "").slice(0, 12000)}\n</master_cv>`,
        exp.text ? `${EXPERIENCE_HEADING}\n<experience_notes>\n${exp.text}\n</experience_notes>` : "",
        job.cv_md ? `<tailored_cv>\n${String(job.cv_md).slice(0, 8000)}\n</tailored_cv>` : "",
        opts.instructions?.trim() ? `Extra instructions from the candidate: ${opts.instructions.trim()}` : "",
        `<questions>\n${JSON.stringify(real.map((x) => ({ id: x.id, question: x.label, type: x.type, required: x.required, options: x.options })), null, 1)}\n</questions>`,
      ].filter(Boolean).join("\n\n"),
    },
  ];
  const result = await completeChat(env, { request, userId, messages, model: "google/gemini-2.5-pro" });
  const items = parseJsonArray(result.content);
  let byId = new Map(items.map((it: any) => [String(it.id), it]));
  // Anti-echo: one rewrite pass for answers that reuse distinctive 4-grams from the posting.
  const postingText = String(job.posting_text ?? "");
  const echoed = items.filter((it: any) => echoCheck(String(it.answer ?? ""), postingText).hits.length);
  const echoBefore = items.filter((it: any) => it.answer).map((it: any) => echoCheck(String(it.answer), postingText));
  if (echoed.length) {
    const detail = echoed.map((it: any) => ({ id: it.id, answer: it.answer, copied: echoCheck(String(it.answer), postingText).hits.flatMap((h) => h.grams) }));
    const rw = await completeChat(env, { request, userId, model: "google/gemini-2.5-flash", messages: [
      ...messages,
      { role: "assistant", content: result.content },
      { role: "user", content: `These answers reuse wording from the job posting. Rewrite each so it no longer uses the copied phrases, keeping the same true facts and placeholders, in the candidate's own voice with concrete specifics. Output ONLY a JSON array of {"id","answer","needs_input","note"} for these ids.\n${JSON.stringify(detail, null, 1)}` },
    ] });
    try { for (const it of parseJsonArray(rw.content)) if (byId.has(String(it.id)) && String(it.answer ?? "").trim()) byId.set(String(it.id), { ...byId.get(String(it.id)), ...it }); } catch { /* keep first pass */ }
  }
  const echoAfter = [...byId.values()].filter((it: any) => it.answer).map((it: any) => echoCheck(String(it.answer), postingText));
  const sum = (a: ReturnType<typeof echoCheck>[]) => { const t = a.reduce((x, y) => x + y.total, 0); return t ? a.reduce((x, y) => x + y.shared, 0) / t : 0; };
  const echoStats = { before: sum(echoBefore), after: sum(echoAfter), rewritten: echoed.length };
  for (const x of real) {
    const it: any = byId.get(x.id) ?? {};
    x.answer = String(it.answer ?? "").trim();
    x.needs_input = !!it.needs_input || !x.answer;
    x.note = String(it.note ?? "").trim();
  }
  // Prefill identity fields from the tailored CV header (never stored in the repo).
  const cvText = String(job.cv_md ?? "");
  const name = (cvText.match(/^#\s+(.+)$/m) || [])[1]?.trim() ?? "";
  const email = (cvText.match(/[\w.+-]+@[\w-]+\.[\w.]+/) || [])[0] ?? "";
  const phone = (cvText.match(/\(?\+\d[\d\s()-]{7,}\d/) || [])[0] ?? "";
  for (const x of questions.filter((y) => y.trivial)) {
    if (/name/i.test(x.label)) x.answer = name;
    else if (/e-?mail/i.test(x.label)) x.answer = email;
    else if (/phone|mobile/i.test(x.label)) x.answer = phone;
    else if (x.type === "file") x.answer = "(attach the CV for this job)";
  }
  const out: JobAnswers & { echo?: typeof echoStats } = { source, questions, generated_at: new Date().toISOString(), echo: echoStats };
  const { error: upErr } = await supabase.from("jobkit_jobs")
    .update({ answers: out, answers_generated_at: out.generated_at, updated_at: out.generated_at })
    .eq("user_id", userId).eq("id", opts.jobId);
  if (upErr) throw upErr;
  return { ...out, cached: false };
}
