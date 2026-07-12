"""Versioned automation template definitions for Template Studio."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sqlite3
from typing import Any, Callable

from . import db


TEMPLATE_STATUSES = {"draft", "active", "archived"}
TEMPLATE_RENDERERS = {"ffmpeg_fast", "remotion"}
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{1,79}$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class StudioTemplateAdapter:
    key: str
    composition_id: str
    component: str
    supported_renderers: tuple[str, ...]
    supports_optional_reaction: bool
    reaction_required_fn: Callable[[dict[str, Any], dict[str, Any]], bool]

    def validate_parameters(
        self,
        definition: dict[str, Any],
        parameter_values: dict[str, Any] | None,
    ) -> None:
        validate_parameter_values(definition, parameter_values)

    def materialize_recipe(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        from .studio import parameterized_recipe_from_template

        return parameterized_recipe_from_template(*args, **kwargs)


def _reaction_layout_required(
    definition: dict[str, Any],
    parameter_values: dict[str, Any],
) -> bool:
    reaction_slot = (definition.get("slots") or {}).get("reaction")
    if reaction_slot is None or not bool(reaction_slot.get("required")):
        return False
    return str(parameter_values.get("reaction_position") or "top") != "none"


def _reaction_never_required(
    definition: dict[str, Any],
    parameter_values: dict[str, Any],
) -> bool:
    return False


STUDIO_TEMPLATE_ADAPTERS: dict[str, StudioTemplateAdapter] = {
    "reaction_layout": StudioTemplateAdapter(
        key="reaction_layout",
        composition_id="ReactionLayoutTemplate",
        component="ReactionLayoutTemplate",
        supported_renderers=("ffmpeg_fast", "remotion"),
        supports_optional_reaction=True,
        reaction_required_fn=_reaction_layout_required,
    ),
    "main_only": StudioTemplateAdapter(
        key="main_only",
        composition_id="MainOnlyTemplate",
        component="MainOnlyTemplate",
        supported_renderers=("ffmpeg_fast", "remotion"),
        supports_optional_reaction=True,
        reaction_required_fn=_reaction_never_required,
    ),
}

# Backwards-compatible alias for older imports/tests.  The registry is no longer
# Remotion-only; renderer availability is validated against adapter capability.
RemotionTemplateAdapter = StudioTemplateAdapter
REMOTION_TEMPLATE_ADAPTERS = STUDIO_TEMPLATE_ADAPTERS


def _common_parameters(
    *,
    reaction_position: str,
    reaction_height: int,
    pip_position: str = "top_right",
) -> dict[str, Any]:
    return {
        "reaction_position": {
            "group": "layout",
            "type": "select",
            "values": ["top", "bottom", "pip", "none"],
            "default": reaction_position,
        },
        "reaction_height": {
            "group": "layout",
            "type": "number",
            "min": 240,
            "max": 960,
            "default": reaction_height,
        },
        "pip_position": {
            "group": "layout",
            "type": "select",
            "values": ["top_left", "top_right", "bottom_left", "bottom_right"],
            "default": pip_position,
        },
        "main_fit": {
            "group": "layout",
            "type": "select",
            "values": ["cover", "contain"],
            "default": "cover",
        },
        "reaction_fit": {
            "group": "layout",
            "type": "select",
            "values": ["cover", "contain"],
            "default": "cover",
        },
        "background_color": {
            "group": "layout",
            "type": "color",
            "default": "#000000",
        },
        "main_volume": {
            "group": "audio",
            "type": "number",
            "min": 0,
            "max": 1,
            "default": 1,
        },
        "reaction_volume": {
            "group": "audio",
            "type": "number",
            "min": 0,
            "max": 1,
            "default": 0,
        },
        "mute_reaction": {
            "group": "audio",
            "type": "boolean",
            "default": True,
        },
        "top_text": {
            "group": "text",
            "type": "text",
            "max_length": 200,
            "default": "",
        },
        "bottom_text": {
            "group": "text",
            "type": "text",
            "max_length": 200,
            "default": "",
        },
    }


def _reaction_layout_definition(
    *,
    key: str,
    name: str,
    reaction_position: str,
    reaction_height: int,
    layout_variant: str,
    pip_position: str = "top_right",
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "key": key,
        "name": name,
        "adapter": "reaction_layout",
        "supported_renderers": ["ffmpeg_fast", "remotion"],
        "default_renderer": "ffmpeg_fast",
        "canvas": {"width": 1080, "height": 1920, "fps": 30},
        "slots": {
            "main": {
                "type": "video",
                "required": True,
                "allowed_sections": ["sources", "cuts", "prepared"],
                "duration_policy": "defines_output_duration",
            },
            "reaction": {
                "type": "video",
                "required": True,
                "source": "reaction_asset_or_pool",
                "playback": "loop",
            },
        },
        "parameters": _common_parameters(
            reaction_position=reaction_position,
            reaction_height=reaction_height,
            pip_position=pip_position,
        ),
        "rules": {
            "output_duration": "main.duration",
            "reaction_playback": "loop",
            "output_aspect": "9:16",
            "output_folder": "edits",
            "renderer": "remotion",
            "renderer_adapter": "reaction_layout",
            "composition_id": "ReactionLayoutTemplate",
            "layout_variant": layout_variant,
        },
    }


def _main_only_definition() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "key": "main_only",
        "name": "Main Only",
        "adapter": "main_only",
        "supported_renderers": ["ffmpeg_fast", "remotion"],
        "default_renderer": "ffmpeg_fast",
        "canvas": {"width": 1080, "height": 1920, "fps": 30},
        "slots": {
            "main": {
                "type": "video",
                "required": True,
                "allowed_sections": ["sources", "cuts", "prepared"],
                "duration_policy": "defines_output_duration",
            },
        },
        "parameters": {
            "main_fit": {
                "group": "layout",
                "type": "select",
                "values": ["cover", "contain"],
                "default": "cover",
            },
            "background_color": {
                "group": "layout",
                "type": "color",
                "default": "#000000",
            },
            "main_volume": {
                "group": "audio",
                "type": "number",
                "min": 0,
                "max": 1,
                "default": 1,
            },
            "top_text": {
                "group": "text",
                "type": "text",
                "max_length": 200,
                "default": "",
            },
            "bottom_text": {
                "group": "text",
                "type": "text",
                "max_length": 200,
                "default": "",
            },
        },
        "rules": {
            "output_duration": "main.duration",
            "output_aspect": "9:16",
            "output_folder": "edits",
            "renderer_adapter": "main_only",
            "composition_id": "MainOnlyTemplate",
            "layout_variant": "main_only",
        },
    }


def default_reaction_top_25_definition() -> dict[str, Any]:
    return _reaction_layout_definition(
        key="reaction_top_25",
        name="Reaction Top 25%",
        reaction_position="top",
        reaction_height=480,
        layout_variant="top_reaction",
    )


def default_studio_template_definitions() -> list[dict[str, Any]]:
    return [
        default_reaction_top_25_definition(),
        _reaction_layout_definition(
            key="reaction_top_33",
            name="Reaction Top 33%",
            reaction_position="top",
            reaction_height=634,
            layout_variant="top_reaction",
        ),
        _reaction_layout_definition(
            key="reaction_top_50",
            name="Reaction Top 50%",
            reaction_position="top",
            reaction_height=960,
            layout_variant="top_reaction",
        ),
        _reaction_layout_definition(
            key="reaction_bottom_25",
            name="Reaction Bottom 25%",
            reaction_position="bottom",
            reaction_height=480,
            layout_variant="bottom_reaction",
        ),
        _reaction_layout_definition(
            key="reaction_pip_corner",
            name="Reaction Picture-in-Picture Corner",
            reaction_position="pip",
            reaction_height=420,
            layout_variant="picture_in_picture",
            pip_position="top_right",
        ),
        _main_only_definition(),
    ]


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} должен быть JSON object.")
    return value


def normalize_template_definition(value: Any) -> dict[str, Any]:
    definition = _object(value, "template definition")
    raw_schema_version = definition.get("schema_version", definition.get("version", 1))
    try:
        source_schema_version = int(raw_schema_version or 1)
    except (TypeError, ValueError):
        source_schema_version = 1
    key = str(definition.get("key") or "").strip().lower()
    if not _KEY_RE.fullmatch(key):
        raise ValueError(
            "Template key должен содержать lowercase letters, digits и underscore."
        )
    name = str(definition.get("name") or "").strip()
    if not name:
        raise ValueError("Template name обязателен.")

    rules = dict(_object(definition.get("rules") or {}, "template.rules"))
    adapter_source = definition.get("adapter") or rules.get("renderer_adapter")
    if adapter_source is None and str(definition.get("engine") or "").strip().lower() == "ffmpeg":
        raise ValueError("Template не имеет renderer adapter.")
    adapter_key = str(adapter_source or "reaction_layout").strip().lower()
    adapter = STUDIO_TEMPLATE_ADAPTERS.get(adapter_key)
    if adapter is None:
        raise ValueError(f"Template adapter не поддерживается: {adapter_key}")

    raw_renderers = definition.get("supported_renderers")
    if raw_renderers is None:
        raw_renderers = list(adapter.supported_renderers)
    if not isinstance(raw_renderers, list):
        raise ValueError("supported_renderers должен быть массивом.")
    requested_renderers = {
        str(item or "").strip().lower()
        for item in raw_renderers
        if str(item or "").strip()
    }
    unknown_renderers = requested_renderers - TEMPLATE_RENDERERS
    if unknown_renderers:
        raise ValueError(
            "Unsupported renderer: " + ", ".join(sorted(unknown_renderers))
        )
    allowed_renderers = sorted(
        requested_renderers & set(adapter.supported_renderers),
        key=lambda item: list(adapter.supported_renderers).index(item),
    )
    if not allowed_renderers:
        raise ValueError(
            f"Template не имеет renderer, поддерживаемого adapter {adapter.key}."
        )
    default_source = definition.get("default_renderer")
    if default_source is None and source_schema_version >= 2:
        default_source = rules.get("renderer")
    default_renderer = str(
        default_source
        or ("ffmpeg_fast" if "ffmpeg_fast" in allowed_renderers else allowed_renderers[0])
    ).strip().lower()
    if default_renderer == "ffmpeg":
        default_renderer = "ffmpeg_fast"
    if default_renderer == "remotion" and "remotion" not in allowed_renderers:
        default_renderer = allowed_renderers[0]
    if default_renderer not in allowed_renderers:
        raise ValueError("default_renderer должен входить в supported_renderers adapter-а.")

    canvas = _object(definition.get("canvas"), "template.canvas")
    width = int(canvas.get("width", 1080))
    height = int(canvas.get("height", 1920))
    fps = int(canvas.get("fps", 30))
    if width <= 0 or height <= 0 or fps <= 0:
        raise ValueError("Canvas width, height и fps должны быть положительными.")

    slots = _object(definition.get("slots"), "template.slots")
    if "main" not in slots:
        raise ValueError("Template должен содержать slot main.")
    normalized_slots: dict[str, Any] = {}
    for slot_key, raw_slot in slots.items():
        slot = _object(raw_slot, f"template.slots.{slot_key}")
        slot_type = str(slot.get("type") or "").strip()
        if slot_type != "video":
            raise ValueError("Этап поддерживает только video slots.")
        normalized_slots[str(slot_key)] = {
            key: slot[key]
            for key in slot
            if key in {
                "type", "required", "allowed_sections", "duration_policy",
                "source", "playback",
            }
        }
        normalized_slots[str(slot_key)]["type"] = "video"
        normalized_slots[str(slot_key)]["required"] = bool(
            slot.get("required", False)
        )

    parameters = _object(
        definition.get("parameters"),
        "template.parameters",
    )
    normalized_parameters: dict[str, Any] = {}
    for parameter_key, raw_parameter in parameters.items():
        parameter = _object(
            raw_parameter,
            f"template.parameters.{parameter_key}",
        )
        parameter_type = str(parameter.get("type") or "").strip()
        if parameter_type not in {
            "number", "select", "boolean", "text", "color",
        }:
            raise ValueError(f"Unsupported parameter type: {parameter_type}")
        normalized_parameters[str(parameter_key)] = dict(parameter)

    rules.setdefault("renderer_adapter", adapter.key)
    rules.setdefault("composition_id", adapter.composition_id)
    rules.setdefault("renderer", default_renderer)
    return {
        "schema_version": 2,
        "key": key,
        "name": name,
        "adapter": adapter.key,
        "supported_renderers": allowed_renderers,
        "default_renderer": default_renderer,
        "canvas": {"width": width, "height": height, "fps": fps},
        "slots": normalized_slots,
        "parameters": normalized_parameters,
        "rules": dict(rules),
    }


def adapter_for_definition(
    definition: dict[str, Any],
) -> StudioTemplateAdapter | None:
    normalized = normalize_template_definition(definition)
    adapter_key = str(normalized.get("adapter") or "").strip()
    return STUDIO_TEMPLATE_ADAPTERS.get(adapter_key)


def remotion_adapter_for_definition(
    definition: dict[str, Any],
) -> StudioTemplateAdapter | None:
    adapter = adapter_for_definition(definition)
    normalized = normalize_template_definition(definition)
    if adapter is None or "remotion" not in normalized["supported_renderers"]:
        return None
    return adapter


def require_remotion_adapter(
    definition: dict[str, Any],
) -> StudioTemplateAdapter:
    adapter = remotion_adapter_for_definition(definition)
    if adapter is None:
        raise ValueError("Этот template пока не имеет Remotion renderer adapter.")
    return adapter


def require_template_adapter(definition: dict[str, Any]) -> StudioTemplateAdapter:
    adapter = adapter_for_definition(definition)
    if adapter is None:
        normalized = normalize_template_definition(definition)
        raise ValueError(f"Template adapter не поддерживается: {normalized.get('adapter')}")
    return adapter


def composition_id_for_definition(definition: dict[str, Any]) -> str:
    normalized = normalize_template_definition(definition)
    adapter = require_template_adapter(normalized)
    explicit = str(normalized.get("rules", {}).get("composition_id") or "").strip()
    return explicit or adapter.composition_id


def effective_parameter_values(
    definition: dict[str, Any],
    parameter_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_template_definition(definition)
    overrides = parameter_values or {}
    validate_parameter_values(normalized, overrides)
    values: dict[str, Any] = {}
    for key, parameter in normalized["parameters"].items():
        values[key] = _normalize_parameter_value(
            key,
            parameter,
            overrides[key] if key in overrides else parameter.get("default"),
        )
    return values


def _normalize_parameter_value(
    key: str,
    parameter: dict[str, Any],
    value: Any,
) -> Any:
    parameter_type = str(parameter.get("type") or "").strip()
    if parameter_type == "number":
        if isinstance(value, bool):
            raise ValueError(f"Parameter {key} должен быть number.")
        try:
            normalized = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Parameter {key} должен быть number.") from exc
        minimum = parameter.get("min")
        maximum = parameter.get("max")
        if minimum is not None and normalized < float(minimum):
            raise ValueError(f"Parameter {key} должен быть >= {minimum}.")
        if maximum is not None and normalized > float(maximum):
            raise ValueError(f"Parameter {key} должен быть <= {maximum}.")
        return int(normalized) if normalized.is_integer() else normalized
    if parameter_type == "select":
        choices = [str(item) for item in parameter.get("values") or []]
        normalized = str(value)
        if normalized not in choices:
            raise ValueError(
                f"Parameter {key} должен быть одним из: {', '.join(choices)}."
            )
        return normalized
    if parameter_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"Parameter {key} должен быть boolean.")
        return value
    if parameter_type == "text":
        if not isinstance(value, str):
            raise ValueError(f"Parameter {key} должен быть text.")
        max_length = parameter.get("max_length")
        if max_length is not None and len(value) > int(max_length):
            raise ValueError(f"Parameter {key} длиннее {max_length} символов.")
        return value
    if parameter_type == "color":
        normalized = str(value or "")
        if not _COLOR_RE.fullmatch(normalized):
            raise ValueError(f"Parameter {key} должен быть цветом #RRGGBB.")
        return normalized.lower()
    raise ValueError(f"Unsupported parameter type: {parameter_type}")


def validate_parameter_values(
    definition: dict[str, Any],
    parameter_values: dict[str, Any] | None = None,
) -> None:
    normalized = normalize_template_definition(definition)
    values = parameter_values or {}
    if not isinstance(values, dict):
        raise ValueError("parameter_values должен быть JSON object.")
    unknown = sorted(str(key) for key in values if str(key) not in normalized["parameters"])
    if unknown:
        raise ValueError("Unknown template parameter: " + ", ".join(unknown))
    for key, value in values.items():
        _normalize_parameter_value(str(key), normalized["parameters"][str(key)], value)


def reaction_required_for_definition(
    definition: dict[str, Any],
    parameter_values: dict[str, Any] | None = None,
) -> bool:
    normalized = normalize_template_definition(definition)
    adapter = require_template_adapter(normalized)
    return bool(adapter.reaction_required_fn(
        normalized,
        effective_parameter_values(normalized, parameter_values),
    ))


def validate_renderer_for_definition(
    definition: dict[str, Any],
    renderer_engine: str | None,
) -> str:
    normalized = normalize_template_definition(definition)
    renderer = str(
        renderer_engine or normalized.get("default_renderer") or ""
    ).strip().lower()
    if renderer == "ffmpeg":
        renderer = "ffmpeg_fast"
    if renderer not in normalized["supported_renderers"]:
        raise ValueError(
            f"Renderer {renderer} не поддерживается template {normalized['key']}."
        )
    return renderer


def _ensure_template_definition_adapter(row: Any) -> Any:
    definition = json.loads(str(row["definition_json"]))
    normalized = normalize_template_definition(definition)
    if normalized != definition:
        db.update_studio_template(
            int(row["id"]),
            name=str(row["name"]),
            status=str(row["status"]),
            definition_json=normalized,
        )
        refreshed = db.get_studio_template(int(row["id"]))
        if refreshed is not None:
            return refreshed
    return row


def ensure_default_studio_templates() -> list[Any]:
    rows: list[Any] = []
    for raw_definition in default_studio_template_definitions():
        definition = normalize_template_definition(raw_definition)
        existing = db.get_latest_studio_template_by_key(definition["key"])
        if existing is not None:
            rows.append(_ensure_template_definition_adapter(existing))
            continue
        try:
            template_id = db.create_studio_template(
                template_key=definition["key"],
                name=definition["name"],
                engine="remotion",
                version=1,
                status="active",
                definition_json=definition,
            )
        except sqlite3.IntegrityError:
            existing = db.get_latest_studio_template_by_key(definition["key"])
            if existing is None:
                raise
            rows.append(_ensure_template_definition_adapter(existing))
            continue
        created = db.get_studio_template(template_id)
        if created is None:
            raise RuntimeError("Studio template создан, но не найден.")
        rows.append(created)
    return rows


def ensure_default_studio_template() -> Any:
    rows = ensure_default_studio_templates()
    existing = db.get_latest_studio_template_by_key("reaction_top_25")
    if existing is not None:
        return existing
    if not rows:
        raise RuntimeError("Default Studio templates не созданы.")
    return rows[0]


def template_row_payload(row: Any) -> dict[str, Any]:
    definition = normalize_template_definition(json.loads(str(row["definition_json"])))
    return {
        key: row[key]
        for key in row.keys()
        if key != "definition_json"
    } | {
        "key": str(row["template_key"]),
        "definition": definition,
        "engine": definition["default_renderer"],
        "supported_renderers": definition["supported_renderers"],
        "default_renderer": definition["default_renderer"],
        "adapter": definition["adapter"],
    }


def parameter_defaults(definition: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_template_definition(definition)
    return {
        key: parameter.get("default")
        for key, parameter in normalized["parameters"].items()
    }


class UnsupportedLegacyTemplateError(ValueError):
    """Raised when an edit_templates.recipe_json cannot be safely converted."""


def _legacy_row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        if key in row.keys():
            return row[key]
    except AttributeError:
        if isinstance(row, dict) and key in row:
            return row[key]
    return default


def _legacy_template_key(row: Any) -> str:
    raw_key = str(
        _legacy_row_value(row, "key")
        or _legacy_row_value(row, "template_key")
        or ""
    ).strip().lower()
    key = re.sub(r"[^a-z0-9_]+", "_", raw_key).strip("_")
    legacy_id = _legacy_row_value(row, "id")
    if len(key) < 2:
        key = f"legacy_{int(legacy_id) if legacy_id is not None else 'template'}"
    return key[:80]


def _legacy_template_name(row: Any, key: str) -> str:
    return str(_legacy_row_value(row, "name") or key).strip() or key


def _legacy_recipe(row: Any) -> dict[str, Any]:
    raw = _legacy_row_value(row, "recipe_json")
    if isinstance(raw, dict):
        recipe = raw
    else:
        try:
            recipe = json.loads(str(raw or "{}"))
        except json.JSONDecodeError as exc:
            raise UnsupportedLegacyTemplateError(
                f"legacy recipe_json invalid: {exc.msg}"
            ) from exc
    if not isinstance(recipe, dict):
        raise UnsupportedLegacyTemplateError("legacy recipe_json must be object")
    return recipe


def _slot_int(slot: dict[str, Any], key: str) -> int | None:
    try:
        return int(slot.get(key))
    except (TypeError, ValueError):
        return None


def _apply_legacy_recipe_defaults(
    definition: dict[str, Any],
    recipe: dict[str, Any],
) -> dict[str, Any]:
    parameters = definition.setdefault("parameters", {})

    def set_default(key: str, value: Any) -> None:
        if key in parameters and value is not None:
            parameters[key]["default"] = value

    slots = recipe.get("slots") if isinstance(recipe.get("slots"), dict) else {}
    main_slot = slots.get("main") if isinstance(slots.get("main"), dict) else {}
    reaction_slot = slots.get("reaction") if isinstance(slots.get("reaction"), dict) else {}
    set_default("main_fit", main_slot.get("fit") or main_slot.get("object_fit"))
    set_default("reaction_fit", reaction_slot.get("fit") or reaction_slot.get("object_fit"))

    layout = recipe.get("layout") if isinstance(recipe.get("layout"), dict) else {}
    set_default("background_color", layout.get("background_color"))

    audio = recipe.get("audio") if isinstance(recipe.get("audio"), dict) else {}
    set_default("main_volume", audio.get("main_volume"))
    set_default("reaction_volume", audio.get("reaction_volume"))
    if "mute_reaction" in audio:
        set_default("mute_reaction", bool(audio.get("mute_reaction")))
    elif str(audio.get("mode") or "").strip().lower() in {"main_only", "mute_reaction"}:
        set_default("mute_reaction", True)
        set_default("reaction_volume", 0)
    elif str(audio.get("mode") or "").strip().lower() in {"mix", "both", "reaction"}:
        set_default("mute_reaction", False)

    overlays = recipe.get("overlays")
    if isinstance(overlays, dict):
        set_default("top_text", overlays.get("top_text"))
        set_default("bottom_text", overlays.get("bottom_text"))
    elif isinstance(overlays, list):
        for item in overlays:
            if not isinstance(item, dict):
                continue
            target = str(item.get("target") or item.get("position") or "").lower()
            text = item.get("text")
            if target == "top":
                set_default("top_text", text)
            elif target == "bottom":
                set_default("bottom_text", text)
    return definition


def legacy_edit_template_to_definition(row: Any) -> dict[str, Any]:
    """Best-effort conversion of archived edit_templates into Studio definitions."""
    key = _legacy_template_key(row)
    name = _legacy_template_name(row, key)
    recipe = _legacy_recipe(row)
    if key in {
        "reaction_top_25",
        "reaction_top_33",
        "reaction_top_50",
        "reaction_bottom_25",
        "reaction_pip_corner",
    }:
        for definition in default_studio_template_definitions():
            if str(definition["key"]) == key:
                return normalize_template_definition(
                    _apply_legacy_recipe_defaults({**definition, "name": name}, recipe)
                )

    slots = recipe.get("slots")
    if not isinstance(slots, dict):
        raise UnsupportedLegacyTemplateError("legacy recipe has no slots object")
    main_slot = slots.get("main")
    reaction_slot = slots.get("reaction")
    if not isinstance(main_slot, dict):
        raise UnsupportedLegacyTemplateError("legacy recipe has no main slot")

    if reaction_slot is None:
        definition = _main_only_definition()
        definition["key"] = key
        definition["name"] = name
        return normalize_template_definition(
            _apply_legacy_recipe_defaults(definition, recipe)
        )
    if not isinstance(reaction_slot, dict):
        raise UnsupportedLegacyTemplateError("legacy reaction slot is not object")

    reaction_height = _slot_int(reaction_slot, "h") or _slot_int(reaction_slot, "height")
    reaction_y = _slot_int(reaction_slot, "y") or 0
    main_y = _slot_int(main_slot, "y") or 0
    if reaction_height is None or not 240 <= reaction_height <= 960:
        raise UnsupportedLegacyTemplateError("legacy reaction slot height is unsupported")
    reaction_position = "top" if reaction_y <= main_y else "bottom"
    layout_variant = "top_reaction" if reaction_position == "top" else "bottom_reaction"
    definition = _reaction_layout_definition(
        key=key,
        name=name,
        reaction_position=reaction_position,
        reaction_height=reaction_height,
        layout_variant=layout_variant,
    )
    return normalize_template_definition(
        _apply_legacy_recipe_defaults(definition, recipe)
    )


def archived_legacy_edit_template_definition(
    row: Any,
    reason: str,
) -> dict[str, Any]:
    key = _legacy_template_key(row)
    name = _legacy_template_name(row, key)
    definition = _main_only_definition()
    definition["key"] = key
    definition["name"] = name
    definition.setdefault("rules", {})["migration_warning"] = reason
    legacy_id = _legacy_row_value(row, "id")
    if legacy_id is not None:
        definition["rules"]["legacy_edit_template_id"] = int(legacy_id)
    return normalize_template_definition(definition)


def unique_duplicate_key(template_key: str) -> str:
    base = f"{template_key}_copy"
    candidate = base
    index = 2
    while db.get_latest_studio_template_by_key(candidate) is not None:
        candidate = f"{base}_{index}"
        index += 1
    return candidate
