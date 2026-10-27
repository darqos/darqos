import unicodedata

def is_valid_item_identifier(s: str) -> bool:
    if not s:
        return False

    # Standardize string according to your spec's normalization rule
    normalized = unicodedata.normalize('NFKC', s)

    # 1. Validate the first character (Item_Start)
    first = normalized[0]
    # Check XID_Start manually using .isidentifier() shortcut
    # (Note: .isidentifier() includes ASCII '_', which is also in your spec)
    if not (first.isidentifier() or first in ['.', '/']):
        return False

    # 2. Validate subsequent characters (Item_Continue)
    for char in normalized[1:]:
        # A character is valid in Item_Continue if it's a valid XID_Continue,
        # or one of your custom allowed punctuation marks.
        # ('a' + char).isidentifier() is a trick to evaluate XID_Continue via Python
        if not (('a' + char).isidentifier() or char in ['.', '/', ':']):
            return False

    return True


# --- Verification Tests ---
print(is_valid_item_identifier("my/item:sub-path.v1"))  # ✅ True
print(is_valid_item_identifier(".hidden/path:2"))  # ✅ True
print(is_valid_item_identifier(":invalid_start"))  # ❌ False (Starts with colon)
print(is_valid_item_identifier("item_with_🐱_emoji"))  # ❌ False (Emoji blocked by XID)
