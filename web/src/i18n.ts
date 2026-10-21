import type { Env } from "./types";

/** Language for AI-written text the user reads (scan reasons, flags, CV analysis, notes). Set with JOBKIT_LANG. */
export function langName(env: Env): string {
  return env.JOBKIT_LANG === "pt-PT" ? "European Portuguese (pt-PT, not Brazilian)" : "English";
}

const PT: Record<string, string> = {
  "Check the non-compete / outside-activities clause": "Verificar cláusula de não concorrência / atividades externas",
  "Company type you asked to flag": "Tipo de empresa a sinalizar",
  "Score unavailable.": "Pontuação indisponível.",
  "Added manually": "Adicionada manualmente",
};

/** Translate the few fixed server-side labels (flags etc.). Unknown strings pass through. */
export function tr(env: Env, s: string): string {
  if (env.JOBKIT_LANG === "pt-PT") {
    if (PT[s]) return PT[s];
    const m = s.match(/^Confirm you can work remotely from (.+) \(local entity, EOR or contractor\)$/);
    if (m) return `Confirmar elegibilidade remota a partir de ${m[1]} (entidade local/EOR/contractor)`;
  }
  return s;
}
