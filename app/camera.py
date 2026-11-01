"""Rig de câmera virtual: cada movimento vira linguagem de cinematografia no prompt."""
from pydantic import BaseModel

MOVES = {
    "static":     {"label": "Travada",            "prompt": "locked-off static camera on tripod"},
    "push_in":    {"label": "Push in",            "prompt": "slow subtle push in towards the subject"},
    "dolly_in":   {"label": "Dolly in",           "prompt": "dolly in towards the subject"},
    "dolly_out":  {"label": "Dolly out",          "prompt": "dolly out revealing the scene"},
    "truck_left": {"label": "Travelling esq.",    "prompt": "lateral tracking shot moving left"},
    "truck_right": {"label": "Travelling dir.",   "prompt": "lateral tracking shot moving right"},
    "pan_left":   {"label": "Pan esq.",           "prompt": "smooth camera pan to the left"},
    "pan_right":  {"label": "Pan dir.",           "prompt": "smooth camera pan to the right"},
    "tilt_up":    {"label": "Tilt up",            "prompt": "camera tilts up"},
    "tilt_down":  {"label": "Tilt down",          "prompt": "camera tilts down"},
    "crane_up":   {"label": "Grua sobe",          "prompt": "crane shot rising up above the scene"},
    "crane_down": {"label": "Grua desce",         "prompt": "crane shot descending towards the subject"},
    "zoom_in":    {"label": "Zoom in (lente)",    "prompt": "optical zoom in, focal length increasing"},
    "zoom_out":   {"label": "Zoom out (lente)",   "prompt": "optical zoom out, focal length decreasing"},
    "crash_zoom": {"label": "Crash zoom",         "prompt": "fast dramatic crash zoom in"},
    "dolly_zoom": {"label": "Dolly zoom",         "prompt": "vertigo dolly zoom, background stretches while subject stays the same size"},
    "whip_pan":   {"label": "Whip pan",           "prompt": "very fast whip pan with motion blur"},
    "orbit":      {"label": "Orbit",              "prompt": "camera orbits around the subject in an arc", "approx": True},
    "roll":       {"label": "Roll",               "prompt": "camera rolls, dutch angle"},
    "handheld":   {"label": "Handheld",           "prompt": "handheld documentary camera, subtle natural shake", "approx": True},
}

LENSES = {
    "24": "24mm wide-angle lens, deep perspective",
    "35": "35mm lens, natural cinematic perspective",
    "50": "50mm lens, shallow depth of field",
    "85": "85mm portrait lens, creamy bokeh, compressed background",
    "100m": "100mm macro lens, extreme close-up product detail",
}

ANGLES = {
    "eye": "eye-level angle",
    "low": "low angle hero shot looking up",
    "high": "high angle looking down",
    "top": "overhead top-down shot",
}

SPEEDS = {"lento": 0.5, "normal": 1.0, "rapido": 1.6}


class CameraSpec(BaseModel):
    move: str = "dolly_in"
    lens: str = "50"
    angle: str = "eye"
    speed: str = "lento"

    def framing(self) -> str:
        """Lente + ângulo: vale para still e vídeo."""
        return f"{LENSES.get(self.lens, '')}, {ANGLES.get(self.angle, '')}"

    def prompt_suffix(self) -> str:
        """Movimento + framing: só para os passos de vídeo."""
        m = MOVES.get(self.move, MOVES["static"])
        pace = {"lento": "slow-paced elegant motion", "rapido": "fast energetic motion"}.get(self.speed, "steady motion")
        return f"{m['prompt']}, {pace}, {self.framing()}"

    def label(self) -> str:
        return f"{MOVES.get(self.move, MOVES['static'])['label']} · {self.lens}mm · {self.speed}"


def catalog() -> dict:
    return {"moves": MOVES, "lenses": LENSES, "angles": ANGLES, "speeds": SPEEDS}
