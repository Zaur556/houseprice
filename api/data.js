// GET /api/data: the newest dashboard payload written by `python -m analytics.export`.
import { CACHE, failure, json, sql } from "./_db.js";

export async function GET(request) {
  if (!sql) return json({ error: "DATABASE_URL is not set on this deployment" }, 503);
  let row;
  try {
    [row] = await sql`SELECT id, payload FROM dashboard_exports ORDER BY id DESC LIMIT 1`;
  } catch (e) {
    return failure(e);
  }
  if (!row) return json({ error: "No export published yet. Run python -m analytics.export." }, 404);

  const etag = `"export-${row.id}"`;
  const headers = { "Cache-Control": CACHE, ETag: etag, "X-Export-Id": String(row.id) };
  if (request.headers.get("if-none-match") === etag) return new Response(null, { status: 304, headers });
  return json(row.payload, 200, headers);
}
