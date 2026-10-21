"""Recipe cover-photo upload to the media service (presign -> PUT -> attach)."""
import asyncio
import logging
import mimetypes
from pathlib import Path

import httpx

from ..config import MC_API_WRITE_TIMEOUT_SECONDS
from .client import MCRequestError, _call_proxy_api

logger = logging.getLogger(__name__)


# Image formats the media service accepts for a recipe photo. The web app
# re-encodes to one of these before uploading; we map by file extension.
_IMAGE_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _guess_image_mime(path: Path) -> str:
    """Return the image MIME type for a local file, restricted to the formats
    the recipe media service accepts (jpeg/png/webp). Raises ValueError for
    anything else so we fail before uploading an unsupported file."""
    mime = _IMAGE_MIME_BY_SUFFIX.get(path.suffix.lower())
    if mime is None:
        # Fall back to the system guess, but still require an accepted image type.
        guessed, _ = mimetypes.guess_type(str(path))
        if guessed in _IMAGE_MIME_BY_SUFFIX.values():
            mime = guessed
    if mime is None:
        raise ValueError(
            f"Unsupported image type for {path.name!r}. Use a JPEG, PNG or WebP file."
        )
    return mime


async def _upload_recipe_image(
    image_path: str, language: str, ai_generated: bool = False
) -> dict:
    """Upload a local image to the recipe media service and return a reference
    to it as {"media_id": int, "landscape_ref": {...}}.

    `ai_generated` marks the image as AI-generated, like the checkbox in the web
    app's cover-photo dialog; the site then shows its own "AI" watermark.

    Mirrors the web app's cover-photo flow, which is a three-step handshake:
      1. Ask the media service for a signed upload URL (via the proxy-api),
         passing the file name and MIME type. It returns {url, extraHeaders,
         mediaId}: a short-lived Google Cloud Storage signed URL plus the
         mediaId to reference the image by afterwards.
      2. PUT the raw image bytes straight to that signed URL (NOT through the
         proxy-api), sending any extraHeaders plus the Content-Type the URL was
         signed for.
      3. The caller then references `mediaId` from the recipe's `thumbnail` /
         `detailsImage` fields when it saves the recipe (see create_recipe).
    """
    path = Path(image_path).expanduser()
    if not path.is_file():
        raise ValueError(f"Image file not found: {image_path}")
    mime = _guess_image_mime(path)
    data = path.read_bytes()

    # Step 1: get a signed upload URL + the mediaId to reference the image by.
    presign_response = await _call_proxy_api(
        "api/v1/media/image/upload-url",
        method="POST",
        language=language,
        payload={"fileName": path.name, "mimeType": mime},
        timeout=MC_API_WRITE_TIMEOUT_SECONDS,
    )
    presign = presign_response.get("data") or {}
    upload_url = presign.get("url")
    media_id = presign.get("mediaId")
    extra_headers = presign.get("extraHeaders") or {}
    if not upload_url or media_id is None:
        raise MCRequestError(
            "The media service did not return a signed upload URL. This usually "
            "means the login session expired or bot protection is blocking writes; "
            "re-login or wait a bit and retry."
        )

    # Step 2: upload the raw bytes to the signed storage URL. This goes directly
    # to Google Cloud Storage (no session cookies), and the URL is signed for the
    # exact Content-Type we announced in step 1, so it must match.
    headers = {**extra_headers, "Content-Type": mime}
    async with httpx.AsyncClient(timeout=MC_API_WRITE_TIMEOUT_SECONDS) as client:
        upload = await client.put(upload_url, content=data, headers=headers)
    if upload.status_code >= 400:
        raise MCRequestError(
            f"Uploading the recipe image failed ({upload.status_code}): "
            f"{upload.text[:200]}"
        )
    logger.info("Uploaded recipe image %s as media %s", path.name, media_id)

    # The media service processes the uploaded bytes asynchronously. Referencing
    # the media from a recipe before it finishes yields "Media not found", so
    # wait until it reports ready before returning.
    await _wait_for_media_ready(media_id, language)

    # A media reference on the recipe carries the landscape media id; the
    # portrait variant is optional and left null (the web app does the same
    # unless a separate portrait crop was made).
    return {
        "media_id": media_id,
        "landscape_ref": {
            "portraitMediaId": None,
            "landscapeMediaId": media_id,
            "portraitIsAiGenerated": ai_generated,
            "landscapeIsAiGenerated": ai_generated,
        },
    }


# The media service reports processing state as `uploadStatus`; "3" means the
# image is fully processed and can be referenced from a recipe.
_MEDIA_STATUS_READY = "3"


async def _wait_for_media_ready(
    media_id: int, language: str, attempts: int = 20, delay_seconds: float = 1.5
) -> None:
    """Poll the media service until `media_id` is processed (uploadStatus "3").

    Uploads are usually ready almost immediately, but processing time varies;
    referencing a not-yet-ready media from a recipe fails with "Media not found".
    Raises MCRequestError if the media never becomes ready within the budget.
    """
    for _ in range(attempts):
        response = await _call_proxy_api(
            f"api/v1/media?ids[]={media_id}", method="GET", language=language
        )
        media_list = (response.get("data") or {}).get("media") or []
        status = media_list[0].get("uploadStatus") if media_list else None
        if status == _MEDIA_STATUS_READY:
            return
        await asyncio.sleep(delay_seconds)
    raise MCRequestError(
        f"Uploaded image (media {media_id}) was not ready in time. Retry shortly - "
        "the media service is still processing it."
    )
