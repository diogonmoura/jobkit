import type { Db } from "./db/types";
import { buildExperienceBlock } from "../public/jobkit-experience.js";

/** Enabled experience files for the user, capped for the prompt. */
export async function loadExperience(supabase: Db, userId: string) {
  const { data, error } = await supabase
    .from("jobkit_experience_files")
    .select("filename, content, enabled, uploaded_at, version_label, version_date")
    .eq("user_id", userId)
    .eq("enabled", true)
    .order("uploaded_at", { ascending: true });
  if (error) throw new Error(`experience files: ${error.message}`);
  return buildExperienceBlock(data ?? []);
}

/**
 * What scoring knows about the candidate. The uploaded experience files (newest first) are the basis, with the
 * master CV as a fallback when there are none; the free-text profile from Preferences is only an optional extra note.
 */
export async function loadScoringCandidate(supabase: Db, userId: string, notes: string): Promise<string> {
  const exp = await loadExperience(supabase, userId);
  let basis = exp.text;
  if (!basis) {
    const { data } = await supabase.from("jobkit_profile").select("master_cv").eq("user_id", userId).maybeSingle();
    basis = String(data?.master_cv ?? "");
  }
  return [
    basis.trim() ? `## Experience (uploaded files, newest first; newer facts win)\n${basis.trim().slice(0, 9000)}` : "",
    notes.trim() ? `## Extra notes from the candidate\n${notes.trim().slice(0, 3000)}` : "",
  ].filter(Boolean).join("\n\n");
}
