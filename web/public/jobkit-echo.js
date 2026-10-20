/**
 * Shared (Worker + browser): detect sentences that copy distinctive phrases from the job posting.
 * A "distinctive" 4-gram has at least two non-stopword tokens and is not made only of proper nouns / tool names.
 */
const STOP = new Set(("a an the and or but of to in on at for with by from as is are was were be been being this that these those it its " +
  "we you your our their they he she his her i my me us them who whom which what when where why how all any both each few more most " +
  "other some such no nor not only own same so than too very can will just should now into over under about across within through " +
  "per via also up out off then there here do does did done have has had having would could may might must shall").split(/\s+/));

const words = (s) => (String(s).match(/[\p{L}\p{N}][\p{L}\p{N}'&+.-]*[\p{L}\p{N}]|[\p{L}\p{N}]/gu) || []);

function grams(tokens, n = 4) {
  const out = [];
  for (let i = 0; i + n <= tokens.length; i++) out.push(tokens.slice(i, i + n));
  return out;
}

/** Proper-noun-ish token: capitalised mid-text in the posting (e.g. Acme, Python, OpenAI). */
function properSet(text) {
  const set = new Set();
  const sentences = String(text).split(/(?<=[.!?])\s+|\n+/);
  for (const s of sentences) {
    const w = words(s);
    w.forEach((t, i) => { if (i > 0 && /^\p{Lu}/u.test(t)) set.add(t.toLowerCase()); });
  }
  return set;
}

function distinctive(g, proper) {
  const content = g.filter((t) => !STOP.has(t));
  if (content.length < 2) return false;
  if (content.every((t) => proper.has(t))) return false;
  return true;
}

/** Split prose into sentences, ignoring Markdown headings and the contact line. */
export function proseSentences(md) {
  const lines = String(md).split("\n").filter((l) => l.trim() && !/^#/.test(l.trim()) && !/@|\|.*\|/.test(l));
  return lines
    .map((l) => l.replace(/^\s*[-*]\s+/, "").replace(/\*\*/g, ""))
    .flatMap((l) => l.split(/(?<=[.!?;])\s+/))
    .map((s) => s.trim())
    .filter((s) => words(s).length >= 4);
}

// Also catch close paraphrases: 3-grams of content words (stopwords removed), e.g. "strategic depth hands-on".
function contentGrams(text, proper) {
  const t = words(text).map((x) => x.toLowerCase()).filter((x) => !STOP.has(x));
  return grams(t, 3).filter((g) => !g.every((x) => proper.has(x))).map((g) => "~" + g.join(" "));
}

export function echoCheck(outputText, postingText) {
  const proper = properSet(postingText);
  const post = new Set([
    ...grams(words(postingText).map((t) => t.toLowerCase())).filter((g) => distinctive(g, proper)).map((g) => g.join(" ")),
    ...String(postingText).split(/(?<=[.!?;])\s+|\n+/).flatMap((s) => contentGrams(s, proper)),
  ]);
  const sentences = proseSentences(outputText);
  let total = 0, shared = 0;
  const hits = [];
  for (const s of sentences) {
    const gs = [...grams(words(s).map((t) => t.toLowerCase())).filter((g) => distinctive(g, proper)).map((g) => g.join(" ")), ...contentGrams(s, proper)];
    total += gs.length;
    const m = [...new Set(gs.filter((g) => post.has(g)))];
    shared += gs.filter((g) => post.has(g)).length;
    if (m.length) hits.push({ sentence: s, grams: m });
  }
  return { score: total ? shared / total : 0, shared, total, hits };
}

export const ANTI_ECHO_RULES = [
  "Identity comes from the candidate's real roles and titles. Never call the candidate by the target job title (or a near variant) unless they have actually held it; describe who they are from their real roles, then position them toward the role.",
  "Never copy or closely paraphrase the job posting. Do not reuse any distinctive phrase of three or more words from it. Use the posting only to decide which of the candidate's real evidence to emphasise and in what order.",
  "Show fit through concrete evidence in the candidate's own voice (numbers, scope, tools, outcomes), not by mirroring the posting's adjectives.",
  "Avoid buzzword stacks and generic claims such as 'proven ability to', 'cross-functional', 'multiple domains', 'commercial outcomes', 'strategic depth', 'hands-on execution' unless immediately backed by a specific fact.",
];

/** True when the text calls the candidate by the target title they have not held (checked against their own materials). */
export function titleEcho(text, targetRole, ownMaterials) {
  const role = String(targetRole || "").split(/[-–—,(|]/)[0].trim().toLowerCase();
  if (!role || role.split(/\s+/).length < 2) return "";
  const own = String(ownMaterials || "").toLowerCase();
  return String(text).toLowerCase().includes(role) && !own.includes(role) ? role : "";
}
