"""Decode and re-encode raster proof; original names and metadata are discarded."""
import io
import os
import uuid
from flask import current_app
from PIL import Image, UnidentifiedImageError
from .services import BusinessRuleError

MAX_BYTES = 5 * 1024 * 1024

def save_image(upload):
    raw = upload.stream.read(MAX_BYTES + 1)
    if not raw or len(raw) > MAX_BYTES:
        raise BusinessRuleError("Image must be nonempty and no larger than 5 MB.")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ("JPEG", "PNG", "WEBP") or image.width * image.height > 20000000:
                raise BusinessRuleError("Use a JPEG, PNG or WebP image up to 20 megapixels.")
            image.load()
            clean = image.convert("RGB")
            out = io.BytesIO()
            clean.save(out, format="JPEG", quality=88)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise BusinessRuleError("The uploaded file is not a valid supported image.")
    name = uuid.uuid4().hex + ".jpg"
    folder = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "wb") as handle:
        handle.write(out.getvalue())
    return "/media/" + name
