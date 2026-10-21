"""Device catalog and step-program (mode) building for recipe creation.

This server supports the Monsieur Cuisine **Smart** (MC3.0) only - its program
set, the logic that turns a caller's step dict into the API `mode`/
`deviceSettings` payload, and localized default step titles.
"""
import re
from collections.abc import Sequence


# The recipe-creation API identifies the device by a numeric deviceTypeId; 13 is
# the Monsieur Cuisine Smart (MC3.0), the only device this server supports.
MC_SMART_DEVICE_ID = 13

# Device programs (automatic modes) that a recipe step can run, keyed by the
# API's snake_case program name, with the parameters each one accepts. Used to
# build a step's `mode` object and to expose valid programs to callers. This is
# the Monsieur Cuisine Smart (MC3.0) set (the only supported device). "time" is
# always in SECONDS in the payload. Discovered from the create-recipe UI and
# verified against the API; the API enforces the exact numeric ranges.
# NOTE: the create_recipe tool docstring in server.py mirrors this list so
# callers see it without a tool call - keep the two in sync when editing.
DEVICE_PROGRAMS: dict[str, dict] = {
    "customized": {"params": ["temperature_celsius", "speed", "time_seconds", "reverse"]},
    "kneading": {"params": ["time_seconds"]},
    "kneading_solid_dough": {"params": ["time_seconds"]},
    "kneading_soft_dough": {"params": ["time_seconds"]},
    "kneading_liquid_dough": {"params": ["time_seconds"]},
    "steaming": {"params": ["time_seconds"]},
    "roasting": {"params": ["temperature_celsius", "time_seconds"]},
    "scale": {"params": ["weight_grams"]},
    "sous_vide": {"params": ["temperature_celsius", "time_seconds"]},
    "slow_cooking": {"params": ["temperature_celsius", "time_seconds"]},
    "fermentation": {"params": ["temperature_celsius", "time_seconds"]},
    "rice_cooking": {"params": ["time_seconds"]},
    "food_processor": {"params": ["time_seconds"]},
    "puree": {"params": ["time_seconds"]},
    "smoothie": {"params": ["time_seconds"]},
    "turbo": {"params": ["time_seconds"]},
    # Speed ramp ("Rampe"): several speed levels run one after another, each for
    # its own time, e.g. 4->5->6->7 at 10 s each for smooth pureeing. Found in
    # official MC Smart recipes; stored as one deviceSettings entry per level.
    "ramp": {"params": ["ramp_steps"]},
    "cooking_eggs": {
        "params": ["egg_size", "egg_texture"],
        "egg_sizes": ["small", "medium", "large"],
        # Backend enum is snake_case: the web app displays "waxySoft" but sends
        # "waxy_soft". "waxySoft" is normalized to "waxy_soft" (see _egg_texture).
        "egg_textures": ["soft", "waxy_soft", "hard"],
    },
    # Backend enum for the precleaning duration is "short" / "long" (the web app
    # labels the second one differently, but the stored value is "long").
    "precleaning": {"params": ["cleaning_duration"], "cleaning_durations": ["short", "long"]},
}

# Cooking time (minutes) the device uses for eggs, by size then texture. The
# create API still wants an explicit time on the step, so for cooking_eggs the
# tool derives it from egg_size/egg_texture when the caller doesn't set one.
EGG_COOK_MINUTES = {
    "small": {"soft": 8, "waxy_soft": 9, "hard": 15},
    "medium": {"soft": 10, "waxy_soft": 12, "hard": 18},
    "large": {"soft": 11, "waxy_soft": 13, "hard": 18},
}
PRECLEANING_DEFAULT_SECONDS = 60


def _egg_texture(value: str) -> str:
    """Normalize an egg-texture value to the backend snake_case enum.

    The web app displays "waxySoft" but stores "waxy_soft"; accept either form
    (and any camelCase) and convert to the snake_case the API validates against.
    """
    normalized = re.sub(r"[A-Z]", lambda m: "_" + m.group(0).lower(), value)
    valid = DEVICE_PROGRAMS["cooking_eggs"]["egg_textures"]
    if normalized not in valid:
        raise ValueError(f"Invalid egg_texture {value!r}, expected one of {valid}")
    return normalized


def _normalize_program(program: str) -> str:
    """Normalize a device-program name to the API's snake_case form, accepting
    either snake_case ("sous_vide") or the camelCase form used in the UI code
    ("sousVide"). Raises ValueError if the program is unknown.
    """
    snake = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", program).lower()
    if snake not in DEVICE_PROGRAMS:
        raise ValueError(
            f"Unknown device program {program!r}. Valid programs: {sorted(DEVICE_PROGRAMS)}"
        )
    return snake


def _build_step_mode(step: dict) -> dict | None:
    """Build a step's `mode` object (the device program) from a step dict, or
    return None if the step has no program.

    Expected optional keys on `step`: program, temperature_celsius, speed,
    time_seconds, weight_grams, reverse, egg_size, egg_texture,
    cleaning_duration. `time_seconds` is passed straight through (the API stores
    time in seconds).
    """
    program = step.get("program")
    if not program:
        return None

    program = _normalize_program(program)

    if program == "ramp":
        ramp_steps = step.get("ramp_steps") or []
        if len(ramp_steps) < 2:
            raise ValueError(
                "Device program 'ramp' needs ramp_steps with at least two "
                "{'speed', 'time_seconds'} entries"
            )
        settings = [
            {"order": order, "speed": int(level["speed"]), "time": int(level["time_seconds"]),
             "temperature": 0}
            for order, level in enumerate(ramp_steps)
        ]
        return {"type": "ramp", "modeSetting": None, "deviceSettings": settings}

    time_seconds = step.get("time_seconds")
    mode_setting: dict | None = None
    if program == "cooking_eggs":
        size = step.get("egg_size", "medium")
        texture = _egg_texture(step.get("egg_texture", "soft"))
        mode_setting = {"size": size, "texture": texture}
        if time_seconds is None:
            time_seconds = EGG_COOK_MINUTES.get(size, {}).get(texture, 10) * 60
    elif program == "precleaning":
        duration = step.get("cleaning_duration", "short")
        valid_durations = DEVICE_PROGRAMS["precleaning"]["cleaning_durations"]
        if duration not in valid_durations:
            raise ValueError(f"Invalid cleaning_duration {duration!r}, expected one of {valid_durations}")
        mode_setting = {"duration": duration}
        if time_seconds is None:
            time_seconds = PRECLEANING_DEFAULT_SECONDS

    setting: dict = {"order": 0}
    if time_seconds is not None:
        setting["time"] = time_seconds
    if step.get("speed") is not None:
        setting["speed"] = step["speed"]
    if step.get("temperature_celsius") is not None:
        setting["temperature"] = step["temperature_celsius"]
    if step.get("weight_grams") is not None:
        setting["weight"] = step["weight_grams"]

    # The API rejects a deviceSettings entry that carries no actual value.
    if len(setting) == 1:  # only "order"
        raise ValueError(
            f"Device program {program!r} needs at least one setting "
            f"(one of {DEVICE_PROGRAMS[program]['params']})"
        )

    # Rotation direction applies only to the "customized" program and is stored
    # as `clockwise` on the deviceSettings entry (NOT as a `reverse` flag on the
    # mode). clockwise=false is the counter-clockwise "gentle stir" / reverse
    # mode; the web app always includes it for customized steps, defaulting to
    # clockwise=true (normal rotation).
    if program == "customized":
        setting["clockwise"] = not bool(step.get("reverse"))

    mode: dict = {"type": program, "modeSetting": mode_setting, "deviceSettings": [setting]}
    return mode


# Localized "Step" word used to auto-fill a step title when the caller does not
# supply one. The web app requires every step to have a non-empty title (min 1
# char); without it the recipe stays an incomplete draft that cannot be
# published (not even privately). Keyed by the language part of the locale.
_STEP_WORD_BY_LANGUAGE = {
    "pl": "Krok",
    "en": "Step",
    "de": "Schritt",
    "fr": "Étape",
    "es": "Paso",
    "it": "Passo",
    "nl": "Stap",
    "cs": "Krok",
    "sk": "Krok",
}


def _default_step_title(language: str, index: int) -> str:
    """Build a non-empty default title like "Krok 1" for a step with no title.

    Steps must have a title or the recipe cannot be published, so callers that
    pass only step text still get a valid, ordinal title (localized by the
    recipe language, falling back to English "Step")."""
    word = _STEP_WORD_BY_LANGUAGE.get(language.split("-")[0].lower(), "Step")
    return f"{word} {index}"


def _build_api_steps(steps: Sequence[dict | str] | None, language: str) -> list[dict]:
    """Turn caller step dicts/strings into the API `steps` shape.

    Each step becomes {"title", "description", "mode"?}. A step needs a non-empty
    title to be publishable, so a missing one gets a localized ordinal default.
    A plain string is shorthand for a text-only step.
    """
    api_steps = []
    for step_index, raw_step in enumerate(steps or [], start=1):
        step = {"text": raw_step} if isinstance(raw_step, str) else raw_step
        step_title = step.get("title") or _default_step_title(language, step_index)
        api_step = {"title": step_title, "description": step.get("text", "")}
        mode = _build_step_mode(step)
        if mode is not None:
            api_step["mode"] = mode
        api_steps.append(api_step)
    return api_steps
