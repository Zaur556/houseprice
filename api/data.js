// GET /api/data                  manifest of the newest export: { export_id, parts, ... }
// GET /api/data?id=<id>&part=<k> one gzipped slice of that export's JSON
//
// The dashboard payload is stored as gzipped slices (dashboard_parts) so no single
// response comes near Vercel's 4.5 MB function limit, however large the data grows.
// Each slice goes out with Content-Encoding: gzip; the browser unzips it and the page
// joins the slices before parsing. Exports from before the split are served whole.
import { CACHE, failure, json, sql } from "./_db.js";

const IMMUTABLE = "public, max-age=31536000, s-maxage=31536000, immutable";

export async function GET(request) {
  if (!sql) return json({ error: "DATABASE_URL is not set on this deployment" }, 503);
  const q = new URL(request.url).searchParams;
  try {
    if (q.has("part")) return await part(q.get("id"), q.get("part"));
    return await manifest(request);
  } catch (e) {
    return failure(e);
  }
}

async function latest() {
  try {
    return await sql`
      SELECT id, parts, raw_bytes, gz_bytes, to_char(generated_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS generated_at,
             CASE WHEN parts IS NULL THEN payload END AS payload
      FROM dashboard_exports ORDER BY id DESC LIMIT 1`;
  } catch (e) {
    // 42703: the parts columns don't exist yet (deployed before the export code migrated the
    // table). Serve the old single-piece format rather than failing.
    if (e && e.code === "42703") return await sql`SELECT id, NULL AS parts, payload FROM dashboard_exports ORDER BY id DESC LIMIT 1`;
    throw e;
  }
}

async function manifest(request) {
  const [row] = await latest();
  if (!row) return json({ error: "No export published yet. Run python -m analytics.export." }, 404);
  const etag = `"export-${row.id}"`;
  const headers = { "Cache-Control": CACHE, ETag: etag, "X-Export-Id": String(row.id) };
  if (request.headers.get("if-none-match") === etag) return new Response(null, { status: 304, headers });
  if (row.parts == null) return json(row.payload, 200, headers);     // legacy single-row export
  return json({ export_id: row.id, parts: row.parts, raw_bytes: row.raw_bytes, gz_bytes: row.gz_bytes,
                generated_at: row.generated_at }, 200, headers);
}

async function part(idParam, partParam) {
  const id = Number(idParam), k = Number(partParam);
  if (!Number.isInteger(id) || !Number.isInteger(k) || id < 1 || k < 0) return json({ error: "Bad id or part" }, 400);
  const [row] = await sql`SELECT data FROM dashboard_parts WHERE export_id = ${id} AND part = ${k}`;
  if (!row) return json({ error: "No such export part (it may have been replaced by a newer export)" }, 404);
  // a given export id never changes, so its parts can be cached for good
  return new Response(Buffer.from(row.data, "base64"), {
    status: 200,
    headers: { "Content-Type": "application/json; charset=utf-8", "Content-Encoding": "gzip", "Cache-Control": IMMUTABLE },
  });
}
