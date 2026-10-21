import type { Env } from "./types";
import { localizeFlag } from "../public/jobkit-flags.js";

/** Language for AI-written text the user reads (scan reasons, flags, CV analysis, notes). Set with JOBKIT_LANG. */
export function langName(env: Env): string {
  return env.JOBKIT_LANG === "pt-PT" ? "European Portuguese (pt-PT, not Brazilian)" : "English";
}

/** Translate the fixed server-side labels (standard flags etc.) into JOBKIT_LANG. Unknown strings pass through. */
export function tr(env: Env, s: string): string {
  return localizeFlag(s, env.JOBKIT_LANG === "pt-PT" ? "pt-PT" : "en");
}
