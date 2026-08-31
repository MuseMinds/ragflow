"""Lightweight fail-closed image-description adapter for MuseMind generation v2."""

import io

from common.string_utils import clean_markdown_block


def vision_llm_chunk(binary, vision_model, prompt=None, callback=None):
    """Describe one figure, preserving legacy best effort outside exact v2."""
    callback = callback or (lambda prog, msg: None)
    img = binary

    try:
        if hasattr(img, "size"):
            min_side = 11
            if img.size[0] < min_side or img.size[1] < min_side:
                callback(0.0, f"Skip tiny image for VLM: {img.size[0]}x{img.size[1]}")
                return ""
        with io.BytesIO() as img_binary:
            try:
                img.save(img_binary, format="JPEG")
            except Exception:  # noqa: BLE001 - JPEG failure intentionally falls back to PNG
                img_binary.seek(0)
                img_binary.truncate()
                img.save(img_binary, format="PNG")

            img_binary.seek(0)
            answer = clean_markdown_block(vision_model.describe_with_prompt(img_binary.read(), prompt))
            return "\n" + answer
    except Exception:  # noqa: BLE001 - provider and image failures share the fail-closed boundary
        callback(-1, "CV LLM request failed.")
        if getattr(getattr(vision_model, "mdl", None), "content_free_observability", False) is True:
            raise RuntimeError("Pinned image-description request failed") from None
        return ""
