// GET /api/status: a few bytes the dashboard polls to learn whether a newer export exists,
// plus the latest scrape run per section for monitoring.
import { CACHE, failure, json, sql } from "./_db.js";

export async function GET() {
  if (!sql) return json({ error: "DATABASE_URL is not set on this deployment" }, 503);
  try {
    const [latest] = await sql`
      SELECT id, to_char(generated_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS generated_at
      FROM dashboard_exports ORDER BY id DESC LIMIT 1`;
    const runs = await sql`
      SELECT DISTINCT ON (section) section, status, found, new, price_changes, delisted, error,
             to_char(finished_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS finished_at
      FROM scrape_runs WHERE source <> 'demo'
      ORDER BY section, started_at DESC`;
    return json({ export_id: latest?.id ?? null, generated_at: latest?.generated_at ?? null, runs }, 200,
      { "Cache-Control": "public, max-age=0, s-maxage=60, stale-while-revalidate=600" });
  } catch (e) {
    return failure(e);
  }
}
