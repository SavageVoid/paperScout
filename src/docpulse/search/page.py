"""Day 4: a one-page UI.

Self-contained on purpose — no CDN, no build step, no framework. It is served by
the same process that answers /search, so if the page loads, the API is up.
"""

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DocPulse search</title>
<style>
  :root { color-scheme: light dark; --fg:#1b1b1f; --muted:#6b6b76; --line:#e3e3e8; --bg:#fbfbfd; --accent:#2050d0; }
  @media (prefers-color-scheme: dark) {
    :root { --fg:#e8e8ee; --muted:#a0a0ad; --line:#2b2b33; --bg:#16161a; --accent:#7aa2ff; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, sans-serif; }
  main { max-width: 820px; margin: 0 auto; padding: 48px 20px 80px; }
  h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.01em; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 26px; }
  form { display: flex; gap: 8px; flex-wrap: wrap; }
  input[type=search], input[type=text] {
    flex: 1 1 320px; padding: 11px 13px; font-size: 15px; color: var(--fg);
    background: transparent; border: 1px solid var(--line); border-radius: 9px; }
  input:focus { outline: 2px solid var(--accent); outline-offset: 1px; border-color: transparent; }
  button { padding: 11px 18px; font-size: 15px; border-radius: 9px; border: 1px solid transparent;
           background: var(--accent); color: #fff; cursor: pointer; }
  button:disabled { opacity: .55; cursor: default; }
  .filters { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
  .filters input { flex: 0 1 160px; padding: 7px 10px; font-size: 13px;
                   border: 1px solid var(--line); border-radius: 8px; background: transparent; color: var(--fg); }
  .meta { color: var(--muted); font-size: 12.5px; margin: 18px 0 8px; min-height: 18px; }
  ol { list-style: none; padding: 0; margin: 0; }
  li { padding: 16px 0; border-top: 1px solid var(--line); }
  li:first-child { border-top: none; }
  .titlerow { display: flex; gap: 10px; align-items: baseline; justify-content: space-between; }
  .title { font-weight: 600; }
  .score { color: var(--muted); font-variant-numeric: tabular-nums; font-size: 13px; white-space: nowrap; }
  .abs { color: var(--muted); font-size: 13.5px; margin-top: 4px;
         display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
  .tags { color: var(--muted); font-size: 12px; margin-top: 6px; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .err { color: #c0392b; font-size: 13.5px; }
  footer { margin-top: 40px; color: var(--muted); font-size: 12px; }
</style>
</head>
<body>
<main>
  <h1>DocPulse search</h1>
  <div class="sub">Semantic search over arXiv abstracts &middot; <span id="model">loading model info&hellip;</span></div>

  <form id="form" autocomplete="off">
    <input id="q" type="search" placeholder="e.g. sparse attention for long documents" required minlength="1">
    <button id="go" type="submit">Search</button>
  </form>

  <div class="filters">
    <input id="category" type="text" placeholder="category, e.g. cs.LG">
    <input id="since" type="text" placeholder="since YYYY-MM-DD">
    <input id="until" type="text" placeholder="until YYYY-MM-DD">
  </div>

  <div class="meta" id="meta"></div>
  <ol id="results"></ol>

  <footer>Vectors: all-MiniLM-L6-v2 (384d, unit length). Scores are cosine similarity.</footer>
</main>

<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));

function params() {
  const p = new URLSearchParams({ q: $("q").value.trim(), limit: "10", include_abstract: "true" });
  for (const k of ["category", "since", "until"]) if ($(k).value.trim()) p.set(k, $(k).value.trim());
  return p;
}

async function run(event) {
  if (event) event.preventDefault();
  const q = $("q").value.trim();
  if (!q) return;
  $("go").disabled = true;
  $("meta").textContent = "searching\\u2026";
  $("results").innerHTML = "";
  try {
    const res = await fetch("/search?" + params().toString());
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || res.statusText);
    $("meta").textContent = body.n_returned + " results in " + body.took_ms + " ms";
    $("results").innerHTML = body.results.map((r) => {
      const cats = r.categories ? esc(r.categories) : "";
      const date = r.published ? esc(r.published.slice(0, 10)) : "";
      const title = '<a href="https://arxiv.org/abs/' + encodeURIComponent(r.arxiv_id) + '" target="_blank" rel="noopener">'
                    + esc(r.title || r.arxiv_id) + "</a>";
      return '<li><div class="titlerow"><span class="title">' + title + '</span>'
           + '<span class="score">' + r.score.toFixed(3) + '</span></div>'
           + (r.abstract ? '<div class="abs">' + esc(r.abstract) + '</div>' : "")
           + '<div class="tags">' + esc(r.arxiv_id) + (date ? " &middot; " + date : "")
           + (cats ? " &middot; " + cats : "") + "</div></li>";
    }).join("");
    if (!body.results.length) $("meta").textContent = "no results";
  } catch (err) {
    $("meta").innerHTML = '<span class="err">' + esc(err.message) + "</span>";
  } finally {
    $("go").disabled = false;
  }
}

$("form").addEventListener("submit", run);

fetch("/health").then((r) => r.json()).then((h) => {
  const m = h.model || {};
  $("model").textContent = (m.model_id || "model") + " @ " + String(m.model_revision || "").slice(0, 7)
                         + " \\u00b7 " + (h.n_vectors ?? 0) + " vectors";
}).catch(() => { $("model").textContent = "health check failed"; });
</script>
</body>
</html>
"""
