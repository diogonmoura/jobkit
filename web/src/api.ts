import { getOwnerId, jsonResponse, requireUser, safeEqual } from "./auth";
import { dbMode, serviceDb, serviceDbOr } from "./db";
import { proxyNeonAuth } from "./auth/proxy";
import { handleDbApi } from "./db/api";
import { handleEngineApi } from "./engine-api";
import { handleSetup } from "./setup";
import { completeChat } from "./llm";
import type { Env, PreferenceProfile } from "./types";
import {
  loadProfile,
  loadCandidateProfile,
  normalizeProfile,
  refreshProfileFromFeedback,
  saveProfile,
  seedProfileFromCv,
} from "./preferences";
import { runJobScan, importJobs } from "./scan/pipeline";
import { recheckJobs } from "./scan/suitability";
import { resolveBoardUrl, addBoard } from "./watchlist";
import { generateJobCv } from "./job-cv";
import { generateJobAnswers } from "./job-answers";
import { loadExperience } from "./experience";
import { buildTailorCvMessages } from "../public/jobkit-cv-prompt.js";
import { estimateSalary, extractPublishedSalary, extractBenefits } from "./salary";

export async function handleApi(
  request: Request,
  env: Env,
  url: URL,
): Promise<Response | null> {
  if (!url.pathname.startsWith("/api/")) return null;

  const path = url.pathname.slice("/api".length) || "/";

  // Postgres setups sign in through Neon Auth, proxied so its session cookies are first-party.
  if (dbMode(env) === "postgres" && (path === "/auth" || path.startsWith("/auth/"))) {
    return proxyNeonAuth(request, env, path.slice("/auth".length));
  }

  if (path === "/scan/cron" && request.method === "POST") {
    const secret = request.headers.get("X-Scan-Cron-Secret") ?? "";
    if (!env.SCAN_CRON_SECRET || !safeEqual(secret, env.SCAN_CRON_SECRET)) {
      return jsonResponse({ error: "Unauthorized" }, { status: 401 });
    }
    const ownerId = await getOwnerId(env);
    if (!ownerId) {
      return jsonResponse({ error: "Cron not configured: finish /setup first" }, { status: 503 });
    }
    const admin = serviceDb(env);
    const result = await runJobScan(env, admin, ownerId, "cron", null);
    return jsonResponse({ ok: true, ...result });
  }

  // The engine's token, never a browser session.
  const engineResponse = await handleEngineApi(request, env, path);
  if (engineResponse) return engineResponse;

  const setupResponse = await handleSetup(request, env, path);
  if (setupResponse) return setupResponse;

  const auth = await requireUser(request, env);
  if (!auth) {
    return jsonResponse({ error: "Unauthorized" }, { status: 401 });
  }
  const { userId, db, pending } = auth;

  if (path === "/session" && request.method === "GET") return jsonResponse({ userId });

  if (path === "/db" && request.method === "POST" && dbMode(env) === "postgres") {
    return handleDbApi(request, env, userId);
  }

  try {
    if (path === "/ai/chat" && request.method === "POST") {
      // Proxy for browser-side AI features (CV tailoring). Uses the key saved in Setup; never returns it.
      const body = (await request.json().catch(() => ({}))) as { messages?: { role: string; content: string }[]; model?: string };
      const messages = (Array.isArray(body.messages) ? body.messages : [])
        .filter((m) => m && ["system", "user", "assistant"].includes(m.role))
        .slice(0, 40)
        .map((m) => ({ role: m.role as "system" | "user" | "assistant", content: String(m.content ?? "").slice(0, 120_000) }));
      if (!messages.length) return jsonResponse({ error: "No messages" }, { status: 400 }, pending);
      try {
        const r = await completeChat(env, { request, userId, messages, model: body.model || "google/gemini-2.5-flash" });
        return jsonResponse({ model: r.model, choices: [{ message: { role: "assistant", content: r.content } }] }, {}, pending);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        return jsonResponse({ error: msg }, { status: /No LLM key/.test(msg) ? 412 : 502 }, pending);
      }
    }

    if (path === "/preferences" && request.method === "GET") {
      const profile = await loadProfile(db, userId);
      const candidateProfile = await loadCandidateProfile(db, userId);
      return jsonResponse({ profile, candidateProfile }, {}, pending);
    }

    if (path === "/preferences" && request.method === "PUT") {
      const body = (await request.json()) as { profile?: PreferenceProfile; candidateProfile?: string };
      const current = await loadProfile(db, userId);
      // The form only edits some fields; keep tiers, liked examples and other structured extras.
      const profile = normalizeProfile({ ...current, ...(body.profile ?? {}) });
      await saveProfile(db, userId, profile);
      if (typeof body.candidateProfile === "string") {
        const { error } = await db
          .from("jobkit_preference_profile")
          .update({ candidate_profile: body.candidateProfile.slice(0, 20000) })
          .eq("user_id", userId);
        if (error) throw error;
      }
      return jsonResponse({ profile }, {}, pending);
    }

    if (path === "/preferences/refresh-ai" && request.method === "POST") {
      const result = await refreshProfileFromFeedback(env, db, userId, request);
      return jsonResponse(
        { profile: result.profile, costUsd: result.costUsd },
        {},
        pending,
      );
    }

    if (path === "/preferences/seed" && request.method === "POST") {
      const result = await seedProfileFromCv(env, db, userId, request);
      return jsonResponse(
        { profile: result.profile, costUsd: result.costUsd },
        {},
        pending,
      );
    }

    if (path === "/scan" && request.method === "POST") {
      const result = await runJobScan(env, db, userId, "manual", request);
      return jsonResponse({ ok: true, ...result }, {}, pending);
    }

    // Watchlist: detect + test a pasted careers/board URL, then add it.
    if (path === "/watchlist/resolve" && request.method === "POST") {
      const body = (await request.json().catch(() => ({}))) as { url?: string };
      try {
        return jsonResponse({ ok: true, ...(await resolveBoardUrl(env, userId, request, String(body.url ?? ""))) }, {}, pending);
      } catch (e) {
        return jsonResponse({ error: e instanceof Error ? e.message : String(e) }, { status: 422 }, pending);
      }
    }
    if (path === "/watchlist/add" && request.method === "POST") {
      const body = (await request.json().catch(() => ({}))) as { vendor?: string; slug?: string; company?: string; hq?: string; industry?: string };
      const row = await addBoard(db, userId, { vendor: String(body.vendor ?? ""), slug: String(body.slug ?? ""), company: String(body.company ?? ""), hq: body.hq, industry: body.industry });
      return jsonResponse({ ok: true, target: row }, {}, pending);
    }

    // Re-check stored jobs against the role constraints (cached per job unless the rules change or force=true).
    if (path === "/jobs/suitability" && request.method === "POST") {
      const body = (await request.json().catch(() => ({}))) as { force?: boolean };
      const res = await recheckJobs(env, db, userId, request, await loadProfile(db, userId), Boolean(body.force));
      return jsonResponse({ ok: true, ...res }, {}, pending);
    }

    if (path === "/jobs/import" && request.method === "POST") {
      const body = (await request.json()) as { urls?: unknown };
      const urls = (Array.isArray(body.urls) ? body.urls : []).map(String).filter((x) => /^https?:\/\//.test(x)).slice(0, 8);
      if (!urls.length) return jsonResponse({ error: "No valid links" }, { status: 400 }, pending);
      const res = await importJobs(env, db, userId, request, urls);
      return jsonResponse({ ok: true, ...res }, {}, pending);
    }

    if (path === "/jobs/feedback" && request.method === "POST") {
      const body = (await request.json()) as {
        jobId?: string;
        signal?: string;
        reason?: string;
      };
      const jobId = String(body.jobId ?? "");
      const signal = String(body.signal ?? "");
      if (!jobId || !["up", "down", "dismiss", "process", "apply"].includes(signal)) {
        return jsonResponse({ error: "Invalid payload" }, { status: 400 }, pending);
      }
      const { error } = await db.from("jobkit_job_feedback").insert({
        user_id: userId,
        job_id: jobId,
        signal,
        reason: String(body.reason ?? "").slice(0, 500),
      });
      if (error) throw error;
      if (signal === "dismiss") {
        await db
          .from("jobkit_jobs")
          .update({
            dismissed: true,
            is_new: false,
            updated_at: new Date().toISOString(),
          })
          .eq("user_id", userId)
          .eq("id", jobId);
      } else if (signal === "down") {
        await db
          .from("jobkit_jobs")
          .update({ is_new: false, updated_at: new Date().toISOString() })
          .eq("user_id", userId)
          .eq("id", jobId);
      }
      return jsonResponse({ ok: true }, {}, pending);
    }

    if (path === "/jobs/cv" && request.method === "POST") {
      const body = (await request.json()) as { jobId?: string; force?: boolean; instructions?: string; postingText?: string };
      if (!body.jobId) return jsonResponse({ error: "jobId em falta" }, { status: 400 }, pending);
      const out = await generateJobCv(env, db, userId, request, {
        jobId: String(body.jobId),
        force: body.force === true,
        instructions: body.instructions ? String(body.instructions) : "",
        postingText: body.postingText ? String(body.postingText) : "",
      });
      return jsonResponse(out, {}, pending);
    }

    // Read-only: what the experience files contribute to the CV prompt (no AI call).
    if (path === "/experience/preview" && request.method === "GET") {
      const exp = await loadExperience(db, userId);
      const msgs = buildTailorCvMessages({ masterCv: "(master)", postingText: "(posting)", company: "", role: "", experience: exp.text });
      const user = String(msgs[1]?.content ?? "");
      const at = user.indexOf("<experience_notes>");
      return jsonResponse({
        count: exp.count, total: exp.total, limit: exp.limit, truncated: exp.truncated,
        inCvPrompt: at >= 0, excerpt: at >= 0 ? user.slice(Math.max(0, at - 160), at + 400) : "",
      }, {}, pending);
    }

    if (path === "/jobs/answers" && request.method === "POST") {
      const body = (await request.json()) as { jobId?: string; force?: boolean; instructions?: string; manualQuestions?: string[] };
      if (!body.jobId) return jsonResponse({ error: "jobId em falta" }, { status: 400 }, pending);
      const out = await generateJobAnswers(env, db, userId, request, {
        jobId: String(body.jobId),
        force: body.force === true,
        instructions: body.instructions ? String(body.instructions) : "",
        manualQuestions: Array.isArray(body.manualQuestions) ? body.manualQuestions.map(String).slice(0, 30) : [],
      });
      return jsonResponse(out, {}, pending);
    }

    if (path === "/jobs/salary" && request.method === "POST") {
      const body = (await request.json()) as {
        jobId?: string;
        role?: string;
        seniority?: string;
        location?: string;
        postingText?: string;
      };
      const jobId = String(body.jobId ?? "");
      let postingText = String(body.postingText ?? "");
      let role = String(body.role ?? "");
      let location = String(body.location ?? "");
      if (jobId) {
        const { data: job } = await db
          .from("jobkit_jobs")
          .select("role, location, posting_text, salary")
          .eq("user_id", userId)
          .eq("id", jobId)
          .maybeSingle();
        if (job) {
          role = role || String(job.role);
          location = location || String(job.location);
          postingText = postingText || String(job.posting_text ?? "");
          const existing = job.salary as Record<string, unknown> | null;
          if (existing && (existing.published || existing.estimate)) {
            return jsonResponse({ salary: existing }, {}, pending);
          }
        }
      }
      const published = extractPublishedSalary(postingText);
      const benefits = extractBenefits(postingText);
      if (published) {
        const salary = { published, benefits };
        if (jobId) {
          await db
            .from("jobkit_jobs")
            .update({ salary, benefits, updated_at: new Date().toISOString() })
            .eq("user_id", userId)
            .eq("id", jobId);
        }
        return jsonResponse({ salary }, {}, pending);
      }
      const admin = serviceDbOr(env, db);
      const profile = await loadProfile(db, userId);
      const seniority = String(body.seniority ?? profile.seniority[0] ?? "mid");
      const { estimate, costUsd } = await estimateSalary(
        env,
        userId,
        request,
        role,
        seniority,
        location || profile.home_base?.country || profile.locations[0] || "Remote",
        admin,
      );
      const salary = { estimate, benefits };
      if (jobId) {
        await db
          .from("jobkit_jobs")
          .update({ salary, benefits, updated_at: new Date().toISOString() })
          .eq("user_id", userId)
          .eq("id", jobId);
      }
      return jsonResponse({ salary, costUsd }, {}, pending);
    }

    return jsonResponse({ error: "Not found" }, { status: 404 }, pending);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return jsonResponse({ error: message }, { status: 500 }, pending);
  }
}
