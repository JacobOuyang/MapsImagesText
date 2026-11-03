# Demo: the night the data went out

A community hub in Red Hook, Brooklyn, during a coastal flood. A drill: the storm, the alerts and every person in it
are fictional.

## The idea

Professional responders have radios and dispatch systems. For the first hours of a disaster, though, the people who
check on the neighbour on the 14th floor are other neighbours: tenants' associations, mutual-aid groups, a church
hall with a generator. They coordinate in group chats, and group chats die when mobile data does.

Text messages usually still get through. They travel on the network's signalling channel, which keeps working after
data has collapsed, and new phones can also text over satellite. So the hub's job is to take **one good connection**
and turn it into maps, photos and coordination that reach every phone **as plain texts**. On the phones, the
offline kit (saved beforehand) draws what the texts carry.

Red Hook is a real place where this happened: after Hurricane Sandy in 2012, neighbours and volunteers went door to
door in dark towers for days. The demo borrows the place and the situation, not any real person or organisation.

## The people

| Who | Where | What happens to them |
| --- | --- | --- |
| **Rosa**, coordinator | the Neighbors Hub at Coffey Park, the only working connection | runs the dashboard |
| **Dani**, 17, volunteer | the hub | walks to Mr. Alvarez, re-routed around a downed line |
| **June**, volunteer | the hub | takes water and a power bank to Luísa |
| **Mr. Alvarez** | 14th floor, Red Hook Houses | no power, no elevator, out of water, phone at 18% |
| **Mr. Chen** | Red Hook Houses | his wife fell on the dark stairs |
| **Sam** | Beard St, inside the flood zone | water coming in downstairs; sends a photo of the street |
| **The Okafors** | on the street Dani takes | a tree comes down on a power line outside their house |
| **Luísa** | Red Hook Houses | writes in Portuguese; her mother is 82 |
| 34 more neighbours | across Red Hook | check in safe or needing help |

## The beats

Each beat is one button in the dashboard (`/dashboard`, bottom left) and one call to `sim/redhook.py:beat`. What you
see is the real hub (`evac/hub.py`): the router, the store, Muse, SAM, the kit. Only the neighbours' phones are
simulated.

| # | Beat | What the system does | Voice-over |
| --- | --- | --- | --- |
| 0 | Setup | 43 people, the flood zone around Van Brunt and Beard St, the hub | "9:40 at night, hour three of the storm. Rosa's laptop at the Neighbors Hub has the only working connection. The 42 phones around her can only text." |
| 1 | The alert | the city's warning, re-sent to everyone as a signed kit message: words + map + signature; those inside the zone get their own way out | "The city's flood warning reaches the hub and goes back out to every phone as ordinary texts: the words, a map the kit draws, and a signature." |
| 2 | A rumour | a forwarded copy saying "go to the Navy Yard" | "A forwarded copy says go to the Navy Yard. One phrase changed, and the kit says it's not genuine." |
| 3 | Check-in | everyone taps safe / need help / someone hurt; pins fill the map; Muse suggests who to help first | "One tap in the kit sends status, people, battery and GPS. Muse reads every check-in and suggests who to help first." |
| 4 | A volunteer | Dani is sent to Mr. Alvarez on a walk that avoids the floodwater; it opens in a map app | "Dani goes to Mr. Alvarez. The walk keeps out of the water, and it opens in an offline map app." |
| 5 | A photo by text | the Okafors' photo arrives at 128 px; Muse and SAM rate it critical (a downed line); the hub asks their phone for a closer look and the phone crops its own original | "A photo arrives as a dozen texts. Muse and SAM flag a downed power line. Rosa asks for a closer look, and their phone sends back just the wire." |
| 6 | Mark the danger | one click: a closure on the map, Dani re-routed around it, 17 neighbours nearby warned | "One click marks the danger. Dani's route bends around it, and everyone nearby is warned." |
| 7 | Her language | Luísa's check-in is in Portuguese; the hub answers in Portuguese, packed so accents don't cost double; June goes to her | "Luísa writes in Portuguese, and the hub answers in Portuguese." |
| 8 | Midnight | volunteers arrive; everyone accounted for; the night's whole data bill | "Everyone accounted for. The whole night, every text in and out, is a few percent of one phone photo." |

Closing line: **"Every phone already speaks SMS. One good connection is enough for a whole neighbourhood."**

## What's real and what's simulated

- **Real:**
  - Every message the hub sends or receives goes through the router, the store and the kit codes.
  - Muse Spark reads the alert, rewrites the instructions, triages photos, picks who to help first and translates.
  - SAM 3.1 finds what to zoom into.
  - Routes are computed on OpenStreetMap streets.
  - Signatures are real Ed25519.
- **Simulated:**
  - The neighbours' phones: they check in, send photos and answer zoom requests automatically, the way a person using the kit would.
  - The photos: made with Muse Image for the drill (`sim/samples/redhook/`).
  - The volunteers' walking: moved along their routes.
  - The texts: no SMS gateway yet, so the hub's "sent" messages go to the simulated phones.

## Running it

```bash
.venv/bin/python sim/server.py
```

Open http://127.0.0.1:8765/dashboard in a window about 1920 × 1080 and press the beats in order. The first alert of
a session takes about 30 s (six personal routes); the rest take seconds. Streets for Red Hook are cached in
`cache/osm/` after the first run, because Overpass is slow.

## The video

To record it by hand (screen recording plus a voice-over), open http://127.0.0.1:8765/demo in a 1920 × 1080
window. It plays four scenes, the three-way comparison, look closer, the signed alert, and check-in with a photo, one
step per → key, with a caption for each step (C hides them). Scenes 3 and 4 are this drill, seen from the Okafors'
phone beside the dashboard. The voice-over, section by section with what is on screen, is [`SCRIPT.md`](SCRIPT.md).

To render the drill from screenshots instead, with the simulator running:

```bash
.venv/bin/python demo/video/make_video.py --capture
```

It runs every beat against the server and screenshots the dashboard with headless Chrome at 2× (`capture.py`), turns
the shots into scenes (captions, title cards, slow zooms, crossfades) and encodes `out/demo/redhook/redhook.mp4` with
AVFoundation (`encode.swift`, compiled on first use). The scenes are data in `make_video.py`; after changing a caption
or a duration, run it without `--capture` to re-cut from the same screenshots.
