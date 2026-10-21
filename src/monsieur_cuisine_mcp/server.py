import argparse
import logging

from fastmcp import FastMCP

from . import recipes
from .config import MC_API_DEFAULT_LANGUAGE
from .logging_config import setup_logging

logger = logging.getLogger(__name__)

mcp = FastMCP("Monsieur Cuisine MCP")


@mcp.tool
async def search_recipes(
    query: str | None = None,
    device_type: str | None = None,
    categories: list[str] | None = None,
    ingredient_categories: list[str] | None = None,
    complexity: str | None = None,
    duration_min: int | None = None,
    duration_max: int | None = None,
    rating_min: float | None = None,
    rating_max: float | None = None,
    media: str | None = None,
    source: str | None = None,
    calories_min: float | None = None,
    calories_max: float | None = None,
    protein_min: float | None = None,
    protein_max: float | None = None,
    carbohydrate_min: float | None = None,
    carbohydrate_max: float | None = None,
    fat_min: float | None = None,
    fat_max: float | None = None,
    sort_field: str = "lastUpdated",
    sort_direction: str = "DESC",
    page: int = 1,
    language: str = MC_API_DEFAULT_LANGUAGE,
) -> dict:
    """Search Monsieur Cuisine recipes by free text and/or filters.

    IMPORTANT: `categories`, `ingredient_categories` and `complexity` are
    language-dependent - their valid identifiers differ per `language` (e.g.
    category IDs and complexity labels for "pl-PL" are entirely different
    from "en-US"). Always call list_recipe_filters(language) first with the
    *same* language to get valid identifiers for these three arguments.

    Each result includes a `url` field pointing at the recipe's web page -
    do not fetch that page directly. Instead, pass the result's `id` to the
    get_recipe tool, which returns the same data as clean JSON.

    Args:
        query: Free-text search term (e.g. "chicken").
        device_type: Filter to a device; only "MC3.0" (Monsieur Cuisine Smart)
            is supported. Omit to search recipes for any device.
        categories: Meal-type category IDs to filter by (e.g. main courses,
            desserts, vegan). Language-dependent - see list_recipe_filters.
        ingredient_categories: Ingredient category IDs to filter by (e.g.
            fish, beef, vegetables). Language-dependent - see
            list_recipe_filters.
        complexity: Recipe difficulty label. Language-dependent - see
            list_recipe_filters (e.g. "Easy" for "en-US", "łatwe" for "pl-PL").
        duration_min: Minimum total duration in minutes.
        duration_max: Maximum total duration in minutes.
        rating_min: Minimum average rating (0-5).
        rating_max: Maximum average rating (0-5).
        media: "video" to only return recipes with a video.
        source: Recipe source, one of "official" (Monsieur Cuisine) or
            "member" (community-submitted).
        calories_min: Minimum energy value per serving (kcal).
        calories_max: Maximum energy value per serving (kcal).
        protein_min: Minimum protein per serving (g).
        protein_max: Maximum protein per serving (g).
        carbohydrate_min: Minimum carbohydrate per serving (g).
        carbohydrate_max: Maximum carbohydrate per serving (g).
        fat_min: Minimum fat per serving (g).
        fat_max: Maximum fat per serving (g).
        sort_field: Field to sort by, e.g. "lastUpdated", "rating", "popularity".
        sort_direction: "ASC" or "DESC".
        page: 1-based page number (20 results per page).
        language: Result language/locale, e.g. "pl-PL", "en-US".
    """
    return await recipes.search_recipes(
        query=query,
        device_type=device_type,
        categories=categories,
        ingredient_categories=ingredient_categories,
        complexity=complexity,
        duration_min=duration_min,
        duration_max=duration_max,
        rating_min=rating_min,
        rating_max=rating_max,
        media=media,
        source=source,
        calories_min=calories_min,
        calories_max=calories_max,
        protein_min=protein_min,
        protein_max=protein_max,
        carbohydrate_min=carbohydrate_min,
        carbohydrate_max=carbohydrate_max,
        fat_min=fat_min,
        fat_max=fat_max,
        sort_field=sort_field,
        sort_direction=sort_direction,
        page=page,
        language=language,
    )


@mcp.tool
async def list_recipe_filters(language: str = MC_API_DEFAULT_LANGUAGE) -> dict:
    """List available recipe search filters and their valid option
    identifiers for a given language.

    Call this before search_recipes whenever you need to pass `categories`,
    `ingredient_categories` or `complexity`, since their valid identifiers
    are specific to `language` and differ between locales.

    Args:
        language: Locale to fetch filters for, e.g. "pl-PL", "en-US". Must
            match the `language` you intend to pass to search_recipes.
    """
    return await recipes.get_recipe_filters(language=language)


@mcp.tool
async def get_recipe(recipe_id: str, language: str = MC_API_DEFAULT_LANGUAGE) -> dict:
    """Fetch full details for a single recipe: ingredients, step-by-step
    instructions per serving size, and nutrients (calories/protein/
    carbohydrate/fat).

    Always call this tool to get recipe details instead of fetching the
    recipe's web page directly - the web page requires a headless browser to
    render and does not expose this data as clean JSON.

    Works for public recipes without login. It also reads the caller's OWN
    unpublished recipes (draft / private-publish) - those aren't served by the
    public API, so it falls back to the authenticated endpoint, which needs a
    saved login session. (Own recipes come back in a slightly different shape.)

    Args:
        recipe_id: Numeric recipe id, from the `id` field of a search_recipes
            or list_my_recipes result (not the URL slug).
        language: Locale to fetch the recipe in, e.g. "pl-PL", "en-US".
    """
    return await recipes.get_recipe(recipe_id=recipe_id, language=language)


@mcp.tool
async def list_favorite_recipes(
    page: int = 1,
    page_size: int = 12,
    sort_field: str = "date",
    sort_direction: str = "DESC",
    language: str = MC_API_DEFAULT_LANGUAGE,
) -> dict:
    """List the logged-in user's favorite (bookmarked) recipes, with full
    recipe details for each one.

    Requires a saved login session: run `uv run monsieur-cuisine-mcp-login`
    once beforehand to log in manually in a browser window. If no session is
    saved, this tool raises an error telling the user to do that.

    Each result's top-level `translationId` field is what remove_favorite_recipe
    expects - not the nested recipe's `id` field.

    Args:
        page: 1-based page number.
        page_size: Number of favorites per page.
        sort_field: Field to sort by, e.g. "date".
        sort_direction: "ASC" or "DESC".
        language: Result language/locale, e.g. "pl-PL", "en-US".
    """
    return await recipes.list_favorite_recipes(
        page=page,
        page_size=page_size,
        sort_field=sort_field,
        sort_direction=sort_direction,
        language=language,
    )


@mcp.tool
async def list_my_recipes(
    status: str | list[str] | None = None,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    sort_field: str = "title",
    sort_direction: str = "ASC",
    language: str = MC_API_DEFAULT_LANGUAGE,
) -> dict:
    """List the logged-in user's OWN recipes - the web app's "My recipes" page.

    This is the recipes the user authored (drafts, in-review, published and
    rejected), which is different from list_favorite_recipes (recipes the user
    bookmarked). Each entry's `id` is the numeric recipe id used by delete_recipe
    and get_recipe. Trashed/deleted recipes are never listed.

    Requires a saved login session: run `uv run monsieur-cuisine-mcp-login`
    once beforehand. If no session is saved, this tool raises an error telling
    the user to do that.

    Args:
        status: Filter by recipe status. Omit for the default "all" view (every
            status except trashed). One value or a list, from "draft", "pending"
            (in review), "private-publish", "publish", "rejected", "approved".
        page: 1-based page number.
        page_size: Number of recipes per page (default 20).
        keyword: Optional free-text search over the user's own recipes.
        sort_field: Field to sort by (default "title").
        sort_direction: "ASC" or "DESC" (default "ASC").
        language: Result language/locale, e.g. "pl-PL", "en-US".
    """
    return await recipes.list_my_recipes(
        status=status,
        page=page,
        page_size=page_size,
        keyword=keyword,
        sort_field=sort_field,
        sort_direction=sort_direction,
        language=language,
    )


@mcp.tool
async def add_favorite_recipe(translation_id: int, language: str = MC_API_DEFAULT_LANGUAGE) -> dict:
    """Add a recipe to the logged-in user's favorites (bookmarks).

    Requires a saved login session: run `uv run monsieur-cuisine-mcp-login`
    once beforehand to log in manually in a browser window. If no session is
    saved, this tool raises an error telling the user to do that.

    IMPORTANT: `translation_id` is the recipe's *translationId* field from a
    search_recipes/get_recipe result - NOT the numeric recipe `id` used by
    those same tools. Always call search_recipes or get_recipe first to look
    up the correct translationId for the recipe to add.

    Args:
        translation_id: The `translationId` of the recipe to add, from a
            search_recipes/get_recipe result.
        language: Locale for the response message, e.g. "pl-PL", "en-US".
    """
    return await recipes.add_favorite_recipe(translation_id=translation_id, language=language)


@mcp.tool
async def remove_favorite_recipe(translation_id: int, language: str = MC_API_DEFAULT_LANGUAGE) -> dict:
    """Remove a recipe from the logged-in user's favorites (bookmarks).

    Requires a saved login session: run `uv run monsieur-cuisine-mcp-login`
    once beforehand to log in manually in a browser window. If no session is
    saved, this tool raises an error telling the user to do that.

    IMPORTANT: `translation_id` is the bookmark/recipe *translationId* field
    from a list_favorite_recipes result - NOT the numeric recipe `id` used by
    get_recipe/search_recipes. Always call list_favorite_recipes first to
    look up the correct translationId for the recipe to remove.

    Args:
        translation_id: The `translationId` of the favorite to remove, from
            a list_favorite_recipes result.
        language: Locale for the response message, e.g. "pl-PL", "en-US".
    """
    return await recipes.remove_favorite_recipe(translation_id=translation_id, language=language)


@mcp.tool
async def search_ingredients(
    query: str, count: int = 10, language: str = MC_API_DEFAULT_LANGUAGE
) -> dict:
    """Search the Monsieur Cuisine ingredient database, returning matching
    ingredients with their `systemIngredientId`.

    Use this to find the `system_ingredient_id` for an ingredient before
    passing it to create_recipe, when you want to guarantee the exact database
    ingredient rather than relying on create_recipe's automatic first-match
    resolution of a plain name.

    Requires a saved login session: run `uv run monsieur-cuisine-mcp-login`
    once beforehand. If no session is saved, this tool raises an error telling
    the user to do that.

    Args:
        query: Ingredient term to search for (e.g. "flour"). Language-specific.
        count: Maximum number of matches to return.
        language: Locale to search in, e.g. "pl-PL", "en-US".
    """
    return await recipes.search_ingredients(query=query, count=count, language=language)


@mcp.tool
async def list_recipe_creation_options(language: str = MC_API_DEFAULT_LANGUAGE) -> dict:
    """List the valid option identifiers for creating a recipe in a given
    language: complexity levels, categories, serving units, ingredient units
    and device types.

    Call this before create_recipe whenever you need to set `complexity_id` or
    `category_ids`, since their valid ids are language-specific and differ
    between locales.

    Requires a saved login session: run `uv run monsieur-cuisine-mcp-login`
    once beforehand. If no session is saved, this tool raises an error telling
    the user to do that.

    Args:
        language: Locale to fetch options for, e.g. "pl-PL", "en-US". Must
            match the `language` you intend to pass to create_recipe.
    """
    return await recipes.get_recipe_creation_options(language=language)


@mcp.tool
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
    steps: list[dict] | None = None,
    image_path: str | None = None,
    image_ai_generated: bool | None = None,
    language: str = MC_API_DEFAULT_LANGUAGE,
) -> dict:
    """Create a new recipe as a DRAFT under the logged-in user's account.

    The recipe is created for the Monsieur Cuisine Smart (MC3.0), the only
    supported device. Only `title` is required. The recipe is saved as a draft
    (status "draft") and is NOT published. Pass `image_path` to attach a dish
    photo (see below).
    Publishing is a separate step: once the draft is complete, call
    publish_recipe (public review, or private=True for a private publish). A
    published recipe cannot be reverted to a draft; to take it down, switch it to
    private or delete it with delete_recipe.

    Requires a saved login session: run `uv run monsieur-cuisine-mcp-login`
    once beforehand. If no session is saved, this tool raises an error telling
    the user to do that.

    The valid step `program` names and their params are listed in full under
    `steps` below - you do NOT need another call to enumerate them. Only
    `complexity_id` and `category_ids` are language-specific ids you cannot
    guess: call list_recipe_creation_options(language) first (same language) to
    get those, or omit them (a draft is valid without them).

    Args:
        title: Dish name (required).
        description: Short description of the dish.
        complexity_id: Difficulty level id from list_recipe_creation_options.
        category_ids: Category ids from list_recipe_creation_options.
        servings_amount: Number of servings the recipe yields.
        servings_unit: Serving unit, from list_recipe_creation_options'
            serving_units (e.g. "porcje").
        prep_time_minutes: Preparation time in minutes.
        ready_time_minutes: Total time until ready, in minutes.
        calories: Energy per serving (kcal). Rounded to a whole number.
        protein: Protein per serving (g). Rounded to a whole number.
        carbohydrate: Carbohydrate per serving (g). Rounded to a whole number.
        fat: Fat per serving (g). Rounded to a whole number.
        ingredients: List of ingredient dicts for the default general group,
            each with keys "name", "amount", "unit". Ingredients are free text -
            the "name" is used verbatim (like typing a custom ingredient in the
            app without picking a suggestion), no database match required. Add
            an optional "system_ingredient_id" (a positive id from
            search_ingredients) only to link a specific database ingredient.
            Add "optional": true to mark an ingredient as optional. The
            default group is auto-named "General Ingredients" (a group needs a
            name to publish).
        ingredient_groups: Optional named ingredient groups, each
            {"name": <group name>, "ingredients": [ingredient dicts]} (same
            ingredient shape as above). Use for recipes split into sections
            like "Dough" / "Sauce"; give each a non-empty name so the recipe can
            be published.
        steps: Ordered list of step dicts. Each step is
            {"text": <instruction>, ...optional program fields}; a plain string
            is shorthand for a text-only step. Add an optional "title" (short
            step heading): every step needs a non-empty title to be publishable,
            so when omitted a localized ordinal ("Krok 1"/"Step 1") is filled in
            automatically. A step with no "program" is a plain text instruction.
            To run an automatic device program, add "program" plus the params it
            uses. All times are in SECONDS.

            Full list of programs (Monsieur Cuisine smart) and their params:
              - "customized": temperature_celsius, speed, time_seconds,
                reverse (bool, true = counter-clockwise)
              - "kneading", "kneading_solid_dough", "kneading_soft_dough",
                "kneading_liquid_dough": time_seconds
              - "steaming", "rice_cooking", "food_processor", "puree",
                "smoothie", "turbo": time_seconds
              - "roasting", "sous_vide", "slow_cooking", "fermentation":
                temperature_celsius, time_seconds
              - "ramp" (speed rising step by step, e.g. for pureeing):
                ramp_steps = [{"speed": 4, "time_seconds": 10},
                {"speed": 5, "time_seconds": 10}, ...] (at least two)
              - "scale" (weighing): weight_grams
              - "cooking_eggs": egg_size ("small"|"medium"|"large"),
                egg_texture ("soft"|"waxy_soft"|"hard")
              - "precleaning": cleaning_duration ("short"|"long")

            Examples:
              {"text": "Rozdrobnij", "program": "customized", "speed": 7,
               "temperature_celsius": 90, "time_seconds": 120}
              {"text": "Zwaz skladniki", "program": "scale", "weight_grams": 250}
              {"text": "Wymieszaj recznie"}  # plain text step
        image_path: Optional path to a local dish photo (JPEG, PNG or WebP) to
            attach as the recipe's cover image. It is uploaded to the media
            service and set as both the thumbnail and the details image. This is
            the photo a draft needs before it can be published in the web app.
        image_ai_generated: True if the photo is AI-generated; the site then
            marks it with its own "AI" watermark (like the web app checkbox).
        language: Recipe language/locale, e.g. "pl-PL", "en-US".
    """
    return await recipes.create_recipe(
        title=title, description=description,
        complexity_id=complexity_id, category_ids=category_ids,
        servings_amount=servings_amount, servings_unit=servings_unit,
        prep_time_minutes=prep_time_minutes, ready_time_minutes=ready_time_minutes,
        calories=calories, protein=protein, carbohydrate=carbohydrate, fat=fat,
        ingredients=ingredients, ingredient_groups=ingredient_groups,
        steps=steps, image_path=image_path,
        image_ai_generated=image_ai_generated, language=language,
    )


@mcp.tool
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
    steps: list[dict] | None = None,
    image_path: str | None = None,
    image_ai_generated: bool | None = None,
    language: str = MC_API_DEFAULT_LANGUAGE,
) -> dict:
    """Edit one of the logged-in user's own recipes (the "Edit" button in the
    web app). Only the fields you pass are changed; everything else is kept.

    This is the counterpart to create_recipe: it fetches the recipe and re-saves
    it as a whole (the update is a full replace), so any argument left unset
    keeps its current value. The recipe keeps its current status; a published
    recipe is re-saved in place and must stay complete (the server rejects an
    edit that would make it incomplete). Requires a saved login session
    (`uv run monsieur-cuisine-mcp-login`).

    Field meanings match create_recipe. IMPORTANT - collection fields REPLACE
    the current value when supplied: passing `steps` replaces ALL steps, and
    passing `ingredients` and/or `ingredient_groups` replaces ALL ingredient
    groups (so re-send the full list, not just the changed entries). Nutrients
    are merged instead: only the calories/protein/carbohydrate/fat you pass are
    overwritten. `image_path` replaces the cover photo. See create_recipe for the
    step/ingredient/program shapes.

    Args:
        recipe_id: Numeric id of the user's own recipe to edit (e.g. from
            list_my_recipes or the create_recipe result).
        title: New dish name.
        description: New description.
        complexity_id: New difficulty id from list_recipe_creation_options.
        category_ids: New category ids (replaces the current categories).
        servings_amount: New number of servings.
        servings_unit: New serving unit.
        prep_time_minutes: New preparation time in minutes.
        ready_time_minutes: New total time until ready, in minutes.
        calories: New energy per serving (kcal); rounded to a whole number.
        protein: New protein per serving (g); rounded to a whole number.
        carbohydrate: New carbohydrate per serving (g); rounded to a whole number.
        fat: New fat per serving (g); rounded to a whole number.
        ingredients: New default-group ingredients (replaces all groups; same
            dict shape as create_recipe).
        ingredient_groups: New named ingredient groups (replaces all groups).
        steps: New ordered steps (replaces all steps; same shape as
            create_recipe, including per-step "title"/"program"/params).
        image_path: Local JPEG/PNG/WebP to set as the new cover photo.
        image_ai_generated: Mark the new or existing cover photo as AI-generated
            (True) or not (False); omit to keep the current marking.
        language: Recipe language/locale, e.g. "pl-PL", "en-US".
    """
    return await recipes.edit_recipe(
        recipe_id=recipe_id, title=title, description=description,
        complexity_id=complexity_id, category_ids=category_ids,
        servings_amount=servings_amount, servings_unit=servings_unit,
        prep_time_minutes=prep_time_minutes, ready_time_minutes=ready_time_minutes,
        calories=calories, protein=protein, carbohydrate=carbohydrate, fat=fat,
        ingredients=ingredients, ingredient_groups=ingredient_groups,
        steps=steps, image_path=image_path,
        image_ai_generated=image_ai_generated, language=language,
    )


@mcp.tool
async def delete_recipe(
    recipe_id: int,
    language: str = MC_API_DEFAULT_LANGUAGE,
) -> dict:
    """Delete one of the logged-in user's own recipes (moves it to trash).

    This is the "Delete" action from the web app's "My recipes" list ("..."
    menu): it sets the recipe's status to "trash", so it disappears from the
    recipe list. The web app has no trash view or restore button, so on the
    website this is effectively permanent. Technically it's a soft delete - the
    data is kept server-side and can be brought back with restore_recipe - but
    there is no hard-delete endpoint, so a trashed recipe stays in trash forever.
    Works for the user's own recipes in any status; it cannot delete recipes that
    are not the user's own.

    Because this changes the user's account, only call it for a recipe the user
    has clearly asked to delete, identified by its `recipe_id` (from
    list_my_recipes / the create_recipe result). The result reports the deleted
    recipe's title and previous status.

    Requires a saved login session: run `uv run monsieur-cuisine-mcp-login`
    once beforehand.

    Args:
        recipe_id: Numeric id of the user's own recipe to delete.
        language: Locale, e.g. "pl-PL", "en-US".
    """
    return await recipes.delete_recipe(recipe_id=recipe_id, language=language)


@mcp.tool
async def restore_recipe(
    recipe_id: int,
    language: str = MC_API_DEFAULT_LANGUAGE,
) -> dict:
    """Restore a trashed recipe (undo delete_recipe), bringing it back as a draft.

    delete_recipe only moves a recipe to "trash" and keeps its data, so this can
    undo it. The web app can't (it has no trash view), but the API allows it. The
    recipe comes back as a DRAFT no matter what it was before deletion, so if it
    was published, re-publish it with publish_recipe afterwards. Find trashed
    recipes to restore with list_my_recipes(status="trash").

    Meaningful only on a trashed recipe: on an already-published recipe it fails
    (a live recipe can't be moved back to draft); on a draft it's a no-op.

    Requires a saved login session: run `uv run monsieur-cuisine-mcp-login`
    once beforehand.

    Args:
        recipe_id: Numeric id of the trashed recipe to restore.
        language: Locale, e.g. "pl-PL", "en-US".
    """
    return await recipes.restore_recipe(recipe_id=recipe_id, language=language)


@mcp.tool
async def publish_recipe(
    recipe_id: int,
    private: bool = False,
    language: str = MC_API_DEFAULT_LANGUAGE,
) -> dict:
    """Publish one of the logged-in user's own DRAFT recipes.

    The workflow is: create_recipe makes a draft, then this tool publishes it.
    Two modes, mirroring the web app's publish dialog:
      - private=False (default): PUBLIC publish - submits the recipe for
        editorial review (status "pending"); it goes public only after the
        Monsieur Cuisine team approves it. The web app requires confirming the
        recipe and photo are your own original content; publishing here asserts
        the same, so only do it when the user intends to submit their original
        recipe for public review.
      - private=True: PRIVATE publish (status "private-publish") - published only
        to the user's own account, no review.

    The recipe must be complete or the server refuses to publish (a clear "not
    complete" error lists what is missing): cover photo, complexity, categories,
    a serving size, at least one ingredient, and a non-empty title on every step
    and ingredient group.

    Also switches an already-published recipe between public and private. NOTE:
    a published recipe cannot be reverted to a draft (the platform forbids it) -
    to take one down, switch it to private (private=True) or use delete_recipe.

    Requires a saved login session: run `uv run monsieur-cuisine-mcp-login`
    once beforehand.

    Args:
        recipe_id: Numeric id of the user's own recipe to publish (e.g. from the
            create_recipe result or list_my_recipes).
        private: True to publish privately; False (default) to submit for public
            review.
        language: Locale, e.g. "pl-PL", "en-US".
    """
    return await recipes.publish_recipe(recipe_id=recipe_id, private=private, language=language)


def main() -> None:
    parser = argparse.ArgumentParser(description="Monsieur Cuisine MCP server")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose event logging (DEBUG level)",
    )
    args = parser.parse_args()
    setup_logging(logging.DEBUG if args.debug else None)
    logger.info("Starting Monsieur Cuisine MCP server")
    mcp.run()


if __name__ == "__main__":
    main()
