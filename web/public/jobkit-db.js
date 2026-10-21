/**
 * Firestore-shaped adapter over Supabase Postgres + Realtime.
 * Used by the hosted tracker (web/public/index.html).
 */
export function createJobkitDb(supabase, userId) {
  const appListeners = new Set();
  const followingListeners = new Set();
  const profileListeners = new Set();
  const jobsListeners = new Set();
  const preferencesListeners = new Set();
  let appsCache = [];
  let followingCache = null;
  let profileCache = { master_cv: "" };
  let jobsCache = [];
  let preferencesCache = { profile: {} };

  async function loadApps() {
    const { data, error } = await supabase
      .from("jobkit_apps")
      .select("*")
      .eq("user_id", userId);
    if (error) throw error;
    appsCache = (data ?? []).map(rowToApp);
    notifyApps();
  }

  async function loadFollowing() {
    const { data, error } = await supabase
      .from("jobkit_following")
      .select("*")
      .eq("user_id", userId)
      .eq("id", "all")
      .maybeSingle();
    if (error) throw error;
    followingCache = data
      ? { updated: data.updated ?? "", companies: data.companies ?? [] }
      : null;
    notifyFollowing();
  }

  async function loadJobs() {
    const { data, error } = await supabase
      .from("jobkit_jobs")
      .select("*")
      .eq("user_id", userId)
      .order("created_at", { ascending: false });
    if (error) throw error;
    jobsCache = (data ?? []).map(rowToJob);
    notifyJobs();
  }

  async function loadPreferences() {
    const { data, error } = await supabase
      .from("jobkit_preference_profile")
      .select("profile")
      .eq("user_id", userId)
      .maybeSingle();
    if (error) throw error;
    preferencesCache = { profile: data?.profile ?? {} };
    notifyPreferences();
  }

  async function loadProfile() {
    const { data, error } = await supabase
      .from("jobkit_profile")
      .select("master_cv")
      .eq("user_id", userId)
      .maybeSingle();
    if (error) throw error;
    profileCache = { master_cv: data?.master_cv ?? "" };
    notifyProfile();
  }

  function notifyApps() {
    const snap = {
      docs: appsCache.map((a) => ({
        id: a.id,
        data: () => {
          const { id, ...rest } = a;
          return rest;
        },
      })),
    };
    for (const cb of appListeners) cb(snap);
  }

  function notifyFollowing() {
    const snap = {
      docs: followingCache
        ? [{ id: "all", data: () => followingCache }]
        : [],
    };
    for (const cb of followingListeners) cb(snap);
  }

  function notifyProfile() {
    const snap = {
      docs: [{ id: "profile", data: () => ({ ...profileCache }) }],
    };
    for (const cb of profileListeners) cb(snap);
  }

  function notifyJobs() {
    const snap = {
      docs: jobsCache.map((j) => ({
        id: j.id,
        data: () => {
          const { id, ...rest } = j;
          return rest;
        },
      })),
    };
    for (const cb of jobsListeners) cb(snap);
  }

  function notifyPreferences() {
    const snap = {
      docs: [{ id: "preferences", data: () => ({ ...preferencesCache.profile }) }],
    };
    for (const cb of preferencesListeners) cb(snap);
  }

  const channel = supabase
    .channel(`jobkit-tracker-${userId}`)
    .on(
      "postgres_changes",
      {
        event: "*",
        schema: "public",
        table: "jobkit_apps",
        filter: `user_id=eq.${userId}`,
      },
      () => loadApps().catch(() => {}),
    )
    .on(
      "postgres_changes",
      {
        event: "*",
        schema: "public",
        table: "jobkit_following",
        filter: `user_id=eq.${userId}`,
      },
      () => loadFollowing().catch(() => {}),
    )
    .on(
      "postgres_changes",
      {
        event: "*",
        schema: "public",
        table: "jobkit_profile",
        filter: `user_id=eq.${userId}`,
      },
      () => loadProfile().catch(() => {}),
    )
    .on(
      "postgres_changes",
      {
        event: "*",
        schema: "public",
        table: "jobkit_jobs",
        filter: `user_id=eq.${userId}`,
      },
      () => loadJobs().catch(() => {}),
    )
    .on(
      "postgres_changes",
      {
        event: "*",
        schema: "public",
        table: "jobkit_preference_profile",
        filter: `user_id=eq.${userId}`,
      },
      () => loadPreferences().catch(() => {}),
    )
    .subscribe();

  void loadApps();
  void loadFollowing();
  void loadProfile();
  void loadJobs();
  void loadPreferences();

  // Detailed experience files (.md/.txt). Plain CRUD, no AI.
  const experience = {
    async list() {
      const { data, error } = await supabase
        .from("jobkit_experience_files")
        .select("id, filename, size, content, enabled, uploaded_at, updated_at, kind, version_label, version_date")
        .eq("user_id", userId)
        .order("uploaded_at", { ascending: true });
      if (error) throw error;
      return data ?? [];
    },
    async add({ filename, content, size, kind = "md", version_label = "", version_date = null }) {
      const { error } = await supabase.from("jobkit_experience_files").insert({
        user_id: userId, filename, content, size: size ?? new Blob([content]).size, enabled: true,
        kind, version_label, version_date: version_date || null,
      });
      if (error) throw error;
    },
    async update(id, fields) {
      const patch = { ...fields, updated_at: new Date().toISOString() };
      if (fields.content !== undefined) { if (patch.size === undefined) patch.size = new Blob([fields.content]).size; patch.uploaded_at = patch.updated_at; }
      if (patch.version_date === "") patch.version_date = null;
      const { error } = await supabase.from("jobkit_experience_files").update(patch).eq("user_id", userId).eq("id", id);
      if (error) throw error;
    },
    async remove(id) {
      const { error } = await supabase.from("jobkit_experience_files").delete().eq("user_id", userId).eq("id", id);
      if (error) throw error;
    },
  };

  // Job ids that already have a 👍/👎 (the thumbs are hidden once used).
  const feedback = {
    async voted() {
      const { data, error } = await supabase
        .from("jobkit_job_feedback")
        .select("job_id, signal")
        .eq("user_id", userId)
        .in("signal", ["up", "down"]);
      if (error) throw error;
      return new Map((data ?? []).map((r) => [r.job_id, r.signal]));
    },
  };

  // Companies the daily scan watches (read-only here; edited in Setup).
  const scanTargets = {
    async list() {
      const { data, error } = await supabase
        .from("jobkit_scan_targets")
        .select("id, company, vendor, slug, enabled, last_scanned_at")
        .eq("user_id", userId)
        .order("company");
      if (error) throw error;
      return data ?? [];
    },
  };

  return {
    experience,
    feedback,
    scanTargets,
    collection(name) {
      if (name === "apps") {
        return {
          onSnapshot(onNext, onError) {
            appListeners.add(onNext);
            loadApps().catch((e) => onError && onError({ code: e.message }));
            return () => appListeners.delete(onNext);
          },
        };
      }
      if (name === "following") {
        return {
          onSnapshot(onNext, onError) {
            followingListeners.add(onNext);
            loadFollowing().catch((e) => onError && onError({ code: e.message }));
            return () => followingListeners.delete(onNext);
          },
        };
      }
      if (name === "profile") {
        return {
          onSnapshot(onNext, onError) {
            profileListeners.add(onNext);
            loadProfile().catch((e) => onError && onError({ code: e.message }));
            return () => profileListeners.delete(onNext);
          },
        };
      }
      if (name === "jobs") {
        return {
          onSnapshot(onNext, onError) {
            jobsListeners.add(onNext);
            loadJobs().catch((e) => onError && onError({ code: e.message }));
            return () => jobsListeners.delete(onNext);
          },
        };
      }
      if (name === "preferences") {
        return {
          onSnapshot(onNext, onError) {
            preferencesListeners.add(onNext);
            loadPreferences().catch((e) => onError && onError({ code: e.message }));
            return () => preferencesListeners.delete(onNext);
          },
        };
      }
      throw new Error("unknown collection: " + name);
    },
    doc(path) {
      const [coll, id] = path.split("/");
      if (coll === "profile") {
        return {
          async set(fields) {
            const master_cv = fields.master_cv ?? profileCache.master_cv ?? "";
            const { error } = await supabase.from("jobkit_profile").upsert(
              { user_id: userId, master_cv, updated_at: new Date().toISOString() },
              { onConflict: "user_id" },
            );
            if (error) throw error;
            await loadProfile();
          },
          async update(fields) {
            return this.set({ ...profileCache, ...fields });
          },
        };
      }
      if (coll === "jobs") {
        return {
          async update(fields) {
            const existing = jobsCache.find((j) => j.id === id);
            const merged = { ...(existing ?? { id }), ...fields };
            const row = jobToRow(userId, id, merged);
            const { error } = await supabase
              .from("jobkit_jobs")
              .upsert(row, { onConflict: "user_id,id" });
            if (error) throw error;
            await loadJobs();
          },
        };
      }
      if (coll !== "apps") throw new Error("unsupported doc: " + path);
      return {
        async update(fields) {
          const existing = appsCache.find((a) => a.id === id);
          const merged = { ...(existing ?? { id }), ...fields };
          if (merged.status === undefined && existing) merged.status = existing.status;
          const row = appToRow(userId, id, merged);
          const { error } = await supabase
            .from("jobkit_apps")
            .upsert(row, { onConflict: "user_id,id" });
          if (error) throw error;
          await loadApps();
        },
        async delete() {
          const { error } = await supabase
            .from("jobkit_apps")
            .delete()
            .eq("user_id", userId)
            .eq("id", id);
          if (error) throw error;
          await loadApps();
        },
        async set(fields) {
          const row = appToRow(userId, id, { id, ...fields });
          const { error } = await supabase
            .from("jobkit_apps")
            .upsert(row, { onConflict: "user_id,id" });
          if (error) throw error;
          await loadApps();
        },
      };
    },
    dispose() {
      supabase.removeChannel(channel);
    },
  };
}

function rowToApp(row) {
  return {
    id: row.id,
    company: row.company,
    role: row.role,
    applied: row.applied,
    cv: row.cv ?? "",
    cv_md: row.cv_md ?? "",
    posting_text: row.posting_text ?? "",
    status: row.status,
    last: row.last ?? (row.status === "considering" ? "" : row.applied),
    posting: row.posting ?? "",
    events: row.events ?? [],
    job_id: row.job_id ?? null,
    answers: row.answers ?? null,
  };
}

function rowToJob(row) {
  return {
    id: row.id,
    company: row.company,
    role: row.role,
    posting: row.posting ?? "",
    posting_text: row.posting_text ?? "",
    location: row.location ?? "",
    source: row.source ?? "manual",
    external_key: row.external_key ?? "",
    scan_score: row.scan_score,
    match_reason: row.match_reason ?? "",
    score_breakdown: row.score_breakdown ?? {},
    flags: row.flags ?? [],
    is_new: Boolean(row.is_new),
    dismissed: Boolean(row.dismissed),
    salary: row.salary ?? {},
    benefits: row.benefits ?? [],
    cv_md: row.cv_md ?? "",
    cv_analysis: row.cv_analysis ?? "",
    cv_generated_at: row.cv_generated_at ?? null,
    answers: row.answers ?? null,
    created_at: row.created_at,
  };
}

function jobToRow(userId, id, job) {
  return {
    user_id: userId,
    id,
    company: job.company,
    role: job.role,
    posting: job.posting ?? "",
    posting_text: job.posting_text ?? "",
    location: job.location ?? "",
    source: job.source ?? "manual",
    external_key: job.external_key ?? "",
    scan_score: job.scan_score ?? null,
    match_reason: job.match_reason ?? "",
    score_breakdown: job.score_breakdown ?? {},
    flags: job.flags ?? [],
    is_new: Boolean(job.is_new),
    dismissed: Boolean(job.dismissed),
    salary: job.salary ?? {},
    benefits: job.benefits ?? [],
    cv_md: job.cv_md ?? "",
    cv_analysis: job.cv_analysis ?? "",
    ...(job.answers !== undefined ? { answers: job.answers } : {}),
    updated_at: new Date().toISOString(),
  };
}

function appToRow(userId, id, app) {
  const status = app.status ?? "applied";
  return {
    user_id: userId,
    id,
    company: app.company,
    role: app.role,
    applied: app.applied,
    cv: app.cv ?? "",
    cv_md: app.cv_md ?? "",
    posting_text: app.posting_text ?? "",
    status,
    last: app.last ?? (status === "considering" ? null : app.applied),
    posting: app.posting ?? "",
    events: app.events ?? [],
    ...(app.job_id !== undefined ? { job_id: app.job_id } : {}),
    ...(app.answers !== undefined ? { answers: app.answers } : {}),
  };
}
