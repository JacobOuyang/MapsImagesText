# The demo

The demo environment for MuZip (this branch only). Start the simulator and open the stage at 1920 × 1080:

```bash
.venv/bin/python sim/server.py
```

http://127.0.0.1:8765/demo steps through four scenes (→ or space for the next step, 1–4 to jump, C to hide the
captions). Everything under the pages is real: the router, the look-closer loop, Muse Spark and SAM 3.1. Only the
phones (and the drone) are simulated, and they crop their own originals exactly as MuZip does.

| Scene | Page | What it shows |
|---|---|---|
| 1 Three ways | `/compare` | One question, three ways: the photo, WebP squeezed into MuZip's texts, MuZip; Muse's measured answers (5 runs each). The data is `demo/three_ways/` (a copy of the last `evals/kit/three_ways.py` run), so it works on a fresh clone. |
| 2 Look closer | `/closer` | A phone asks which bottle is for a headache; Muse asks for one high-detail close-up of the shelves and answers. |
| 3 Group chat | `/group` | A simulated WhatsApp group: Ana shares a photo of her flooded street (the group gets the sketch), Ben and Chen ask Muse about different details, and Muse asks Ana's phone for different close-ups each time. |
| 4 Drone | `/closer?device=drone` | The same loop on a drone over a flood: Muse reads the sheet on a roof, NEED INSULIN 2 PEOPLE. |

Each page also works on its own (`/group?auto=1` plays the whole chat). The real WhatsApp run is recorded separately
with MuZip on a Mac (see the main README). The voice-over, section by section with what is on screen, is
[SCRIPT.md](SCRIPT.md).

Tested end to end with the live models: scene 2 answered the ibuprofen in one close-up (69 texts); scene 3 NO PARKING
2AM–6AM (83 texts) and Warehouse 12 (49 texts), 140 texts for the whole chat; scene 4 NEED INSULIN 2 PEOPLE in two
close-ups (126 texts).

Photos: `sim/samples/redhook/flooded_corner.jpg` and `sim/samples/field/` were made with Muse Image (prompts in
`sim/samples/field/prompts.json`).
