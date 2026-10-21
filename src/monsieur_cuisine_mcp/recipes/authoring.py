"""Create and edit recipes, and fetch the creation option lists."""
import logging
import re
from collections.abc import Sequence

import httpx

from .. import auth
from ..config import (
    MC_API_DEFAULT_LANGUAGE,
    MC_API_REQUEST_TIMEOUT_SECONDS,
    MC_API_WRITE_TIMEOUT_SECONDS,
    MC_BASE_URL,
    MC_BROWSER_USER_AGENT,
    MC_CREATE_RECIPE_PATH,
    MC_WP_AJAX_URL,
)
from .client import MCRequestError, _call_proxy_api, _language_path_prefix
from .devices import DEVICE_PROGRAMS, MC_SMART_DEVICE_ID, _build_api_steps
from .ingredients import _build_ingredient_groups
from .lifecycle import _get_own_recipe
from .media import _upload_recipe_image
from .nutrients import _build_nutrients, _merge_nutrients
from .payloads import _empty_serving, _recipe_to_put_payload

logger = logging.getLogger(__name__)


async def get_recipe_creation_options(language: str = MC_API_DEFAULT_LANGUAGE) -> dict:
    """Fetch the valid option identifiers for creating a recipe in a given
    language: complexity levels, categories, serving units, ingredient units
    and device types.

    Requires a saved login session (run `uv run monsieur-cuisine-mcp-login`
    once beforehand) - raises auth.NotLoggedInError if none is found.

    `complexity` and `category` ids are language-specific and must be fetched
    again (with the matching language) whenever the creation language changes.
    The returned structure is normalized to just the fields create_recipe
    needs, not the raw WordPress taxonomy payload.
    """
    cookies = auth.load_session_cookies()
    prefix = _language_path_prefix(language)
    referer = f"{MC_BASE_URL}/{prefix}/{MC_CREATE_RECIPE_PATH}"
    headers = {
        "Accept-Language": language,
        "Referer": referer,
        "Origin": MC_BASE_URL,
        "User-Agent": MC_BROWSER_USER_AGENT,
    }
    logger.debug("Fetching recipe creation guide (referer=%s)", referer)

    async with httpx.AsyncClient(cookies=cookies, timeout=MC_API_REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.post(
            MC_WP_AJAX_URL, data={"action": "load_recipe_post"}, headers=headers
        )
    response.raise_for_status()
    guide = response.json().get("guide", {})

    options = {
        "complexity_levels": [
            {"id": lvl["id"], "name": lvl["name"], "level": int(lvl.get("term_order") or 0)}
            for lvl in guide.get("level_options", [])
        ],
        "categories": [
            {"id": cat["id"], "name": cat["name"]} for cat in guide.get("category_options", [])
        ],
        "serving_units": [u["unit_name"] for u in guide.get("serving_units", [])],
        "ingredient_units": [
            {"name": u["unit_name"], "short_name": u.get("unit_short_name")}
            for u in guide.get("ingredient_units", [])
        ],
        "device_types": [
            {"id": d["id"], "name": d["name"], "slug": d["slug"]}
            for d in guide.get("device_options", [])
        ],
        # Device programs a step can run, and the params each accepts. Not from
        # the WP guide - a static catalog for the Monsieur Cuisine smart set.
        "device_programs": DEVICE_PROGRAMS,
    }
    logger.debug(
        "Creation guide: %d complexity, %d categories, %d serving units",
        len(options["complexity_levels"]),
        len(options["categories"]),
        len(options["serving_units"]),
    )
    return options


async def create_recipe(
    title: str,
    description: str | None = None,
    complexity_id: int | None = None,
    category_ids: list[int] | None = None,
    servings_amount: float | None = None,
    servings_unit: str | None = None,
    prep_time_minutes: int | None = None,
    ready_time_minutes: int | None = None,
    calories: float | None = None,
    protein: float | None = None,
    carbohydrate: float | None = None,
    fat: float | None = None,
    ingredients: list[dict] | None = None,
    ingredient_groups: list[dict] | None = None,
    steps: Sequence[dict | str] | None = None,
    image_path: str | None = None,
    image_ai_generated: bool | None = None,
    language: str = MC_API_DEFAULT_LANGUAGE,
) -> dict:
    """Create a new user recipe as a DRAFT under the logged-in account.

    Requires a saved login session (run `uv run monsieur-cuisine-mcp-login`
    once beforehand) - raises auth.NotLoggedInError if none is found.

    The recipe is created for the Monsieur Cuisine Smart (MC3.0), the only
    supported device. Only `title` is required; every other field is optional (a
    draft does not need to be complete). The recipe is created with status
    "draft" and is NOT published. Publishing is a separate step: once the draft
    is complete, call
    publish_recipe (public review, or private=True for a private publish). A
    published recipe cannot be reverted to a draft; to take it down, switch it to
    private or delete it with delete_recipe.

    Pass `image_ai_generated=True` with an AI-generated photo so the site marks
    it as such (like the checkbox in the web app).

    Pass `image_path` (a local JPEG/PNG/WebP file) to attach a dish photo. It is
    uploaded to the recipe media service and set as both the recipe thumbnail and
    the details image, exactly like adding a cover photo in the web app. This is
    the photo a draft needs before it can be published.

    `complexity_id` and `category_ids` are language-specific; fetch valid values
    via get_recipe_creation_options(language) using the same language first.

    Ingredients are dicts like {"name": "flour", "amount": "200", "unit": "g"}.
    They are free text by default - the `name` is used verbatim, exactly like
    typing a custom ingredient in the web app without picking a suggestion.
    Pass an optional `system_ingredient_id` (from search_ingredients) only to
    link a specific database ingredient. `ingredients` go into the default
    general group (auto-named "General Ingredients", since a group needs a name
    to be publishable); `ingredient_groups` adds named groups, each
    {"name": <group name>, "ingredients": [ingredient dicts]} - give each a
    non-empty name so the recipe can be published.

    Each step is a dict {"text": <instruction>, ...optional program fields}; a
    plain string is also accepted as shorthand for {"text": <string>}. Add an
    optional "title" (short step heading) - every step needs a non-empty title
    to be publishable, so when omitted a localized ordinal ("Krok 1"/"Step 1")
    is filled in automatically. To attach an automatic device program to a step,
    add "program" (a name from
    DEVICE_PROGRAMS, e.g. "customized", "steaming", "sous_vide") plus the params
    that program uses: "temperature_celsius", "speed", "time_seconds" (SECONDS,
    not minutes), "weight_grams", "reverse" (counter-clockwise, customized only),
    and for "cooking_eggs" "egg_size"/"egg_texture", for "precleaning"
    "cleaning_duration". Steps without a "program" are plain text instructions.

    This is done in two requests, mirroring the web app: a small create call,
    then an update call carrying the serving size (times/ingredients/steps) and
    the cover photo. The create call is intentionally NOT retried, because the
    server can create the recipe even when the gateway reports a transient error;
    retrying would create duplicates.
    """
    base: dict = {
        "status": "draft",
        "source": "member",
        "languageLocale": language,
        "deviceTypeIds": [MC_SMART_DEVICE_ID],
        "title": title,
        "allowSocialSharing": False,
        "nutrients": _build_nutrients(calories, protein, carbohydrate, fat),
    }
    if description:
        base["description"] = description
    if complexity_id is not None:
        base["complexityId"] = complexity_id
    if category_ids:
        base["categoryIds"] = category_ids

    # Step 1: create the draft with the basic details.
    created = await _call_proxy_api(
        "api/v3/auth/user/recipes/", method="POST", language=language,
        payload=base, timeout=MC_API_WRITE_TIMEOUT_SECONDS,
    )
    recipe_id = created["data"]["recipe"]["id"]
    logger.info("Created draft recipe %s (%r)", recipe_id, title)

    # If a cover photo was supplied, upload it now so we can reference it in the
    # update payload below. Done after the draft exists (the media service is
    # independent of the recipe, but this keeps the writes ordered like the app).
    image_ref = None
    if image_path:
        media = await _upload_recipe_image(image_path, language, bool(image_ai_generated))
        image_ref = media["landscape_ref"]

    # Step 2: update the draft when there is serving detail and/or a cover photo.
    # The recipe PUT is a full replace, so the update carries everything at once.
    has_serving = any(
        v is not None
        for v in (servings_amount, servings_unit, prep_time_minutes, ready_time_minutes)
    ) or bool(ingredients) or bool(ingredient_groups) or bool(steps)

    if has_serving or image_ref is not None:
        update_payload = {**base}

        if has_serving:
            api_groups = _build_ingredient_groups(ingredients, ingredient_groups, language)
            api_steps = _build_api_steps(steps, language)

            serving = {
                "isDefault": True,
                "amount": servings_amount if servings_amount is not None else 0,
                "unit": servings_unit or "",
                "instruction": "",
                # The API requires these to be >= 1 whenever a serving size
                # exists, so a missing value falls back to 1 minute rather than 0.
                "preparationTime": prep_time_minutes if prep_time_minutes is not None else 1,
                "readyInTime": ready_time_minutes if ready_time_minutes is not None else 1,
                "steps": api_steps,
                "ingredientGroups": api_groups,
            }
            update_payload["servingSizes"] = [serving]

        if image_ref is not None:
            # The same media backs the small thumbnail and the large details
            # image, matching the web app when no separate portrait crop is made.
            update_payload["thumbnail"] = image_ref
            update_payload["detailsImage"] = image_ref

        await _call_proxy_api(
            f"api/v3/auth/user/recipes/{recipe_id}", method="PUT", language=language,
            payload=update_payload, timeout=MC_API_WRITE_TIMEOUT_SECONDS,
        )
        logger.info("Updated draft recipe %s with serving/photo details", recipe_id)

    # Fetch the final persisted recipe to return.
    final = await _call_proxy_api(
        f"api/v3/auth/user/recipes/{recipe_id}", method="GET", language=language
    )
    recipe = final.get("data", {}).get("recipe", {})
    image_url = (recipe.get("detailsImage") or {}).get("landscapeMedia", {})
    image_url = image_url.get("url") if image_url else None
    return {
        "recipe_id": recipe_id,
        "status": recipe.get("status"),
        "title": recipe.get("title"),
        "image_url": image_url,
        "edit_url": f"{MC_BASE_URL}/{_language_path_prefix(language)}/{MC_CREATE_RECIPE_PATH}?recipe_id={recipe_id}",
        "recipe": recipe,
    }


async def edit_recipe(
    recipe_id: int,
    title: str | None = None,
    description: str | None = None,
    complexity_id: int | None = None,
    category_ids: list[int] | None = None,
    servings_amount: float | None = None,
    servings_unit: str | None = None,
    prep_time_minutes: int | None = None,
    ready_time_minutes: int | None = None,
    calories: float | None = None,
    protein: float | None = None,
    carbohydrate: float | None = None,
    fat: float | None = None,
    ingredients: list[dict] | None = None,
    ingredient_groups: list[dict] | None = None,
    steps: Sequence[dict | str] | None = None,
    image_path: str | None = None,
    image_ai_generated: bool | None = None,
    language: str = MC_API_DEFAULT_LANGUAGE,
) -> dict:
    """Edit one of the logged-in user's own recipes. Only the fields you pass are
    changed; everything else is preserved.

    Requires a saved login session (run `uv run monsieur-cuisine-mcp-login`
    once beforehand) - raises auth.NotLoggedInError if none is found.

    The recipe is fetched and re-saved as a whole (the update endpoint is a full
    replace), so any field left as None keeps its current value. The recipe keeps
    its current status; editing a published recipe re-saves it in place (the
    server still validates completeness and rejects an edit that would make it
    incomplete).

    Field semantics mirror create_recipe. Collection fields REPLACE (not merge)
    the current value when supplied: passing `steps` replaces all steps, and
    passing `ingredients`/`ingredient_groups` replaces all ingredient groups.
    Nutrients are the exception - only the specific values you pass
    (calories/protein/carbohydrate/fat) are overwritten, the rest stay. Pass
    `image_path` to replace the cover photo. `image_ai_generated` marks the
    (new or existing) cover photo as AI-generated or not; None keeps the flag.
    """
    recipe = await _get_own_recipe(recipe_id, language, action="edited")
    payload = _recipe_to_put_payload(recipe)

    if title is not None:
        payload["title"] = title
    if description is not None:
        payload["description"] = description
    if complexity_id is not None:
        payload["complexityId"] = complexity_id
    if category_ids is not None:
        payload["categoryIds"] = category_ids
    if any(v is not None for v in (calories, protein, carbohydrate, fat)):
        payload["nutrients"] = _merge_nutrients(
            payload.get("nutrients"), calories, protein, carbohydrate, fat
        )
    if image_path:
        media = await _upload_recipe_image(image_path, language, bool(image_ai_generated))
        payload["thumbnail"] = media["landscape_ref"]
        payload["detailsImage"] = media["landscape_ref"]
    elif image_ai_generated is not None:
        # Only (un)mark the existing cover photo as AI-generated.
        for key in ("thumbnail", "detailsImage"):
            if payload.get(key):
                payload[key]["portraitIsAiGenerated"] = image_ai_generated
                payload[key]["landscapeIsAiGenerated"] = image_ai_generated

    # Serving-level edits operate on the default (first) serving size.
    serving_changed = any(
        v is not None
        for v in (servings_amount, servings_unit, prep_time_minutes, ready_time_minutes)
    ) or steps is not None or ingredients is not None or ingredient_groups is not None
    if serving_changed:
        servings = payload.get("servingSizes") or [_empty_serving()]
        serving = servings[0]
        if servings_amount is not None:
            serving["amount"] = servings_amount
        if servings_unit is not None:
            serving["unit"] = servings_unit
        if prep_time_minutes is not None:
            serving["preparationTime"] = prep_time_minutes
        if ready_time_minutes is not None:
            serving["readyInTime"] = ready_time_minutes
        if steps is not None:
            serving["steps"] = _build_api_steps(steps, language)
        if ingredients is not None or ingredient_groups is not None:
            serving["ingredientGroups"] = _build_ingredient_groups(
                ingredients, ingredient_groups, language
            )
        payload["servingSizes"] = servings

    try:
        await _call_proxy_api(
            f"api/v3/auth/user/recipes/{recipe_id}", method="PUT", language=language,
            payload=payload, timeout=MC_API_WRITE_TIMEOUT_SECONDS,
        )
    except MCRequestError as exc:
        if "not complete" in str(exc):
            detail = re.search(r"not complete[^\"}]*", str(exc))
            raise MCRequestError(
                f"The edit to recipe {recipe_id} would leave it incomplete: "
                f"{detail.group(0) if detail else 'the recipe is not complete'}. "
                "A published recipe must stay complete; adjust the change and retry."
            ) from exc
        raise
    logger.info("Edited recipe %s", recipe_id)

    final = await _call_proxy_api(
        f"api/v3/auth/user/recipes/{recipe_id}", method="GET", language=language
    )
    updated = final.get("data", {}).get("recipe", {})
    image_url = (updated.get("detailsImage") or {}).get("landscapeMedia", {})
    image_url = image_url.get("url") if image_url else None
    return {
        "recipe_id": recipe_id,
        "status": updated.get("status"),
        "title": updated.get("title"),
        "image_url": image_url,
        "edit_url": f"{MC_BASE_URL}/{_language_path_prefix(language)}/{MC_CREATE_RECIPE_PATH}?recipe_id={recipe_id}",
        "recipe": updated,
    }
