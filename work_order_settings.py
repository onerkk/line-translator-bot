"""Persistent admin settings for work-order interpretation."""

from __future__ import annotations


DEFAULT_JUDGMENT_MODE = "special"
VALID_JUDGMENT_MODES = frozenset({"special", "normal"})
_CONFIG_KEY = "work_order"
_MODE_KEY = "judgment_mode"


def normalize_judgment_mode(value):
    return (value if isinstance(value, str) and value in VALID_JUDGMENT_MODES
            else DEFAULT_JUDGMENT_MODE)


def get_judgment_mode():
    """Return the saved mode, falling back to today's special rules."""
    try:
        import phase_config_store

        config = phase_config_store.load_config(_CONFIG_KEY)
        return normalize_judgment_mode(config.get(_MODE_KEY))
    except Exception:
        return DEFAULT_JUDGMENT_MODE


def save_judgment_mode(value):
    """Save a validated mode through the project's cross-deploy settings store."""
    if not isinstance(value, str) or value not in VALID_JUDGMENT_MODES:
        raise ValueError("invalid_work_order_judgment_mode")
    import phase_config_store

    config = phase_config_store.load_config(_CONFIG_KEY)
    config[_MODE_KEY] = value
    return bool(phase_config_store.save_config(_CONFIG_KEY, config))
