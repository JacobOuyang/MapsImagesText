# MapsImagesText

**Pictures for people who can only receive text.** A server with a good connection does the heavy work (reading an emergency alert, looking at a photo, reading the weather radar, routing on street data) and sends the result back as text that still works as a picture: a map or sketch drawn in characters. The same reply exists in four sizes, and the person gets the richest one their connection can carry.

| Version | Channel | What arrives | Typical size |
| --- | --- | --- | --- |
| **Low data** | SMS on an iPhone | the message + a small map in characters that line up in iPhone Messages | 6 to 12 SMS parts (~1 to 1.7 KB) |
| **Medium data** | WhatsApp (any app that shows monospace text) | the message + a 58-column monospace map | 2 to 5 KB of text, 0.4 to 1.1 KB compressed |
| **Small picture** | a slow connection | a 400 px WebP map drawn for that size, or the photo/radar shrunk | 4.5 to 23 KB |
| **High data** | a working connection | an ordinary picture map, or the photo itself | 50 to 180 KB |

Ask it anything in one message; **Muse Spark** decides which tools to call:

- "Is it going to rain on my walk to the Colosseum?" → radar + walking route → one reply with the route map
- an emergency alert (fire, gas leak, wildfire, flood) → the fixed, tested emergency reply: are you affected, what to avoid, which way out
- "my friend sent me this, what is it?" + a photo that won't download → a description + the photo drawn in text
- "How far is the Trevi Fountain?" → a one-line answer

Every map and drawing shows **only what the reply is about**: the route, the streets it uses, the cross streets at its turns, the closures and the zone's edge. Everything else is left blank, so the way to go is the first thing you see. A photo becomes the outline of its main subject. A walk is re-sent as you move: one line of text while you're on track, and a new map of just the next stretch at a turn or after a wrong turn.

Built on the Meta Model API (Muse Spark; SAM 3.1 planned).

Contents: [Architecture](#architecture) · [The router](#the-router-one-message-in-muse-picks-the-tools) · [Tools](#tools) · [Scene and renderers](#scene-one-description-four-drawings) · [Only what matters](#showing-only-what-matters) · [Emergency](#emergency-alerts) · [Photo](#photos-that-wont-load) · [Radar](#weather-radar) · [Directions](#walking-directions-abroad) · [Text-map rules](#why-the-text-maps-line-up) · [Simulator](#try-it-the-simulator) · [Command line](#run-it-from-the-command-line) · [Code](#code) · [Limits](#known-limits)

## Architecture

```mermaid
flowchart LR
  subgraph In["In"]
    M["Message + GPS<br/>(+ photo)"]
  end
  subgraph Router["evac/router.py"]
    MU["Muse Spark<br/>picks tools"]
    RUN["Run tool calls<br/>in parallel"]
    TXT["Muse writes<br/>the reply text"]
  end
  subgraph Tools["evac/tools.py (10 tools)"]
    T1[emergency_reply]
    T2[walking_route]
    T3[rain_radar]
    T4[describe_photo]
    T5["geocode, streets,<br/>nearby_towns, ..."]
  end
  subgraph Store["ctx.store"]
    S["bulky results:<br/>routes, roads, radar grids,<br/>a whole emergency reply"]
  end
  SC["Scene<br/>(evac/scene.py)"]
  subgraph Render["evac/render.py"]
    R1["sms()"]
    R2["mono()"]
    R3["small()"]
    R4["picture()"]
  end
  M --> MU --> RUN --> Tools
  Tools -- "short JSON" --> MU
  Tools --> S --> SC
  MU --> TXT --> SC
  SC --> R1 & R2 & R3 & R4
  R1 --> O1["Low: SMS"]
  R2 --> O2["Medium: WhatsApp text"]
  R3 --> O3["Small: 400 px WebP"]
  R4 --> O4["High: JPEG"]
```

Three ideas hold it together:

1. **Tools return two things.** A short JSON summary goes back to Muse (a distance, the steps, "no rain in the next 2 hours"). The bulky result (a street network, a route, a radar grid, a whole emergency reply) stays in the request's `ctx.store`. The model never has to read thousands of road points, and what gets drawn is exactly what the tools found.
2. **One Scene, four drawings.** Whatever was asked, the result becomes one `Scene`: where you are, the roads, closures, danger zone, the goal (a shelter or a destination), the route, terrain and the text. Each version is drawn from that same Scene by `evac/render.py`, so the SMS map, the WhatsApp map and the pictures always show the same route.
3. **The model plans and words; code computes and draws.** Muse chooses tools and writes the message. Distances, routes, closures, every map and legend are code. For emergencies the model only routes the alert to the fixed, tested reply and never rewrites it.

Everything slow or flaky is cached (`cache/osm/` for road data) or has a fallback: no model key means keyword routing, regex alert parsing and template text; a failed tool comes back to the model as an error it can work around; no road data means routing on imagery only.

## The router: one message in, Muse picks the tools

```mermaid
sequenceDiagram
  participant P as Person (text + GPS)
  participant R as router.handle
  participant M as Muse Spark
  participant T as tools.call
  participant S as Scene + render
  P->>R: "Is it going to rain on my walk to the Colosseum?"
  R->>M: system prompt + message + 10 tool descriptions
  M-->>R: tool_calls: rain_radar(), walking_route("Colosseum, Rome")
  par in parallel
    R->>T: rain_radar
    T-->>R: rain here: none, nearest 31 km NE, forecast: no rain
  and
    R->>T: walking_route
    T-->>R: 1479 m, 20 min, steps
  end
  R->>M: tool results (short JSON)
  M-->>R: reply text (no more tool calls)
  R->>S: walking-route Scene + Muse's text
  S-->>P: SMS · WhatsApp text · small picture · picture
```

`router.handle(message, you, image=None)`:

1. **Plan.** Muse Spark gets a system prompt (the person's GPS, whether a photo is attached, "keep every number exactly", "never list every forecast value", "for any emergency alert call `emergency_reply` and nothing else") and the tool descriptions. It answers with tool calls. Up to 3 rounds, so it can look something up and then act on it.
2. **Run.** Tool calls in a round run in parallel threads (radar and routing each take seconds). Each result goes back to Muse as a `tool` message, truncated to 4,000 characters.
3. **Write.** When Muse stops calling tools, its reply is the message text. For an emergency the fixed reply's text is used verbatim instead.
4. **Draw.** The router picks what to draw from what the tools kept, in this order: an emergency reply, a walking route, the radar, a photo. Map scenes are drawn by `evac/render.py`; radar and photos still bring their own four versions until they become Scenes too.

Without a model key, keyword rules pick one tool (`_run_rules`): a photo → `describe_photo`; a long text mentioning an alert, fire, flood, leak or evacuation → `emergency_reply`; "rain", "radar" or "weather" → `rain_radar`; "walk to X" or "directions to X" → `walking_route`.

### Real runs

Rome Termini, "Is it going to rain on my walk to the Colosseum?" (20.6 s; map: walking route). The SMS version, then the WhatsApp map: only the streets the route uses or crosses are drawn, and the numbers mark where each step starts:

```
tools called: rain_radar(), walking_route(Colosseum, Rome)

No rain in the next 2 hours.
Your walk to Colosseum is 1479 m, about 20 min.
Head SW on Via Giovanni Giolitti ~690 m.
Then head SW on unnamed road ~780 m.
Colosseum is 1377 m SW of you.
Dry walk expected.

MAP (north is up):
         = 🔴   
       == v <   
       v <<<    
     <<       
    v <         
  =<          
  S==         

= open road
< > Λ v route
🔴 you
S destination: Colosseum

┌─ ↑ N ────────────────────────────────────────────────────┐
│                                                ╱╱        │
│                                             🔴╱╱         │
│                                            ╲←╱           │
│                                          ╲╲←↓            │
│                                           ←↓╱            │
│                                      ╲   ←↓╱             │
│                                 ╲╲╲╲  ╲←←↓╱              │
│            Via dell'Esquilino ╲╲←←←←←←←↓╱                │
│                             ╲╲ ←↓╲╲╲╲╳╋╱                 │
│                Via Liberiana ╲←↓    ╲╳ Via Gioberti      │
│                              ←↓╳╲╲                       │
│                            ←←↓   ╲                       │
│                          ←←2                             │
│                        ←←↓                               │
│                      ←←↓                                 │
│                     ←↓                                   │
│                    ←↓                                    │
│                   ←↓                                     │
│                  ←↓                                      │
│                ←←↓                                       │
│               ←↓                                         │
│             ╱↙╱                                          │
│            ╱↙                                            │
│            ←↓                                            │
│           ←↓━━━                                          │
│         ╱─⌂───╲╲                                         │
│        │╱      ╲╲│ Colosseo                              │
└─ ├───────┤ 200 m ────────────────────────────────────────┘

🔴 you
→ route   1 2 3 steps
⌂ destination
━ main road   ─ street
```

Rome Termini, "How far is the Trevi Fountain?":

```
tools called: geocode(Trevi Fountain)

Trevi Fountain is 1516 m to the W.
About a 20 min walk from you.

1.9 s; map: None
```

A photo attached, "my friend sent me this, what is it?" (5.9 s). The flamingos are a small part of the photo, so the drawing zooms in on them:

```
tools called: describe_photo()

A large flock of pink flamingos wades through shallow, misty water in soft golden light.
About two dozen standing together.
Hazy sunrise or sunset glow with mist over the water.

    #
 ###### #######
 ###  #########
(outline of the main subject, zoomed in: left flamingos, central flamingos, right flamingos)

            ────
  ──────────   ─╲   ─        ────
 │╱             ╲────     ───   ─────────────────
  ╲           ───│  ╲   ──                      ╲
  │          ╱   ╲  ╲╲──                        │
  ╲╲ ─────  ╱    ╲╲──│  ───────────             │
   ╲─    ───╱        ───          ────────   ───╱
                                         ────
(outline of the main subject, zoomed in: left flamingos, central flamingos, right flamingos)
```

Before building the router, Muse's tool choice was tested on seven requests: it picked the right tools every time, in 0.6 to 1.3 s per decision.

## Tools

`evac/tools.py`. A function becomes a tool with one decorator line: a plain-language description and its parameters, which become the JSON schema Muse reads.

```python
@tool("Walking route from the person to a destination along real streets and footpaths, with turn-by-turn steps.",
      destination=("string", "Where to, e.g. 'Colosseum, Rome'", True))
def walking_route(ctx, destination):
    ...
    ctx.store["walking_route"] = res          # bulky: kept for the Scene
    return {"distance_m": ..., "minutes": ..., "steps": ...}   # short: goes to the model
```

| Tool | What it returns to the model | What it keeps for drawing | Backed by |
| --- | --- | --- | --- |
| `emergency_reply(text)` | affected?, inside the zone?, the message, steps | the whole emergency reply (all four versions) | `pipeline.run` |
| `read_alert(text)` | hazard, order, place, radius as written, shelters, closed segments | the parsed alert | `notice.parse` (Muse, regex fallback) |
| `locate_incident(place)` | lat/lon, distance, direction | | `pipeline.locate` (intersections from road data) |
| `walking_route(destination)` | distance, minutes, steps, where the destination is | the walking-route Scene | `directions.run` |
| `streets(radius_m, walking)` | how many streets, the main ones | the street network | Overpass |
| `geocode(query)` | lat/lon, distance, direction | | Nominatim |
| `nearby_towns(radius_km)` | towns with distance and direction | the town list | Overpass |
| `rain_radar()` | rain over you, nearest rain, motion, forecast summary | radar grids and its four versions | RainViewer + Open-Meteo |
| `rain_forecast(lat, lon)` | 15-minute rain for 2 hours at one spot | the forecast | Open-Meteo |
| `describe_photo()` | caption, details, subject boxes | the description | Muse Spark (vision) |

`tools.call(ctx, name, args)` never raises: a missing argument, an unknown tool or a failing service (Overpass timing out, for example) comes back as `{"error": ...}`, which the model reads and works around. Every call is logged in `ctx.log`.

## Scene: one description, four drawings

`evac/scene.py` is what a mode produces and every renderer reads. Renderers never fetch data or route; they draw what is in the Scene.

| Field | Meaning |
| --- | --- |
| `you` | the reader's GPS |
| `center` | where the view is centred (None: on you). Walking maps are fitted to the route instead |
| `half_m` | half the width of the view, in metres |
| `text` | the message (instructions, directions, Muse's reply) |
| `roads`, `closures` | OSM roads; closed segments `{road, from, to}` |
| `hazard`, `zone` | e.g. `"fire"`; `((lat, lon), radius_m)` of the danger zone. No zone = plain directions |
| `goal` | `(name, (lat, lon))`: a shelter, or a destination when there is no zone |
| `route` | `[(lat, lon), ...]` from you to the goal or to safety |
| `aerial`, `masks` | the aerial photo of the view and its terrain masks (water, trees) |
| `walk` | routed on foot: off-street cells cost more, footpaths aren't drawn |
| `street_grid` | a Manhattan intersection: the SMS map uses the avenue/street grid |
| `title`, `summary` | the picture's banner |
| `cols` | width of the canonical frame (the SMS routing grid, 40 columns) |
| `focus` | the roads to draw, picked by `evac/focus.py` (None: every road) |
| `marks` | `[(n, (lat, lon))]`: where step n starts, drawn as a number on the route |

`evac/render.py`:

| Function | Version | How |
| --- | --- | --- |
| `sms(scene)` | low data | `textmap.py`: Manhattan street grid, or the routing grid shrunk to 14 to 20 columns; legend; returns the full SMS. A focused scene draws only its roads and the zone's edge |
| `mono(scene, cols)` | medium data | `laptopmap.py`: line-drawn roads, labels, route arrows and step numbers, 58 columns for WhatsApp. A focused scene: only its roads, the zone as an edge (░) and its worst part (▓), no trees, empty rows dropped |
| `small(scene)` | slow connection | `imagemap.py` at 400 px, WebP quality 50: **drawn** at that size, so labels stay readable |
| `picture(scene)` | high data | `imagemap.py` at 800 px, JPEG: an agency-style map; a focused scene draws its roads in full and the rest as faint grey context, with numbered step markers |

Moving the emergency and directions modes onto the Scene was checked against the previous code on five cases (NYC fire, gas leak, wildfire, flood, Lisbon walk) with the model switched off: the SMS replies and the pictures came out byte-for-byte identical. The WhatsApp maps changed only where they now draw the shared route instead of computing their own.

## Showing only what matters

The first versions drew every road in the frame. In Lisbon's old town the WhatsApp map had about 950 line characters and the route was hard to find; the wildfire map shaded the whole zone and every patch of trees. Someone reading a map in a hurry, or in a panic, needs three things: **where they are, which way to go, and what to avoid.** So every Scene now carries `focus`: the few roads worth drawing, chosen by `evac/focus.py`, and every version draws only those.

```mermaid
flowchart TD
  R["Scene: route, roads, closures, you, goal"] --> W["Walked: the nearest road to each route point<br/>(kept if it is nearest to 2+ points)"]
  R --> T["Turns: simplify the route line,<br/>heading change ≥ 35°"]
  T --> L["Landmark at each turn: the biggest named<br/>cross street within 35 m, drawn within 90 m of the turn"]
  R --> M["Main roads: crossed by the route (walking),<br/>or all secondary and bigger in view (alerts)"]
  R --> C["Closures: every piece of each closed road"]
  R --> A["Anchors: the destination's street;<br/>for alerts also the street you're on"]
  W & L & M & C & A --> F["scene.focus"]
  F --> S1["SMS: only these roads (side streets too when walking)<br/>zone drawn as its edge"]
  F --> S2["WhatsApp: only these roads, step numbers on the route,<br/>zone edge ░ + worst part ▓, no trees, empty rows dropped"]
  F --> S3["Pictures: these roads in full colour,<br/>the rest as faint grey lines for context"]
```

Choices that mattered:

- **The nearest road, not every road nearby.** Old-town streets run a few metres apart, so "roads within 14 m of the route" kept 75 roads in Lisbon. Counting only the nearest road at each route point keeps 38.
- **Streets matched by name are cut to the area that matters.** A landmark is drawn only within 90 m of its turn. Before that, "Calçada do Carmo" pulled in its whole length across the download.
- **The view is fitted to what matters.** Walking maps are framed around you, the next stretch of the route and (for a walk under 1 km) the destination, instead of a fixed square centred on you. Half the old frame was behind you.
- **Alerts keep the main roads.** For orientation, an alert map keeps every secondary or bigger road in view (avenues, Sunset Blvd, PCH) and the street you're on. It drops side streets, tree dots and the zone fill. Someone inside the zone also gets a first line to act on: `GO EAST NOW toward Westwood Recreation Center.`

Measured on the same inputs with the model off (non-blank characters in the 58-column WhatsApp map):

| Map | Before | After |
| --- | --- | --- |
| Lisbon walk, Rossio → castle | 957 | 232 |
| Rome walk, Termini → Colosseum | 1264 | 206 |
| Wildfire, inside the zone | 1387 | 624 |
| NYC gas leak | 742 | 586 |
| NYC fire | 713 | 647 |

The SMS maps are still within 0.04 cells of alignment (`worst row drift`) in every case.

### Choosing the version

The simulator's **Person's connection** setting is a data budget per message: no data (0) → SMS; a trickle (~2 KB) → WhatsApp text, counted at its compressed size; slow (~25 KB) → the small picture; good → the full picture. The richest version that fits is highlighted as "sent on this connection". On a phone the app could measure its own speed; over SMS the server can't, so this is a per-person setting.

**Why draw small pictures instead of shrinking big ones:** shrinking the 800 px NYC map to 200 px gives 3.9 KB but its 11 px street names become unreadable smudges. Drawn at 400 px, text and lines keep their pixel size, fewer labels fit (biggest roads first, no overlaps), and the map stays legible at 17 KB. Photos and radar are pictures to begin with, so those are simply shrunk (4.5 KB for the radar, 3.5 KB for a misty photo).

## Emergency alerts

The one fixed path: `emergency_reply` runs `pipeline.run`, which is deterministic apart from Muse reading the alert and rewording the instructions.

```mermaid
flowchart TD
  A[Alert text + person's GPS] --> B["Muse Spark: alert to fields<br/>(numbers, units and street names copied as written)"]
  B --> C["Code: units to metres<br/>no radius = 150 m point incident"]
  C --> D["Find the incident<br/>Nominatim as written, then the crossing of the two streets in OSM road data"]
  D --> E{"Distance from person"}
  E -- "far (> zone + 3 km)" --> F["One line: 'You are 3962 km outside the fire zone. No action needed.'"]
  E -- "inside or near" --> G["Grid around the person<br/>aerial tiles → terrain, OSM roads, closed segments, hazard circle"]
  G --> H["Route: Dijkstra; roads cheap, closures 60×, cost rises toward the hazard centre<br/>to the shelter, or to safe cells if inside"]
  H --> I["Directions: 'Head E on Alma Real Drive ~2.5 km'"]
  I --> J["Muse Spark rewrites the draft (keeps every number and name)"]
  J --> K["Scene → render: SMS, WhatsApp text, small picture, picture"]
```

Details that matter:

- **The model copies, code computes.** Muse returns `{"value": 2, "unit": "mi"}` and street segments exactly as written; `notice._normalize` converts. Letting the model convert made the radius differ run to run, which changed the map area and missed the road cache.
- **Intersections come from road data.** "5 Av and W 47 St" is found where the two streets cross in OpenStreetMap; the geocoder alone was about 600 m off. The place is looked up as written first (biasing toward the reader once put a Manhattan fire in Los Angeles for a reader there).
- **Closures are segments.** "Avoid 5 Av between W 42 St and W 50 St" closes only that stretch, found by its two cross streets.
- **Routing** is Dijkstra on a grid whose cells are twice as tall as wide (like a character): roads cost 1, open ground 3, trees 4 (8 in a wildfire), buildings 12, water 30 (impassable outside wildfires), closures 60, and costs rise toward the centre of the hazard.
- **Muse runs with `reasoning_effort: minimal`**: the same answers in 1 to 2 s instead of 5 to 10 s.

Samples are in [`samples/`](samples/); the NYC alerts are fictional drills.

### NYC fire, you are 3 blocks east

```bash
python3 main.py samples/nyc_fire.txt --at 40.75491,-73.97441 --laptop 58
```

Low data (12 SMS parts). The `+` area around the fire is gone; the closed avenue and the `#` are what to avoid:

```
FDNY responding to 5-alarm fire at 5 Av and W 47 St.
Fire incident 400 m from you.
Avoid 5 Av (W 42 St to W 50 St).
Expect road closures and smoke.

MAP (uptown is up):
=================== 51 St
  |     |     |     |     |  
=====×============= 50 St
  |    ×    |     |     |  
=====×============= 49 St
  |    ×    |     |     |  
=====×============= 48 St
  |    ×    |     |     |  
=====#========= 🔴 == 47 St
  |    ×    |     |     |  
=====×============= 46 St
  |    ×    |     |     |  
=====×============= 45 St
  |    ×    |     |     |  
=====×============= 44 St
  |    ×    |     |     |  
=====×============= 43 St

# fire: AVOID
× road closed: AVOID
= open road
🔴 you
Avenues left to right: 6 Av, 5 Av, Madison, Park, Lex
```

Medium data (3.2 KB, 0.5 KB compressed). Only the avenues (main roads), your street, and the closed avenue are drawn; the zone is an edge `░` with its worst part `▓`:

```
FDNY responding to 5-alarm fire at 5 Av and W 47 St.
Fire incident 400 m from you.
Avoid 5 Av (W 42 St to W 50 St).
Expect road closures and smoke.

                            Lexington Av
 5th Av  Madison Av  Park Av            3rd Av        2nd Av
┌─ ↑ uptown ───────────────────────────────────────────────┐
│   ┃         ┃        ┃┃         ┃        ┃             ┃ │
│   ┃         ┃        ┃┃         ┃        ┃             ┃ │
│   ┃         ┃        ┃┃         ┃        ┃             ┃ │
│   ┃         ┃        ┃┃         ┃        ┃             ┃ │
│   ┃         ┃        ┃┃         ┃        ┃             ┃ │
│━━━×━━━━━━━━━╋━━━━━━━━╋╋━━━━━━━━━╋━━━━━━━━╋━━ 50th St ━━╋━│
│   ×         ┃        ┃┃         ┃        ┃             ┃ │
│   ×         ┃        ┃┃         ┃        ┃             ┃ │
│━━━×━━━━━━━━━╋━━━━━━━━╋╋━━━━━━━━━╋━━━━━━━━╋━━ 49th St ━━╋━│
│░░░×░░░░░    ┃        ┃┃         ┃        ┃             ┃ │
│░  ×     ░░  ┃        ┃┃         ┃        ┃             ┃ │
│░  ×       ░ ┃        ┃┃         ┃        ┃             ┃ │
│░ ▓×▓       ░┃        ┃┃    YOU  ┃        ┃             ┃ │
│░▓▓◆▓FIRE   ░┃        ┃╋────🔴───╋────────╋── 47th St ──╋ │
│░ ▓×▓       ░┃        ┃┃         ┃        ┃             ┃ │
│░  ×━━━━━━━━━╋━━━━━━━━┃┃         ┃        ┃             ┃ │
│━━━×     ░░  ┃       ━╋╋╋━━━━━━━━╋━━━━━━━━╋━━ 46th St ━━╋━│
│░░░×░░░░░    ┃        ┃ ┃        ┃        ┃             ┃ │
│━━━╋━━━━━━━━━╋━━━━━╋━━━━━╲╋━━━━━━╋━━━━━━━━╋━━ 45th St ━━╋━│
│   ×         ┃     ┃      ┃      ┃        ┃             ┃ │
│   ×         ┃     ┃      ┃      ┃        ┃             ┃ │
│   ×         ┃     ┃      ┃      ┃        ┃             ┃ │
│   ×         ┃     ┃      ┃      ┃        ┃             ┃ │
│   ×         ┃     ┃      ┃      ┃        ┃             ┃ │
│   ×         ┃     ┃      ┃      ┃        ┃             ┃ │
│   ×         ┃     ┃╲━━╋━━╋      ┃        ┃             ┃ │
│━━━╋━━━━━━━━━╋━━━━━━━━━╋━━━━━━━━━╋━━━━━━━━╋━━ 42nd St ━━╋━│
└─ ├───────────┤ 200 m ────────────────────────────────────┘

🔴 you
◆ incident   ░ edge of danger zone   ▓ worst of it
× road closed
━ main road   ─ street
Avenues run up and down, streets left and right.
```

Small picture (17 KB, drawn at 400 px) and full picture (89 KB). Side streets are faint grey lines; the avenues, the closure and the zone stand out:

![Small picture map of the NYC fire](docs/alert_small_nyc_fire.webp) ![Picture map of the NYC fire](docs/alert_picture_nyc_fire.jpg)

### NYC gas leak, a street closure

```bash
python3 main.py samples/nyc_gas_leak.txt --at 40.7560,-73.9740 -v
```
notice (llm): {'hazard': 'gas', 'order': 'avoid_area', 'zone_center_place': 'Lexington Av and E 51 St, Manhattan', 'shelters': [], 'avoid_roads': [{'road': 'E 51 St', 'from': 'Park Av', 'to': '3 Av'}], 'deadline': None, 'summary': 'Avoid E 51 St between Park Av and 3 Av in Manhattan due to a gas leak at Lexington Av and E 51 St.', 'zone_radius_m': None}
intersection Lexington Av & E 51 St found in road data at (40.7571285, -73.9719044) (geocoder said (40.756184, -73.972591))
user (40.756, -73.974), zone center (40.7571285, -73.9719044) r=150m, distance 216m, inside=False
closure '51 street': segment between cross streets
107 of 132 roads drawn
text map: 9 rows, worst row drift 0.04 cells 

Gas leak at Lexington Av and E 51 St.
You are 220 m from the incident.
Avoid E 51 St between Park Av and 3 Av.

MAP (uptown is up):
=============== 52 St
  |     |     |     |  
=====××××#××××= 51 St
  |     |     |     |  
=============== 50 St
  |     |     |     |  
=============== 49 St
  |     |   🔴  |     |  
=============== 48 St

# gas: AVOID
× road closed: AVOID
= open road
🔴 you
Avenues left to right: Madison, Park, Lex, 3 Av
```
notice (llm): {'hazard': 'gas', 'order': 'avoid_area', 'zone_center_place': 'Lexington Av and E 51 St, Manhattan', 'shelters': [], 'avoid_roads': [{'road': 'E 51 St', 'from': 'Park Av', 'to': '3 Av'}], 'deadline': None, 'summary': 'Con Edison and FDNY are responding to a gas leak at Lexington Av and E 51 St, avoid E 51 St between Park Av and 3 Av and expect delays.', 'zone_radius_m': None}
intersection Lexington Av & E 51 St found in road data at (40.7571285, -73.9719044) (geocoder said (40.7641117, -73.966817))
user (40.756, -73.974), zone center (40.7571285, -73.9719044) r=150m, distance 216m, inside=False
closure '51 street': segment between cross streets
text map: 13 rows, worst row drift 0.04 cells 

Con Edison and FDNY are responding to a gas leak at Lexington Av and E 51 St.
Gas incident is 220 m from you.
Avoid E 51 St between Park Av and 3 Av.
Expect delays.

MAP (uptown is up):
=============== 53 St
  |     |  ++ | ++  |  
=============== 52 St
  |     | +++ | +++ |  
=====××××#××××= 51 St
  |     | +++ | +++ |  
=============== 50 St
  |     |  ++ | ++  |  
=============== 49 St
  |     |   🔴  |     |  
=============== 48 St
  |     |     |     |  
=============== 47 St

# gas: AVOID
+ near the leak: stay clear
× road closed: AVOID
= open road
🔴 you
Avenues left to right: Madison, Park, Lex, 3 Av
```

### Wildfire, you are inside the zone

```bash
python3 main.py samples/palisades_fire.txt --at 34.0445,-118.5300 --laptop 58
```

Low data (9 SMS parts). Someone inside the zone gets one line to act on first:

```
GO EAST NOW toward Westwood Recreation Center.
EVACUATE NOW.
You are within 2 miles of Temescal Canyon Park wildfire.
Go to Westwood Recreation Center.
Head E on unnamed road ~210 m.
Head E on Alma Real Drive ~2.5 km.
Head E on 17th Street ~620 m.
Continue to Westwood Recreation Center (7.6 km E).
Avoid Sunset Blvd.
Avoid Palisades Dr.

MAP (north is up):
 ×××          
 ××+++++++ ×× 
 +××#### ××××× Sunset Blvd
~+=### 🔴 >>>+=  PCH
~+~######==+==
~~++~~~~+++===
~~~~+~~+~  ===

# wildfire: AVOID
+ evacuation zone: leave
× road closed: AVOID
= open road
~ water
< > Λ v way out
🔴 you
```

Medium data (3.1 KB, 0.6 KB compressed). At 142 m per character, side streets are left out; so are the tree dots and the zone's fill, leaving its edge `░`, its worst part `▓`, the closed roads and the way out:

```
GO EAST NOW toward Westwood Recreation Center.
EVACUATE NOW.
You are within 2 miles of Temescal Canyon Park wildfire.
Go to Westwood Recreation Center.
Head E on unnamed road ~210 m.
Head E on Alma Real Drive ~2.5 km.
Head E on 17th Street ~620 m.
Continue to Westwood Recreation Center (7.6 km E).
Avoid Sunset Blvd.
Avoid Palisades Dr.

┌─ ↑ N ────────────────────────────────────────────────────┐
│      ××××                                                │
│      × ××××××                                            │
│      ××      ××                                          │
│       ×××                                                │
│         ××         ░░░░░░░░░░░░░░                        │
│          ×    ░░░░░              ░░░░                    │
│          ×× ░░                       ░░░                 │
│          ░░×                            ░░        ××     │
│         ░  ××                        Sunset Blvd ××××××××│
│       ░░    ×                               ░  ×××       │
│      ░      ××                          ×   ××××         │
│      ░       ×     ×××××××××××         ××× ×  ░     ━━━━⌂│
│ Palisades Dr ×     ×     ▓▓╱╱×××      ×××××    ░ ╱╱╱━    │
│     ░       ×××   ×× ▓▓▓▓▓▓┃🔴 ×××  ××         ╱╱╱       │
│╱╱━━━━━╲     ××××××  ▓▓▓▓▓▓┃│▓→→→→ ×××        ╱╱╱         │
│≈≈≈≈░   ━━╲━╋╋━━━━━━━━╲▓▓▓◆╱▓▓▓▓ ↓→→→→→→→→→→→→→→→→→→      │
│≈≈≈≈░                 ╲╲╲╳╱▓▓▓▓▓            ┃   ░         │
│≈≈≈≈≈░                 ▓▓╲╲╲╲▓             ┃┃   ░         │
│≈≈≈≈≈░                       ╲━╲╲╲ PCH    ╱╱   ░          │
│≈≈≈≈≈≈░                          ╲╲╋╲   ╱╱╱    ░        ╱╋│
│≈≈≈≈≈≈≈░                            ╲╲╋╋╳     ░       ╱╱╱ │
│≈≈≈≈≈≈≈≈░░                             ╲╲╲╲ ░░      ╱╱╱ ╱╱│
│≈≈≈≈≈≈≈≈≈≈░                               ╲╲╳     ╱╳╳╲╱╱╱ │
│≈≈≈≈≈≈≈≈≈≈≈░░░                          ░░  ╲╲╋ ╱╱╱ ╱╳╳╲╱╱│
│≈≈≈≈≈≈≈≈≈≈≈≈≈≈░░░                   ░░░░      ╲╳╳ ╱╱╱ ╱╳╳╳│
│≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈░░░░░░░░    ░░░░░░░            ╲╳╳ ╱╋╋╳╱╳│
│≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈░░░░Santa Monica Freeway ╲╋╳━╳╳╳╱│
└─ ├────────────┤ 2 km ────────────────────────────────────┘

🔴 you
◆ incident   ░ edge of danger zone   ▓ worst of it
× road closed
→ way out
⌂ shelter
━ main road   ─ street
≈ water
```

## Photos that won't load

A tone sketch (darker pixel, inkier character) shades every pixel, so mist, ripples and stripes crowd out what the photo is of. Now the drawing is the bare minimum: the outline of the main subject, or the borders of the few big shapes when there is no single subject.

```mermaid
flowchart TD
  A["Photo a friend sent<br/>(it didn't download)"] --> B["Muse Spark: caption,<br/>up to 3 details, subject names"]
  A --> C["Blur, YCbCr colour at 2× the cell grid"]
  C --> D["Background = 4 colours along the photo's border (k-means)<br/>subject = pixels far from all of them (Otsu threshold)"]
  D --> E["Clean: drop specks, close gaps (a zebra's stripes),<br/>keep the biggest pieces, fill holes"]
  E --> Z{"Subject size"}
  Z -- "under 6% of the photo" --> ZC["Crop to it, in the photo's shape, and redo"] --> E
  Z -- "4% to 70%, at most 4 pieces" --> O["Subject"]
  Z -- "none, or everywhere" --> LR["3 colour regions,<br/>islands merged away"]
  O --> M1["WhatsApp: its edge in ─ │ ╱ ╲"]
  O --> S1["SMS: a solid # silhouette (shrunk from the WhatsApp mask)"]
  LR --> M2["WhatsApp: the borders between regions"]
  LR --> S2["SMS: 3 flat tones (blank, +, #)"]
  A --> SM["Small: 400 px WebP"]
  A --> H["High: the photo"]
```

Muse's subject boxes were the first idea for finding the subject, but they were too loose: none for the flamingos, the whole frame for "waterfall". SAM 3.1 (a segmentation model on the Meta Model API) would give the real mask; the colour-contrast guess stands in until it is wired in.

The examples are photos macOS ships in `/Library/Desktop Pictures` (the simulator offers them as samples; they are not in this repo).

**A clear subject: "Pink Lotus Flower"** (WhatsApp text1008 bytes (175 gzipped), photo 3231988 bytes):

```
Photo: A vibrant pink lotus flower in full bloom with a bright yellow center against a dark green blurred background.
- Petals are pink with white near the center
- Yellow seed pod surrounded by stamens in the middle
- No readable text, background is dark green foliage

       ###  ####
     ###########
   ############
   #############
        ####
(outline of the main subject)
```

```
                        │    │
                        ╱    ╲╲        ───────────────
                 ───────      ╲────────              ╲
                ╱                             ───────╱
               ╱                             ╱
               │                            ╱
         ──────╱                            │
        ╱                                   ╲╲──
        ╲╲                                     ────
         ╲                                        ╲
         ╲╲                                       │
          ╲╲        ────                       ───╱
           ╲────────   ─╲               ───────
                        ╲╲            ──
                         ╲           ╱
                         │           │
(outline of the main subject)
```

**A subject against mist: "Eagle & Waterfall".** The waterfall is the subject found; the soaring bird is too small to register (WhatsApp text401 bytes (144 gzipped), photo 1722473 bytes):

```
Photo: A lone bird soars in front of a massive waterfall shrouded in golden mist at sunset.
- Massive waterfall cascading on the left side
- Thick golden mist fills the center lit by sunlight
- No readable text in the image

####
######
##  #####
(outline of the main subject: waterfall)
```

```
           │
           ╲╲
            ╲────
                ─╲
       ───       ╲╲
      ╱  ─╲       ╲───
──    │   ╲───────   ───
 ─╲   │          ─╲    ───────
  ╲╲  ╱           ╲────      ╲
   ╲──                ───────╱
(outline of the main subject: waterfall)
```

**No single subject: "Ducks on a Misty Pond".** The five ducks are a few pixels each, so the drawing falls back to the main shapes: the far shore, the hills and the water line (WhatsApp text983 bytes (127 gzipped), photo 1251829 bytes):

```
Photo: A misty, tranquil lake at dawn with a small group of ducks swimming while fog obscures distant trees and hills.
- Five dark ducks swim together on the left side of the calm reflective water
- Thick white fog creates a soft, pale, muted atmosphere with no readable text
- Silhouetted trees on the left and low hills line the far shore

++++++++++++++++++
####++++++++++++++
#############+++++
##################
##################
########++++++++++
++++++
(outlines of the main shapes)
```

```
─
─────────────
            ──────
                 ────────────────
                                ───────────
                                          ───────
                                                ──────
                                                     ─────



                                                 ─
                                  ────────────────────────
                         ─────────
                ─────────
    ────────────                  ────────────────  ──────
────              ────────────────               ───
╱  │──────────────
───
(outlines of the main shapes)
```

## Weather radar

```mermaid
flowchart TD
  A["'RADAR' + GPS"] --> B["RainViewer: latest radar frame<br/>tiles at zoom 7 (~1 km per pixel), 160 × 110 km around you"]
  B --> C["Tile colours → dBZ with RainViewer's colour table<br/>→ none / light / moderate / heavy / storm"]
  B --> D["Frame from 40 min ago → how far the rain moved<br/>→ direction and km/h"]
  A --> E["Open-Meteo: rain at your spot<br/>every 15 min for 2 hours"]
  A --> F["Overpass: towns to label<br/>(one quick try, skipped for 10 min after a timeout)"]
  C & D & E --> G["Facts: over you now? nearest rain, moving where, rain later?<br/>Muse rewrites them"]
  C --> L["Low: 18-column grid  + × 0 #"]
  C & F --> M["Medium: 58-column map ░▒▓█ with towns<br/>+ 2-hour sparkline"]
  B --> H["Small and high: radar over a grey basemap with place names"]
```

RainViewer's free tiles go up to zoom 7 and have a fixed colour scheme, so `radar.PALETTE` maps colours back to radar strength (dBZ) using RainViewer's published colour table. It has no forecast frames, so the 2-hour timeline comes from Open-Meteo.

Example: "Find heavy rain right now" picked `(-9.6838, -40.7812)` (Bahia, Brazil) on 2026-10-28 02:10 UTC. Radar is live, so this differs every run.

Low data:

```
Hi! 23:00 check: dry over you now :) Nearest light rain 31 km NE, moving toward the SW at about 26 km/h. Good news: no rain for your spot in the next 2 hours!

RADAR (north is up, 160 km across):
 000              
 0××      ++      
 ××              ×
× 00     🔴         
×000×××           
××00000××       00

+ light  × moderate
0 heavy  # storm
🔴 you
```

Medium data (2.0 KB, 0.36 KB compressed; small picture 4.6 KB; full picture 51 KB):

```
┌─ ↑ N  radar 23:00 ───────────────────────────────────────┐
│   ░▒░   ▓▓▓                                              │
│     ▒▒                                                   │
│    ▒▓▓                                                   │
│    ▒▓▓                                                   │
│                                  ░░░                     │
│          ▒▒                                              │
│     ▒▒▒░                                                 │
│     ░░░░                                               ▒▒│
│                                                         ▒│
│      ▒▒▓▓▓▒                🔴                            │
│░░     ▒▓▓▓▒                                              │
│▒▒░   ▒▒▒ ▒▒▒                                             │
│▒▒░░▒▒▒▒▒░░░ ░░░                                          │
│▒░░░▒▒▓▓▓▓▓▓▒▒▒░░░                                        │
│▒▒░░▒▒▓▓▓▓▓▓▒▒▒▒▒▒░▒▒░                                    │
│░  ▒░░▒▓▓▓▓▓▓▒▒▒▒▒▒▒▓▒▒                               ▒   │
│       ▒▒░▒▓▓▓▓▓▒▒▒▒▒▒▒                             ░░▒░░▒│
│ ░▒ ░░░▒░▒▒▓▓▓▓▓▓▓▓▓▓▒▒▒  ▒▒                        ░▒▓▓░▒│
└─ ├───────┤ 25 km ────────────────────────────────────────┘

Your spot, next 2 h (15 min steps): ········
23:00 now  ...  00:45

░ light  ▒ moderate  ▓ heavy  █ storm   🔴 you
```

![Radar picture](docs/radar_picture.jpg)

## Walking directions abroad

For someone roaming on a capped plan. Asked directly, Muse Spark gives directions from memory: they read well, but it can't know where you are standing, some turns were wrong (for Rossio to the castle in Lisbon it sent us through the Arco da Bandeira into Praça da Figueira, which it doesn't lead to), and its sketch wasn't to scale. Here the route comes from real street data and Muse only rewords it.

A walk is a **trip**, not one message. The streets are downloaded once; every new GPS fix re-routes on them. Each map shows **only the next stretch**: the route, numbered step starts, the streets the route uses, the cross street at each turn and the main roads it crosses. While you're on the route with a long way to go on the current street, the update is a single line and no map.

```mermaid
flowchart TD
  A["Destination text + GPS"] --> B["Nominatim, biased to your area"]
  B --> C{"Under 4 km?"}
  C -- no --> X["'Too far to walk. Try transit.'"]
  C -- yes --> D["plan(): Overpass streets, pedestrian streets, footways, steps (once)<br/>routing grids: 40 columns for steps, 160 for the drawn route"]
  D --> R["Route from where you are: steps, step starts, distance"]
  R --> V["View: the next two steps (at least 400 m), or the whole walk if under 1 km<br/>focus: walked streets, turn landmarks, main roads crossed"]
  V --> S["Scene → SMS, WhatsApp text, small picture, picture"]
  G["New GPS fix: move()"] --> Q{"Where are you?"}
  Q -- "within 30 m of the destination" --> AR["'You've arrived'"]
  Q -- "on the route, over 80 m left on this street" --> K["One line: 'On route. Keep going: head E on Praça da Figueira ~400 m.'<br/>(pictures still drawn for the picture tiers)"]
  Q -- "at a turn, or over 25 m off the route" --> R
```

```bash
python3 main.py --walk "Castelo de São Jorge, Lisbon" --at 38.7139,-9.1406 -v
```

Low data (7 SMS parts). Under 1 km, so the whole walk fits one map, fitted to the route instead of centred on you:

```
Hey! To get to Castelo de São Jorge: about 0.7 km, 9 min.
1. Head E on Largo do Duque do Cadaval ~40 m
2. Head E on Praça Dom Pedro IV ~130 m
3. Head E on Praça da Figueira ~360 m
4. Head NE on unnamed road ~140 m

MAP (north is up):
              
              
  ==   == v = v   
= 🔴 >>>>>>>>>S 
  ====        
              
              

= open road
< > Λ v route
🔴 you
S destination: Castelo de São Jorge
```

Medium data (1536 bytes, 414 compressed, picture map 77747 bytes). 38 of the 3,419 streets and paths downloaded are drawn; before, this map drew everything in a square around you (957 non-blank characters, now 232):

```
┌─ ↑ N ────────────────────────────────────────────────────┐
│                                                          │
│              Rua de São Pedro Mártir ╱╱                  │
│                                     │╱                   │
│              Rua João das Regras ┃  ││                   │
│           ╱━╲                   ┃┃ │││↑→→→─  ╱↑→→        │
│     ╳    ╱╱ ╲┃           ┃──↑→→→→→╱│ ↑→  ↓→4→→→ ↓→→──    │
│ ╳╱╳│     ┃   ┃┃ ↑→→→→→→→→→→→→   ┃ ↘→→→           │↓→→    │
│  ╲╲│🔴   ┃┃  ↗→3→──  ─  ┃┃╋│                     │  ⌂    │
│   ╲╲↓2→→→→→→→ ┃┃         ┃┃┃│                    │       │
│     ─      ┃│ │┃┃    ╱╱━━╱ ╲╋                            │
│            ┃╲━━╱┃━ Praça da Figueira                     │
│    Praça Dom Pedro IV                                    │
│                                                          │
└─ ├──────────────┤ 200 m ─────────────────────────────────┘

🔴 you
→ route   1 2 3 steps
⌂ destination
━ main road   ─ street
```

Small picture (16 KB) and full picture (78 KB). The route's streets are in full colour, the rest faint grey:

![Small walking route picture](docs/walk_small_lisbon.webp) ![Walking route picture](docs/walk_picture_lisbon.jpg)

**As you walk.** The same trip after three simulated GPS fixes (`directions.move`; the simulator's **Walk 150 m** and **Take a wrong turn** buttons). Updates are templates, with no model call, so they take 1 to 3 s:

```
=== GPS fix: walked 150 m -> on route, next turn (new map)
Next turn coming up. 0.5 km, 7 min to Castelo de São Jorge.
1. Head E on Praça Dom Pedro IV ~40 m
2. Head E on Praça da Figueira ~360 m
3. Head NE on unnamed road ~140 m

MAP (north is up):
            = 
         ==== 
==   >>=>>> v   
= 🔴 >>Λ=>> =>S 
======        
              
              

= open road
< > Λ v route
🔴 you
S destination: Castelo de São Jorge

┌─ ↑ N ────────────────────────────────────────────────────┐
│                                                          │
│                                                      ││  │
│                                                    ╱╱│   │
│                                                ╱╱╱╱╱     │
│                                            ╱╱╱╱╱         │
│                                    ╱    ╱╱╱╱             │
│                                  ╱╱  ╱╱╱                 │
│                                 ││  ││                   │
│                             ┃┃  ││  ↑→→     ╱↑→          │
│   ━━━╲                 ↑→→→→→→ │││ ↗→─↓→→3→→→→↓→→        │
│ ┃    ┃              ╋─↑→─── ┃↓→→→→→       ──   ╲↓→→─     │
│ ┃┃    ┃      ─ ──┼ ╱┃│↗    ┃┃ ╱  ││             │╳↓→     │
│  ┃    ┃┃↑→→→→→→→→→→→→→│  Rua de São Lourenço    │  ⌂     │
│  ┃┃ ↑🔴→2          ┃┃┃│                         │        │
│   ┃┃    ┃        ━━╱┃╲╲┼                                 │
│    ┃    ┃┃     ╱╱━                                       │
│       ━━╱╲━                                              │
│ Praça Dom Pedro IV                                       │
│                                                          │
└─ ├───────┤ 100 m ────────────────────────────────────────┘

🔴 you
→ route   1 2 3 steps
⌂ destination
━ main road   ─ street
view 632 m across, 29 of 3419 roads drawn
sms map: 7 rows, worst row drift 0.04 cells
on route, next turn: 12 m from the last route

=== GPS fix: walked 150 m -> on route (text only)
On route. Keep going: head E on Praça da Figueira ~400 m.
0.4 km, 5 min to Castelo de São Jorge.

(WhatsApp: the same text, no map)
view 522 m across, 24 of 3419 roads drawn
on route: 1 m from the last route

=== GPS fix: wrong turn -> off route, re-routed (new map)
Off route, new directions. 0.5 km, 7 min to Castelo de São Jorge.
1. Head E on Praça da Figueira ~360 m
2. Head NE on unnamed road ~140 m

MAP (north is up):
              
              
=     ==>=>   
= 🔴 >>>>>Λ>>S= 
=======       
              
              

= open road
< > Λ v route
🔴 you
S destination: Castelo de São Jorge

┌─ ↑ N ────────────────────────────────────────────────────┐
│                                                          │
│           Rua de São Pedro Mártir ╱                      │
│                                 ╱╱                       │
│                                ││                        │
│                            ┃   ││                        │
│━━━    Rua João das Regras │┃  │││ ↑→→→─   ╱╱↗→→          │
│━ ╲╲                  ↑→→→→┃┃  │ │↑→  ↓→→2→→→  ↓→→        │
│   ┃┃              ╋┼↑→   ↓→→→→→→→→             ╲↓→→─     │
│    ┃┃ ↑→→→→→→→→→→→→→→    ┃┃ ╱   │              ││ ↓⌂     │
│┃    🔴→          ┃┃╋│    ┃ Poço do Borratem    │         │
│┃    ┃┃           ┃┃┃┃│   ┃                     │         │
│┃┃    ┃       ╱━━━╱┃ ╲╋                                   │
│ ┃    ╱┃     ╱╱ Praça da Figueira                         │
│     ━╱╲╲ Praça Dom Pedro IV                              │
│                                                          │
└─ ├────────┤ 100 m ───────────────────────────────────────┘

🔴 you
→ route   1 2 3 steps
⌂ destination
━ main road   ─ street
view 599 m across, 29 of 3419 roads drawn
sms map: 7 rows, worst row drift 0.04 cells
off route, re-routed: 81 m from the last route
```

## Why the text maps line up

All the maps are drawn from map data, one meaning per character cell, not converted from a photo pixel by pixel:

```mermaid
flowchart LR
  A["lat/lon → local metres<br/>(Manhattan turned 29° so avenues run up)"] --> B["Grid of cells<br/>each ~2× taller than wide, like a character"]
  B --> C["Layers per cell: terrain, roads and their direction,<br/>closures, hazard depth, route"]
  C --> D["One character per cell by priority:<br/>you, incident, shelter, route, closure, road, danger shading, water, trees, blank"]
  D --> E["Labels only where they cover blank space<br/>street names written on the street line"]
  E --> F["Frame, scale bar, legend"]
```

- **iPhone Messages uses SF Pro, a proportional font.** Ordinary ASCII art comes out crooked, so the SMS maps use only characters measured (in a browser canvas, `17px system-ui`) to be exactly one digit wide: `= # + < > 0 $ ¤ £ ¥ ×` and U+2007 FIGURE SPACE for blanks. Narrow ones are padded with thin and hair spaces (`|`, `v`); slightly wide ones (`Λ` +6%, `S` +1.2%) appear at most once or twice per row. Each row's width error is added up and logged (`worst row drift`); the examples are 0.03 to 0.04 cells. Figure spaces and `×` are outside the GSM-7 alphabet, so these messages are UCS-2: 67 characters per SMS part.
- **WhatsApp shows text in triple backticks in a monospace font, but can't colour it.** So the medium maps use line drawing (`━ ┃ ╋ ╱ ╲`) and shading (`░▒▓█`), all one cell wide in Menlo, Monaco and Courier. Braille dots, common in ASCII art, are avoided: on a Mac they fall back to another font and come out 14% wider. A WhatsApp bubble is at most 65% of the chat pane, which in the simulated window fits 58 columns without wrapping.
- **"You" is the 🔴 red dot in both**, the only way to get colour. It is wider than a character, so it takes two cells: exactly two in SF Pro (with padding), a third of a cell short in Menlo (about 2.5 px on that one row).
- **Street names** go at the end of rows on the SMS map (never inside the grid) and are dropped if the row would wrap in the bubble. On the WhatsApp map, Manhattan street names are written on the street line itself so they can't be read as the street above or below, and avenue names go in a header.

Previews of the SMS maps in SF Pro on a Mac (not yet checked on a real iPhone):

![SMS replies in SF Pro](docs/preview_replies.jpg)

## Try it: the simulator

```bash
python3 sim/server.py
```

Open http://127.0.0.1:8765. In the Claude desktop app, `.claude/launch.json` starts it in the browser pane. Every tab shows the result four ways with real sizes, and the **Person's connection** picker highlights the version that would be sent.

- **Emergency:** the left phone is **Emergency Response (555) 010-0911**. Type an alert or pick a sample (NYC fire, NYC gas leak, LA wildfire, Pasadena flood) and choose who gets it: **Bob the Builder**, **Wendy** or **Farmer Pickles**, each with their own GPS. Move someone and press **Text MAP** for a fresh reply. The pipeline log shows what Muse parsed and the alignment check.
- **Photo:** pick a sample photo or upload one.
- **Weather radar:** pick a city, type coordinates, or press **Find heavy rain right now**.
- **Directions abroad:** pick a starting station (Lisbon, Paris, Rome, Tokyo) and a destination. Then simulate the phone's GPS: **Walk 150 m along the route** or **Take a wrong turn**. Each update joins the thread, and the status line says whether a new map was sent or just a line of text.
- **Ask anything:** the router. Pick where you are (Rome, Lisbon, NYC), type anything or pick an example, optionally attach a photo; the tools Muse called are listed under the box.
- **Clear** empties every thread, result and the log.

## Run it from the command line

```bash
pip install requests pillow numpy
```

Put your Meta Model API key in `.env` (git-ignored):

```
LLAMA_API_KEY=...
```

Optional settings in `.env`: `LLM_BASE_URL` (default `https://api.meta.ai/v1`), `LLM_MODEL` (default `muse-spark-1.3`), `LLM_REASONING_EFFORT` (default `minimal`). Without a key: keyword routing, regex alert parsing, no photo descriptions, template text.

```bash
python3 main.py --ask "Is it going to rain on my walk to the Colosseum?" --at 41.9010,12.5016   # the router
python3 main.py --ask "what is this?" --photo some.jpg --at 40.75,-73.98                        # router + photo
python3 main.py samples/palisades_fire.txt --at 34.0445,-118.5300 -v                           # alert → SMS reply
python3 main.py samples/nyc_fire.txt --at 40.75491,-73.97441 --laptop 58                       # + the WhatsApp map
python3 main.py --photo some.jpg                                                               # photo → text versions
python3 main.py --radar 40.7128,-74.0060                                                       # weather radar as text
python3 main.py --walk "Castelo de São Jorge, Lisbon" --at 38.7139,-9.1406 -v                   # walking directions
```

`-v` prints the log: the parsed alert, the distance check, closures found, the map's alignment check, and every tool call with its result.

## Code

| File | Role |
| --- | --- |
| `evac/router.py` | One entry point: Muse picks tools (parallel, up to 3 rounds), writes the reply; the kept results become the versions |
| `evac/tools.py` | The 10 tools, their JSON descriptions, the per-request `Context`, `call()` that never raises |
| `evac/scene.py` | The `Scene`: what to draw, shared by every renderer |
| `evac/focus.py` | Which roads a map draws (walked, turn landmarks, main roads, closures, anchors) and the view fitted to them |
| `evac/render.py` | Scene → `sms`, `mono`, `small`, `picture` |
| `evac/textmap.py` | SMS maps aligned for SF Pro (Manhattan street grid, raster elsewhere) |
| `evac/laptopmap.py` | Monospace maps for WhatsApp: line-drawn roads, shading, labels, route arrows |
| `evac/imagemap.py` | Picture maps (JPEG, or WebP drawn small) in the style of agency alert maps; `shrink()` for photos and radar |
| `evac/pipeline.py` | The fixed emergency reply: parse, locate, route, word, build the Scene |
| `evac/directions.py` | A walk as a `Trip`: streets downloaded once, re-routed at every GPS fix (`move`); each map shows the next stretch |
| `evac/radar.py` | RainViewer tiles → rain levels, motion, Open-Meteo forecast, its four versions |
| `evac/photo.py` | Muse description; the subject's outline (saliency mask, zoom on small subjects) or the borders of the main regions |
| `evac/notice.py` | Alert text → fields (Muse, regex fallback); unit conversion in code |
| `evac/grid.py` | Grid: terrain, roads, closures, hazard, Dijkstra, turn-by-turn directions (`legs` also gives where each step starts) |
| `evac/osm.py` | Overpass queries (roads, walkable paths, towns) cached in `cache/`; street-name matching ("5 Av" = "Fifth Avenue"); intersections |
| `evac/geo.py` | Geocoding, distance, local projection |
| `evac/imagery.py` | XYZ tile stitching (Esri aerial, Esri grey basemap, RainViewer radar) |
| `evac/segment.py` | Aerial image → terrain masks (SAM 3 path, or a colour heuristic) |
| `evac/llm.py` | Meta Model API client (`chat`, `chat_json`, `chat_tools`); loads `.env`; `reasoning_effort` minimal |
| `main.py` | Command line: `--ask`, alerts, `--photo`, `--radar`, `--walk`, `--laptop N` |
| `sim/server.py` | Simulator server: `/api/message` (router), `/api/alert`, `/api/photo`, `/api/radar`, `/api/directions`, `/api/directions/move` (simulated GPS), `/api/find_rain`, sample photos |
| `sim/index.html` | The tabs, the four-way result cards, the connection picker |
| `CLAUDE.md` | Notes for Claude Code: commands, how the pieces connect, the text-map width rules |

## Data sources

Roads, paths and towns © OpenStreetMap contributors (via Overpass); geocoding by Nominatim; aerial imagery and grey basemap © Esri; radar © RainViewer; forecast by Open-Meteo. Picture maps carry these credits.

## Known limits

- Not yet checked on a real iPhone or in real WhatsApp (fonts, emoji widths, Bold Text, text size).
- The free Overpass servers are the slow, flaky part: a new area takes 30 s to 2 minutes or times out (a first walking route from Rome Termini timed out; Muse retried and the second call worked). When the intersection lookup fails, an incident falls back to the geocoder, which once put the gas leak 1.7 km away on the middle of Lexington Avenue. The cache makes repeat runs fast and reliable.
- Radar town names often time out and are then skipped. "Find heavy rain" works on a coarse world view, so the spot it picks can sit in a gap between storms.
- Radar and photos still produce their own versions; the next step makes them Scenes (a grid of values) so the router can, for example, draw the rain on the walking-route map.
- Muse's wording isn't checked against the tool results; the prompt asks it to keep every number and name. It sometimes adds an estimate (the Trevi Fountain reply guessed a 20-minute walk).
- Photo outlines come from a saliency guess (whatever differs from the colours along the photo's border), not a segmentation model. They work for one clear subject (a flower, a zebra's head and back). A flock of small birds becomes a few blobs, and a photo whose subject touches every edge falls back to the borders of its main regions. SAM 3.1 on the Meta Model API is the real fix and is not wired in yet. Muse's subject boxes were too loose to use: none for the flamingos, and the whole frame for "waterfall".
- Walking updates need the phone to send its position. The simulator moves "you" along the route or onto a nearby street off it; a real deployment needs the person to share their location, or reply with it. The wrong-turn button sometimes picks a street behind you.
- Terrain comes from a colour heuristic. On street-scale focused maps the water layer is skipped, because blue rooftops came out as specks of lake.
- SMS replies are long: 6 to 12 parts.
