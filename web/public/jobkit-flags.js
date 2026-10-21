/**
 * Standard scan flags in each UI language. Shared by the Worker (when a scan stores flags) and the page (which
 * re-translates stored flags at display time, so rows written in another language still read correctly).
 */
const PAIRS = [
  ["Check the non-compete / outside-activities clause", "Verificar cláusula de não concorrência / atividades externas"],
  ["Company type you asked to flag", "Tipo de empresa a sinalizar"],
  ["Score unavailable.", "Pontuação indisponível."],
  ["Added manually", "Adicionada manualmente"],
];
const REMOTE_EN = (where) => `Confirm you can work remotely from ${where} (local entity, EOR or contractor)`;
const REMOTE_PT = (where) => `Confirmar elegibilidade remota a partir de ${where} (entidade local/EOR/contractor)`;
const COUNTRY_PT = { PT: "Portugal", Portugal: "Portugal" };

export function localizeFlag(flag, lang = "en") {
  const s = String(flag ?? "").trim();
  if (!s) return s;
  const pt = lang === "pt-PT";
  for (const [en, p] of PAIRS) if (s === en || s === p) return pt ? p : en;
  let m = s.match(/^Confirm you can work remotely from (.+?) \(local entity, EOR or contractor\)$/i);
  if (m) return pt ? REMOTE_PT(m[1]) : s;
  m = s.match(/^Confirmar elegibilidade remota (?:a partir de |em |de )?(.+?)\s*\((?:entidade|entity)[^)]*\)$/i);
  if (m) {
    const where = COUNTRY_PT[m[1].trim()] || m[1].trim();
    return pt ? REMOTE_PT(where) : REMOTE_EN(where);
  }
  return s;
}
