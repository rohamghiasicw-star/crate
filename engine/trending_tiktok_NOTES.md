# trending_tiktok.py - research notes (2026-08-03)

Goal: real TikTok trending sounds for the Trending tab, replacing the SoundCloud
New & Hot feed Roham rejected. Everything below was tested live on 2026-08-03.

## What won

Two-source merge, max 2 HTTP requests per fetch (fits the 6h server cache):

1. **tokchart.com homepage** (primary, ranks 1-4/5)
   Independent live TikTok sound chart built from their own crawler. The free
   homepage exposes the top ~5 ranked sounds: real TikTok sound IDs, song
   title + artists, videos-made count (real "uses"), growth, first-seen date.
   Rows 6+ are paywalled placeholders ("Premium Track" / "Hidden Song Title") -
   the module detects and skips them, never parses bait. It also skips the
   sponsored "Ad" row that sits at the top with a fake 999 score.
   Sound URLs: built as `https://www.tiktok.com/music/x-<sound_id>` - the slug
   is decorative for the client router. Verified in a real browser: the
   x-slug URL for sound 7659226077998697223 loaded "original sound -
   mordarbll", 489.3K videos, full video grid. (curl sees a 43KB JS shell for
   x-slugs, full SSR only for canonical slugs - browser users are fine.)

2. **Apple Music playlist `pl.35b7d6e334854d1585237d106e69bdc2`**
   ("TikTok Songs 2026 | Viral Internet Hits", Topsify Global) - fills to
   `limit`. 101 tracks, verified actively maintained (current viral: Bruno
   Mars "Risk It All", PinkPantheress "Stateside", Tame Impala x JENNIE
   "Dracula (JENNIE Remix)", IShowSpeed "Champions (WC 26)", Ariana "hate
   that i made you love me"). The page embeds the full track list as JSON in
   `<script id="serialized-server-data">` - no auth, no key, no rate limit
   pain. Gives title, artist, 300x300 art, per-song Apple URL. Storefront
   follows the `region` arg (`us` fallback if a storefront 404s).

Merged list: tokchart rows first (they are the literal "trending sounds right
now" with counts + TikTok sound pages), Apple fills the rest, deduped by
normalized title+artist, re-ranked 1..limit.

## Why not the TikTok Creative Center (the thing Roham actually asked for)

**It no longer exists.** Full chain of evidence:

- Bare `creative_radar_api/v1/popular_trend/sound/rank_list` -> 40101 "no
  permission", as expected.
- Found the signing scheme in the npm package `tiktok-user-sign` v4.0.0
  (referenced by github.com/techgokdeniz/tiktok-creative-center-api):
  `user-sign = digitfold16(md5("A7B&9z#1G6$2K@8M!3-<anonymous-user-id>-<timestamp>"))`
  where digitfold16 XORs hex digit i with digit i+16. Headers:
  `anonymous-user-id` (any v4 uuid), `timestamp` (unix secs), `user-sign`.
  Implemented in stdlib Python, sent live: **the auth now passes** - response
  became `{"code":0,"msg":"deprecated"}` with empty data. Same "deprecated"
  for hashtag/list. v2 paths and music/song variants -> 404.
- The Creative Center web UI 302s to `ads.tiktok.com/creative/creativeCenter/
  trends` - the new "TikTok One Creative Suite". Downloaded all 39 JS chunks
  of its trends routes: the tab set is hashtag / creator / video only.
  **There is no music/sound tab in TikTok One at all.** The Popular Music
  chart was killed with the migration, which is also why every public mirror
  that proxied it died (see countik below).
- Working signing code is kept in the module docstring's history and here, in
  case TikTok ever revives a sound endpoint behind the same gate.

## Every other avenue tried

| Avenue | Result |
|---|---|
| countik.com/popular/songs | Found the real XHR by unpacking their Nuxt chunks: `https://countik.com/api/hot/music/{CC}?page=1&limit=20`. Returns 200 but `music_list: []` for US, GB, DE, ID, BR - their upstream was the dead Creative Center API. Dead mirror. |
| Apple Music "TikTok Viral" by TikTok (official curator, `pl.7a22269606dc4f67852db5baf6b6830c`) | Fetches fine, 47 tracks - but frozen mid-2021 (MONTERO, drivers license, Buss It). TikTok stopped curating it. Rejected as fake-trending. |
| TikTok Billboard Top 50 (the official US chart) | billboard.com/charts/tiktok-billboard-top-50/ -> 404. Discontinued March 7, 2025 after 18 months. |
| artists.tiktok.com/charts (TikTok for Artists) | 200 but a JS shell; pulled its bundles - only login-gated `artist_api/ttfa/*` song-data endpoints, no public chart API. |
| tikwm.com/api/feed/list (trending feed) | Works keyless, real data, but the feed's sounds are ~90% "original sound - username" noise, not a music chart. Kept out. |
| tokchart deeper (pagination/other pages) | /weekly /charts /songs all 404; sitemap is help-only; Livewire replay would re-render with guest entitlements (rows 6+ stay blurred server-side). Free top-5 is the ceiling. |
| urlebird.com | 403 Cloudflare. |
| tokboard.com, tikrank.com | Dead (connection failure). |
| trendsounds.com | It's a wedding DJ company. |
| exolyt.com/music | 404; rest of site login-gated. |
| viberate.com | Free charts are TikTok *artist* rankings (followers/likes), no songs chart. |
| IG Reels trending audio (secondary ask) | creators.instagram.com/trends -> HTTP 400 without login; the in-app professional dashboard's trending audio has no public/anon surface. Login-gated, noted and dropped. |
| GitHub scrapers (slayhop-trending, xbeaulac/tiktok-gen, blakelaw/TikTokData) | All harvest the signed headers with Playwright against the OLD Creative Center page - all dead with the endpoint. Useful only for confirming the header names. |

## Reliability / fragility

- **tokchart**: HTML scrape of a Laravel/Livewire page - the most fragile
  piece. Parser is defensive (paywall markers, Ad row, catalog vs UGC cell
  layouts) and the module works Apple-only if tokchart breaks (fetch() only
  raises when BOTH sources fail). Their free tier could shrink below 5 rows.
- **Apple playlist**: very stable page format (serialized-server-data has
  been stable for years); the real risk is Topsify abandoning the playlist,
  like TikTok abandoned theirs. Freshness sanity check: if the fill rows ever
  look stale, swap `APPLE_PLAYLIST_ID` - candidates verified fetchable:
  `pl.753bd53cfa6142669d68b1da4554da7f` (Mega Hits "TikTok Viral Hits 2026"),
  `pl.d9dcaa71eae146549c216c6fc81640bd` (Filtr "TikTok Songs <month> 2026",
  monthly refresh). One-line change.
- No keys, no paid APIs, no Playwright, stdlib only, Python 3.9 clean.

## Integration notes (for server.py, done by Roham's side later)

- `from trending_tiktok import fetch` then `rows = fetch(20, 'US')` inside
  the existing 6h `_TREND` cache block. Signature and row keys match the
  brief; extra keys included: `src` ('tokchart'|'applemusic'), `sound_type`
  (tokchart rows), `tiktok_search` (Apple rows - a tiktok.com search URL if
  the UI prefers keeping users on TikTok instead of Apple Music).
- `plays_or_uses` is videos-made-with-sound for tokchart rows, `None` for
  Apple rows - the UI should hide the count when None, not print 0.
- fetch() raising = both sources down; keep serving the last cached rows
  (the current /trending handler already does exactly that).
- tokchart `art` is '' (their thumbnails are Alpine.js lazy-loaded, not in
  HTML). Apple rows all carry 300x300 art. If art for the top rows matters,
  the tiktok music page oEmbed/SSR could backfill it, at +1 request per row.
