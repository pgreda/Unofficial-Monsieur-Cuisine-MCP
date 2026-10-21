"""Ingredient search and ingredient-group payload building for recipes."""
import logging
import urllib.parse

from ..config import MC_API_DEFAULT_LANGUAGE
from .client import _call_proxy_api

logger = logging.getLogger(__name__)


async def search_ingredients(
    query: str, count: int = 10, language: str = MC_API_DEFAULT_LANGUAGE
) -> dict:
    """Search the Monsieur Cuisine ingredient database for a term, returning
    matching ingredients with their `systemIngredientId`.

    Requires a saved login session (run `uv run monsieur-cuisine-mcp-login`
    once beforehand) - raises auth.NotLoggedInError if none is found.

    The `systemIngredientId` values are what create_recipe needs for its
    ingredients: the create API only accepts ingredients that map to a real
    database ingredient (id >= 1), not free text. Ingredient names (and thus
    the ids) are language-specific.
    """
    qs = urllib.parse.urlencode({"q": query, "count": count})
    data = await _call_proxy_api(
        f"api/v1/ingredients/quick-search?{qs}", method="GET", language=language
    )
    ingredients = data.get("data", {}).get("ingredients", [])
    logger.debug("Ingredient search %r returned %d results", query, len(ingredients))
    return data


def _build_ingredient(ingredient: dict, order: int) -> dict:
    """Turn a caller-supplied ingredient dict into the API's ingredient shape.

    Ingredients are free text by default: the `name` is sent as-is and no
    database match is required (the create API stores it with a null
    systemIngredientId, exactly like typing a custom ingredient in the web app
    without picking a suggestion). Pass an explicit `system_ingredient_id`
    (from search_ingredients) only when you want to link to a specific database
    ingredient; it must be a positive id (0/-1 are rejected by the API).
    """
    name = ingredient.get("name")
    if not name:
        raise ValueError("Each ingredient needs a 'name'")

    api_ingredient: dict = {
        "name": name,
        "amount": str(ingredient.get("amount", "")),
        "unit": ingredient.get("unit", ""),
        "order": ingredient.get("order", order),
    }
    # Shown as "optional" in the app, e.g. garnish or a binder to taste.
    if ingredient.get("optional"):
        api_ingredient["isOptional"] = True
    system_id = ingredient.get("system_ingredient_id")
    if system_id is not None:
        if system_id < 1:
            raise ValueError(
                f"system_ingredient_id must be a positive id from search_ingredients, got {system_id!r}. "
                "Omit it to add a free-text ingredient."
            )
        api_ingredient["systemIngredientId"] = system_id
    return api_ingredient


# Localized "General Ingredients" label the web app gives the default/general
# ingredient group (window.mc3I18n.generalIngredients). Every ingredient group
# needs a non-empty name to publish a recipe, so the flat-ingredients default
# group is named with this instead of "" (mirroring the app). Locales that the
# site itself leaves untranslated (en/es/sk) fall back to the English label.
_GENERAL_INGREDIENTS_BY_LANGUAGE = {
    "pl": "Składniki ogólne",
    "en": "General Ingredients",
    "de": "Allgemeine Zutaten",
    "fr": "Ingrédients courants",
    "it": "Ingredienti generali",
    "nl": "Algemene ingrediënten",
    "cs": "Obecné suroviny",
}


def _default_ingredient_group_name(language: str) -> str:
    """Localized name for the default/general ingredient group, matching the web
    app. A named default group keeps the recipe publishable (an empty group name
    blocks publishing)."""
    return _GENERAL_INGREDIENTS_BY_LANGUAGE.get(
        language.split("-")[0].lower(), "General Ingredients"
    )


def _build_ingredient_groups(
    ingredients: list[dict] | None,
    ingredient_groups: list[dict] | None,
    language: str = MC_API_DEFAULT_LANGUAGE,
) -> list[dict]:
    """Assemble the API `ingredientGroups` list from a flat ingredient list
    (placed in the default general group) and/or explicit named groups.

    Each named group is {"name": str, "ingredients": [ingredient dicts]}. The
    flat `ingredients` go into the default group, which is named with the
    localized "General Ingredients" label (like the web app) so the recipe stays
    publishable - an empty group name blocks publishing.
    """
    groups: list[dict] = []
    if ingredients:
        groups.append(
            {"name": _default_ingredient_group_name(language), "ingredients": ingredients}
        )
    for grp in ingredient_groups or []:
        groups.append({"name": grp.get("name", ""), "ingredients": grp.get("ingredients", [])})

    api_groups = []
    for index, grp in enumerate(groups):
        api_ingredients = [
            _build_ingredient(ing, order) for order, ing in enumerate(grp["ingredients"])
        ]
        # The API requires exactly one default group; the first group is it
        # (matching the web app, where group 0 is the general/default group).
        api_groups.append(
            {"name": grp["name"], "isDefault": index == 0,
             "order": index, "ingredients": api_ingredients}
        )
    return api_groups
