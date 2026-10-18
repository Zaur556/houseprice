import { neon } from "@neondatabase/serverless";

// Vercel's Neon integration sets DATABASE_URL (and POSTGRES_URL); either works.
const url = process.env.DATABASE_URL || process.env.POSTGRES_URL;
export const sql = url ? neon(url) : null;

// Five minutes at the edge, then serve stale while one request refreshes it.
// Exports land every 6 hours, so viewers are never more than ~5 minutes behind.
export const CACHE = "public, max-age=0, s-maxage=300, stale-while-revalidate=86400";

export function json(body, status = 200, headers = {}) {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...headers },
  });
}

// 42P01: the scraper/export has not created dashboard_exports yet.
export function failure(e) {
  if (e && e.code === "42P01") return json({ error: "No export published yet. Run python -m analytics.export." }, 404);
  console.error(e);
  return json({ error: "Database unavailable" }, 503);
}
