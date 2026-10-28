# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Built on the Meta Model API (Muse Spark; SAM 3.1 planned). A server with a good connection does the work and sends the result to someone who can only receive text. A router (`evac/router.py`) lets Muse pick tools for any message; underneath are four modes: **emergency** (alert + GPS → instructions and a text map), **photo** (a photo that won't load → description + the photo drawn in text), **weather radar** (latest radar + 2-hour forecast as text) and **directions abroad** (walking route on street data). Each reply exists in four versions: SMS aligned for **iPhone Messages** (iPhone only, multi-part SMS is fine), a 58-column monospace map for WhatsApp, a small picture (400 px WebP) and a full JPEG.

## Commands

There is no build, lint or test suite. Python 3.9 (`/opt/anaconda3/bin/python3`); dependencies are `requests`, `pillow`, `numpy` (and `fontTools` for font checks).

```bash
python3 main.py --ask "Is it going to rain on my walk to the Colosseum?" --at 41.9010,12.5016 -v  # router
python3 main.py samples/palisades_fire.txt --at 34.0445,-118.5300 -v   # wildfire, raster map
python3 main.py samples/nyc_fire.txt --at 40.75491,-73.97441 -v        # Manhattan street-grid map
python3 main.py samples/nyc_gas_leak.txt --at 40.7560,-73.9740 -v      # street (not avenue) closure
python3 main.py samples/nyc_fire.txt --at 40.75491,-73.97441 --laptop 58  # + the WhatsApp map
python3 main.py --photo some.jpg                                          # photo mode
python3 main.py --radar 40.7128,-74.0060                                  # radar mode (live data)
python3 main.py --walk "Castelo de São Jorge, Lisbon" --at 38.7139,-9.1406 -v
python3 sim/server.py                                                     # simulator on :8765 (.claude/launch.json: "simulator")
```

`-v` prints the parsed alert (`notice (llm)` vs `notice (regex)` shows whether Muse ran), the distance check, closures found, and `text map: ... worst row drift X cells`. Drift must stay at or below ~0.05 cells (the 🔴 marker costs 0.04); that line is the regression check for the map.

`.env` (git-ignored, loaded by `evac/llm.py`) holds `LLAMA_API_KEY` (a Meta Model API key; `MODEL_API_KEY` also works). Optional: `LLM_BASE_URL` (default `https://api.meta.ai/v1`), `LLM_MODEL` (default `muse-spark-1.3`), `LLM_REASONING_EFFORT` (default `minimal`). With no key, everything falls back to regex parsing and a message template.

## Architecture

Request flow: `router.handle(message, you, image)` → Muse (`llm.chat_tools`) picks tools from `tools.schemas()` → `tools.call` runs them in parallel (never raises; errors go back to the model) → each tool returns a short JSON summary to the model and keeps its bulky result in `ctx.store[tool name]` → Muse writes the reply → `router._versions` turns the kept results into the versions (emergency reply > walking route > radar > photo). Emergencies always go through `emergency_reply` (= `pipeline.run`) and its text is used verbatim. Without a key, `_run_rules` routes on keywords.

**Scene and renderers:** `evac/scene.py:Scene` is what to draw (you, view size, text, roads, closures, zone, goal, route as lat/lon, aerial + masks, walk, street_grid, banner). `evac/render.py` draws a Scene as `sms` (textmap), `mono` (laptopmap), `small`/`picture` (imagemap). Renderers never fetch or route; every version draws `scene.route`. The emergency and directions modes build Scenes; radar and photo still render their own versions (next step: a raster layer in Scene). When changing a renderer, compare outputs with the model off (`llm._key = lambda: None`) against a saved baseline: SMS and pictures should stay byte-identical unless the change is meant to alter them.

`evac/pipeline.py:run` is the emergency flow: `notice.parse` → `geo.current_location` → `locate` (incident) → distance check (far = one line, return) → `grid.Grid` sized to the zone → aerial tiles + `segment` → `osm.fetch_roads` → `add_roads` (closures) → `add_hazard_circle` → `route` → `textmap.render_grid` or `render_raster` → `instructions` (Muse rewrite) → `compose`; with `laptop=N` it also calls `laptopmap.render` (N columns) and with `image=True` `imagemap.render`. `evac/photo.py`, `evac/radar.py` and `evac/directions.py` are the other modes; each returns `sms`, `text`, `mono` (+ bytes/gzip) and, except photo, `image`. `sim/server.py` exposes them as `/api/alert|photo|radar|directions` and the page renders every result with one `triple()` function.

Things that span files and are easy to break:

- **The model copies, code computes.** `notice.SCHEMA_PROMPT` asks Muse for numbers, units and street names exactly as written (`{"value": 2, "unit": "mi"}`, `avoid_roads: [{road, from, to}]`); `notice._normalize` converts to `zone_radius_m` and segment dicts. Letting the model convert made the radius differ run to run, which changed the map bbox and missed the road cache.
- **Muse Spark reasons before answering** and those tokens count toward `max_completion_tokens`. At default effort it spent 1,500 to 2,000 reasoning tokens (5 to 10 s) per small extraction; `minimal` gives the same answers in 1 to 2 s. An empty `content` with `finish_reason=length` means the limit was eaten by reasoning.
- **Overpass is the slow, flaky part** (30 s to 2 min, timeouts). `osm.fetch_roads` caches by an MD5 of the exact query in `cache/osm/`, so any change to bbox maths invalidates the cache. Mirrors are tried in order.
- **Street names are matched through `osm.norm_road`** ("5 Av" = "5th Avenue" = "Fifth Avenue", "W 47 St" = "East 47th Street"; directions and ordinals are dropped). Closures, intersections and the Manhattan grid all depend on it.
- **Intersections are found in road data, not the geocoder.** `pipeline.locate` splits "A and B", fetches roads around the rough geocode (or the user) and calls `osm.find_intersection`. Nominatim placed "5 Av and W 47 St" ~600 m off and returned nothing for Lexington & 51st.
- **Zone sizing:** no radius in the alert means a 150 m point incident (`DEFAULT_RADIUS_M`); the map half-width scales with the radius, so city incidents get ~25 to 65 m cells and wildfires ~600 m text cells. The "X m from you" wording uses distance to the incident for small zones and to the zone edge for large ones.
- **Three renderers share one route.** `textmap` (SMS), `laptopmap` (monospace) and `imagemap` (JPEG) all draw the same roads/zone/closures; `laptopmap` and `imagemap` take `center=None` for plain directions (no hazard, `shelter` = destination). `laptopmap` re-routes on its own grid; `imagemap` draws the path it is given (directions passes a finer 160-column route).
- **Overpass helpers:** `osm._overpass` caches every query; `fetch_places` (radar town labels) gets one quick try and `radar` skips it for 10 minutes after a timeout. `WALKABLE` excludes service roads and trail paths because big-city queries timed out with them.
- **Radar colours → rain:** RainViewer's free tiles (max zoom 7) have a fixed colour scheme; `radar.PALETTE` maps colours back to dBZ from RainViewer's published colour table. It has no forecast frames, so the 2-hour timeline comes from Open-Meteo.
- **`grid.Grid`** is a north-up raster in local metres with cell height = 2 × width (monospace aspect); routing is Dijkstra with per-cell costs (roads cheap, closures 60, cost multiplied deeper inside the hazard). It is used for routing and for the raster map, but not for the Manhattan map.

## Text maps (`evac/textmap.py`)

Messages renders texts in SF Pro (proportional). Only characters measured to be exactly one digit wide at 17 pt may repeat in a row: `= # + < > 0 $ ¤ £ ¥ ×` and U+2007 FIGURE SPACE as the blank. Narrow glyphs are padded to that width with thin/hair spaces (`TOKENS`: `v`, `|`); slightly wide ones (`Λ` +6%, `S` +1.2%) are allowed only once or twice per row and are listed in `DRIFT`. The "you" marker is the 🔴 emoji (17 px), padded to exactly two cells; `_place_you` gives it the next cell (`skip`). Small misalignment (a pixel or two) is acceptable; a whole cell is not. When adding a symbol, measure it first (browser canvas with `font: 17px system-ui`; `-apple-system` silently falls back to Times in Chrome) and add any width error to `DRIFT`.

- Words go only at the end of rows (street names) or in the legend, never inside the grid. Labels that would make a row exceed `BUBBLE_PT` are dropped, because a wrapped row destroys the map.
- `render_grid` is used when the incident is named at a Manhattan intersection (`is_grid_place`). It rotates coordinates by `GRID_BEARING` (29°), takes each avenue's/street's median position from OSM, and places everything by interpolation; avenue order comes from the hard-coded `AVENUES` list.
- Messages are UCS-2 (67 characters per SMS part); current replies are 9 to 12 parts.
- Alignment has only been checked in SF Pro on a Mac, not on a real iPhone.

## Monospace maps (`evac/laptopmap.py`, WhatsApp)

WhatsApp shows ```text``` in monospace but can't colour it. Only glyphs measured one cell wide in Menlo/Monaco/Courier are used: box drawing (`─━│┃┼╋╱╲╳`), shading (`░▒▓█`), arrows, `◆ ⌂ × ≈ ·`. Braille is avoided (falls back to a wider font on Mac). The 🔴 "you" is 1.66 cells in Menlo and takes two cells, leaving its row ~1/3 cell short. 58 columns is the widest that fits the simulated WhatsApp bubble (65% of the pane) without wrapping; narrow maps get a taller aspect. Manhattan is rotated 29° so avenues run up; street names are written on the street line, avenue names in a header. Side streets are dropped above 25 m per character unless walking; footpaths are routed on but not drawn.

## Conventions

- Every commit updates `README.md` with what the program does, how it works, and example inputs/outputs copied from real runs (re-run the samples; don't hand-write outputs). The commit message describes the same.
- NYC sample alerts are fictional drills and are labelled as such inside the alert text.
- Model and network failures always fall back (regex parse, template message, imagery-only routing) instead of blocking the reply.
