"""The one thing every mode produces and every renderer reads: what to draw, not how.

A mode (an alert, walking directions, ...) gathers data and fills a Scene; `evac.render` turns the same Scene
into each version (SMS text map, monospace map, picture map). Renderers never fetch data or route; they only
draw what is here, so all versions of one reply show the same route, zone and roads.
"""
from dataclasses import dataclass, field
from typing import Optional

from . import grid

FRAME_COLS = 40  # default width of the canonical north-up frame: the SMS map's grid, whose bbox the aerial covers


@dataclass
class Scene:
    you: tuple                                   # the reader's (lat, lon)
    half_m: float                                # half the width (and height) of the view, in metres
    text: str = ""                               # the message: instructions, directions
    roads: list = field(default_factory=list)    # OSM roads {"name", "kind", "pts"}
    closures: list = field(default_factory=list)  # closed segments [{"road", "from", "to"}]
    hazard: Optional[str] = None                 # "fire", "wildfire", ... (None: no danger zone)
    zone: Optional[tuple] = None                 # ((lat, lon), radius_m) of the danger zone
    goal: Optional[tuple] = None                 # (name, (lat, lon)): a shelter, or a destination if no zone
    route: list = field(default_factory=list)    # [(lat, lon), ...] from you towards the goal or safety
    aerial: object = None                        # PIL image covering frame().bbox(), or None
    masks: Optional[dict] = None                 # terrain masks for `aerial` (segment.segment)
    walk: bool = False                           # routed on foot: no terrain, footpaths not drawn
    street_grid: bool = False                    # Manhattan intersection: SMS map on the avenue/street grid
    title: str = ""                              # picture banner
    summary: str = ""                            # picture banner subtitle
    cols: int = FRAME_COLS                       # columns of the canonical frame (the SMS routing grid)
    center: Optional[tuple] = None               # (lat, lon) the view is centred on (None: you)
    focus: Optional[list] = None                 # the roads to draw (evac/focus.py); None draws every road
    marks: list = field(default_factory=list)    # [(number, (lat, lon))]: where each numbered step starts
    landmarks: list = field(default_factory=list)  # [(name, (lat, lon))]: things to look out for on the way

    @property
    def alert(self):
        """An alert (danger zone, shelter, way out) rather than plain directions (destination, route)."""
        return self.zone is not None

    @property
    def shown(self):
        """The roads the maps draw."""
        return self.roads if self.focus is None else self.focus

    def frame(self):
        """The canonical north-up grid over the view with terrain, the drawn roads, closures and the zone."""
        g = grid.Grid(self.center or self.you, self.half_m, self.half_m, self.cols)
        if self.walk:
            g.terrain[:] = grid.BUILDING  # off-street cells are expensive: stay on streets and paths
        elif self.masks is not None:
            g.add_terrain(self.masks)
        g.add_roads(self.shown, self.closures)
        if self.zone:
            g.add_hazard_circle(*self.zone)
        return g

    def bbox(self):
        """What the aerial image must cover."""
        return grid.Grid(self.center or self.you, self.half_m, self.half_m, self.cols).bbox()
