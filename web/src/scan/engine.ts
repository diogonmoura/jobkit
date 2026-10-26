import type { Db } from "../db/types";
import type { RawOpening } from "../types";

/**
 * Postings found by the jobkit engine (the Python CLI) on boards this Worker can't read, such as
 * SuccessFactors or Workday. The engine upserts them into jobkit_external_openings with the service key;
 * each scan reads them, scores them like its own, and deletes the rows it read, so each is considered once.
 */

const TABLE = "jobkit_external_openings";
// A scan scores a bounded batch; the rest waits for the next run.
const MAX_PER_RUN = 200;

type ExternalRow = {
  external_key: string;
  company: string;
  title: string;
  url: string;
  description?: string | null;
  location?: string | null;
  source?: string | null;
};

export function engineOpening(row: ExternalRow): RawOpening {
  return {
    externalKey: String(row.external_key),
    company: String(row.company),
    title: String(row.title),
    url: String(row.url),
    location: String(row.location ?? ""),
    description: String(row.description ?? ""),
    source: "engine",
    vendor: String(row.source || "engine").split(":")[0],
  };
}

export async function loadEngineOpenings(supabase: Db, userId: string): Promise<RawOpening[]> {
  const { data, error } = await supabase
    .from(TABLE)
    .select("external_key, company, title, url, description, location, source")
    .eq("user_id", userId)
    .limit(MAX_PER_RUN);
  if (error) return []; // an older database without the table: scan as before
  return (data ?? []).map((row) => engineOpening(row as ExternalRow));
}

export async function clearEngineOpenings(supabase: Db, userId: string, openings: RawOpening[]): Promise<void> {
  if (!openings.length) return;
  await supabase
    .from(TABLE)
    .delete()
    .eq("user_id", userId)
    .in("external_key", openings.map((o) => o.externalKey));
}
