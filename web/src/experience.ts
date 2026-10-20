import type { SupabaseClient } from "@supabase/supabase-js";
import { buildExperienceBlock } from "../public/jobkit-experience.js";

/** Enabled experience files for the user, capped for the prompt. */
export async function loadExperience(supabase: SupabaseClient, userId: string) {
  const { data, error } = await supabase
    .from("jobkit_experience_files")
    .select("filename, content, enabled, uploaded_at, version_label, version_date")
    .eq("user_id", userId)
    .eq("enabled", true)
    .order("uploaded_at", { ascending: true });
  if (error) throw new Error(`experience files: ${error.message}`);
  return buildExperienceBlock(data ?? []);
}
