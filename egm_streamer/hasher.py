from PIL import Image
import imagehash
from typing import Literal

def compute_hash(img: Image.Image, algo: str = "dhash", hash_size: int = 8) -> imagehash.ImageHash:
    if algo == "dhash":
        return imagehash.dhash(img, hash_size=hash_size)
    elif algo == "phash":
        return imagehash.phash(img, hash_size=hash_size)
    elif algo == "ahash":
        return imagehash.average_hash(img, hash_size=hash_size)
    else:
        raise ValueError(f"Unknown hash algo: {algo}")

def hex_to_hash(hex_str: str) -> imagehash.ImageHash:
    return imagehash.hex_to_hash(hex_str)
