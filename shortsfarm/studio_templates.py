"""Versioned automation template definitions for Template Studio."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sqlite3
from typing import Any

from . import db


TEMPLATE_STATUSES = {"draft", "active", "archived"}
TEMPLATE_ENGINES = {"remotion", "ffmpeg"}
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{1,79}$")


@dataclass(frozen=True)
class RemotionTemplateAdapter:
    key: str
    composition_id: str
    component: str


REMOTION_TEMPLATE_ADAPTERS: dict[str, RemotionTemplateAdapter] = {
    "reaction_layout": RemotionTemplateAdapter(
        key="reaction_layout",
        composition_id="ReactionLayoutTemplate",
        component="ReactionLayoutTemplate",
    ),
}


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
        "version": 1,
        "key": key,
        "name": name,
        "engine": "remotion",
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
    ]


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} должен быть JSON object.")
    return value


def normalize_template_definition(value: Any) -> dict[str, Any]:
    definition = _object(value, "template definition")
    key = str(definition.get("key") or "").strip().lower()
    if not _KEY_RE.fullmatch(key):
        raise ValueError(
            "Template key должен содержать lowercase letters, digits и underscore."
        )
    name = str(definition.get("name") or "").strip()
    if not name:
        raise ValueError("Template name обязателен.")
    engine = str(definition.get("engine") or "").strip().lower()
    if engine not in TEMPLATE_ENGINES:
        raise ValueError("Template engine должен быть remotion или ffmpeg.")

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

    rules = dict(_object(definition.get("rules"), "template.rules"))
    if engine == "remotion":
        rules.setdefault("renderer", "remotion")
        rules.setdefault("renderer_adapter", "reaction_layout")
        rules.setdefault("composition_id", "ReactionLayoutTemplate")
    return {
        "version": 1,
        "key": key,
        "name": name,
        "engine": engine,
        "canvas": {"width": width, "height": height, "fps": fps},
        "slots": normalized_slots,
        "parameters": normalized_parameters,
        "rules": dict(rules),
    }


def remotion_adapter_for_definition(
    definition: dict[str, Any],
) -> RemotionTemplateAdapter | None:
    normalized = normalize_template_definition(definition)
    if normalized["engine"] != "remotion":
        return None
    adapter_key = str(
        normalized.get("rules", {}).get("renderer_adapter") or ""
    ).strip()
    return REMOTION_TEMPLATE_ADAPTERS.get(adapter_key)


def require_remotion_adapter(
    definition: dict[str, Any],
) -> RemotionTemplateAdapter:
    adapter = remotion_adapter_for_definition(definition)
    if adapter is None:
        raise ValueError("Этот template пока не имеет Remotion renderer adapter.")
    return adapter


def composition_id_for_definition(definition: dict[str, Any]) -> str:
    normalized = normalize_template_definition(definition)
    adapter = require_remotion_adapter(normalized)
    explicit = str(normalized.get("rules", {}).get("composition_id") or "").strip()
    return explicit or adapter.composition_id


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
                engine=definition["engine"],
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
    return {
        key: row[key]
        for key in row.keys()
        if key != "definition_json"
    } | {
        "key": str(row["template_key"]),
        "definition": json.loads(str(row["definition_json"])),
    }


def parameter_defaults(definition: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_template_definition(definition)
    return {
        key: parameter.get("default")
        for key, parameter in normalized["parameters"].items()
    }


def unique_duplicate_key(template_key: str) -> str:
    base = f"{template_key}_copy"
    candidate = base
    index = 2
    while db.get_latest_studio_template_by_key(candidate) is not None:
        candidate = f"{base}_{index}"
        index += 1
    return candidate
