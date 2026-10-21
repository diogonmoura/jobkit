/** AI calls go through this Worker (/api/ai/chat), which uses the LLM key saved in Setup. The browser never sees the key. */
export const AI_CHAT_URL = "/api/ai/chat";
export const SETTINGS_URL = globalThis.__JOBKIT_SUPABASE?.auth?.settingsUrl || "/setup#llm";

const DEMO_TAILORED = `# Jane Doe
+351 900 000 000 | jane.doe@example.com | [linkedin.com/in/janedoe](https://www.linkedin.com/in/janedoe) | Porto, Portugal

## Professional Profile
Strategy and operations leader with 10+ years across consulting and technology.
This demo paragraph is re-weighted toward the posting keywords without inventing facts.

## Professional Experience

### Head of Strategy
Example Corp, Portugal | 2021 - Present
- **Team Leadership:** Leads a team of 6 across Portugal and Germany.
- **Transformation:** Ran a cost programme that cut annual spend by 30%.

## Education

### MSc Management
University of Porto, Porto | 2014 - 2016

## Skills
- **Strategy:** Corporate strategy, Operating models, PMO
- **Tools:** Power BI, n8n, Excel

## Languages
- Portuguese (native)
- English (fluent)
`;

export class AiError extends Error {
  constructor(message, { status, code, needsSettings } = {}) {
    super(message);
    this.name = "AiError";
    this.status = status;
    this.code = code;
    this.needsSettings = needsSettings;
  }
}

/**
 * Chat completion via the Worker. Calls onDelta(text) once with the full answer (the Worker does not stream).
 * Returns the assistant text.
 */
export async function streamChat(messages, { demo = false, signal, onDelta } = {}) {
  if (demo) {
    const text = DEMO_TAILORED;
    let out = "";
    for (let i = 0; i < text.length; i += 28) {
      if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
      const piece = text.slice(i, i + 28);
      out += piece;
      onDelta?.(piece);
      await new Promise((r) => setTimeout(r, 12));
    }
    return out;
  }
  const res = await fetch(AI_CHAT_URL, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ messages }),
    signal,
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = json?.error || `AI request failed (${res.status})`;
    throw new AiError(msg, { status: res.status, needsSettings: /no llm key|setup/i.test(msg), code: res.status === 412 ? "no_key" : undefined });
  }
  const text = String(json?.choices?.[0]?.message?.content ?? "").trim();
  if (!text) throw new AiError("Empty response from the AI model");
  onDelta?.(text);
  return text;
}
