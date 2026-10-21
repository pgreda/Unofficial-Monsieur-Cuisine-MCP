"""Reverse-map a fetched recipe (GET shape) into the update (PUT) shape."""


def _serving_to_put_payload(serving: dict) -> dict:
    """Map a serving size from the GET recipe shape back to the PUT shape,
    dropping the read-only/computed fields (ids, icons, categories, durations)
    and keeping only what the update endpoint accepts."""
    steps = []
    for st in serving.get("steps") or []:
        api_step = {"title": st.get("title", ""), "description": st.get("description", "")}
        # The stored `mode` is already in the write shape (type/modeSetting/
        # deviceSettings), so preserve it verbatim; omit it for text-only steps.
        if st.get("mode"):
            api_step["mode"] = st["mode"]
        steps.append(api_step)

    groups = []
    for group in serving.get("ingredientGroups") or []:
        ingredients = []
        for ing in group.get("ingredients") or []:
            item = {
                "name": ing.get("name", ""),
                "amount": str(ing.get("amount", "")),
                "unit": ing.get("unit", ""),
                "order": ing.get("order", 0),
            }
            system_id = ing.get("systemIngredientId")
            if system_id and system_id >= 1:
                item["systemIngredientId"] = system_id
            if ing.get("isOptional"):
                item["isOptional"] = True
            ingredients.append(item)
        groups.append({
            "name": group.get("name", ""),
            "isDefault": group.get("isDefault", False),
            "order": group.get("order", 0),
            "ingredients": ingredients,
        })

    return {
        "isDefault": serving.get("isDefault", True),
        "amount": serving.get("amount", 0),
        "unit": serving.get("unit", ""),
        "instruction": serving.get("instruction", ""),
        "preparationTime": serving.get("preparationTime", 1),
        "readyInTime": serving.get("readyInTime", 1),
        "steps": steps,
        "ingredientGroups": groups,
    }


def _recipe_to_put_payload(recipe: dict) -> dict:
    """Rebuild the full update (PUT) payload from a fetched recipe (GET shape).

    The recipe PUT is a full replace, so editing one field still means sending
    the whole recipe back. This reverse-maps the GET representation (nested
    objects, extra read-only fields) into the flat id-based shape the update
    endpoint expects, preserving everything so nothing is lost on save.
    """
    payload: dict = {
        "status": recipe.get("status", "draft"),
        "source": recipe.get("source", "member"),
        "languageLocale": recipe.get("languageLocale"),
        "deviceTypeIds": [d["id"] for d in (recipe.get("deviceTypes") or []) if d.get("id")],
        "title": recipe.get("title", ""),
        "allowSocialSharing": recipe.get("allowSocialSharing", False),
        "nutrients": [
            {"name": n.get("name"), "amount": n.get("amount"), "unit": n.get("unit", "")}
            for n in (recipe.get("nutrients") or [])
        ],
    }
    if recipe.get("description") is not None:
        payload["description"] = recipe["description"]
    complexity = recipe.get("complexity")
    if isinstance(complexity, dict) and complexity.get("id"):
        payload["complexityId"] = complexity["id"]
    category_ids = [c["id"] for c in (recipe.get("categories") or []) if c.get("id")]
    if category_ids:
        payload["categoryIds"] = category_ids
    for key in ("thumbnail", "detailsImage"):
        media = recipe.get(key)
        if isinstance(media, dict):
            landscape = media.get("landscapeMedia") or {}
            portrait = media.get("portraitMedia") or {}
            if landscape.get("id") or portrait.get("id"):
                # Carry the AI-image flags over, or a re-save would silently
                # clear the "AI-generated" marking set in the web app.
                payload[key] = {
                    "portraitMediaId": portrait.get("id"),
                    "landscapeMediaId": landscape.get("id"),
                    "portraitIsAiGenerated": bool(portrait.get("isAiGenerated")),
                    "landscapeIsAiGenerated": bool(landscape.get("isAiGenerated")),
                }
    servings = [_serving_to_put_payload(s) for s in (recipe.get("servingSizes") or [])]
    if servings:
        payload["servingSizes"] = servings
    return payload


def _empty_serving() -> dict:
    """A minimal default serving, used when editing serving details on a recipe
    that has no serving size yet."""
    return {
        "isDefault": True, "amount": 0, "unit": "", "instruction": "",
        "preparationTime": 1, "readyInTime": 1, "steps": [], "ingredientGroups": [],
    }
