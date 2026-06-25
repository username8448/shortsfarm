"""Versioned automation template definitions for Template Studio."""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from . import db


TEMPLATE_STATUSES = {"draft", "active", "archived"}
TEMPLATE_ENGINES = {"remotion", "ffmpeg"}
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{1,79}$")


def default_reaction_top_25_definition() -> dict[str, Any]:
    return {
        "version": 1,
        "key": "reaction_top_25",
        "name": "Reaction Top 25%",
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
        "parameters": {
            "reaction_height": {
                "group": "layout",
                "type": "number",
                "min": 240,
                "max": 960,
                "default": 480,
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
        },
        "rules": {
            "output_duration": "main.duration",
            "reaction_playback": "loop",
            "output_aspect": "9:16",
            "output_folder": "edits",
            "renderer": "remotion",
        },
    }


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

    rules = _object(definition.get("rules"), "template.rules")
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


def ensure_default_studio_template() -> Any:
    existing = db.get_latest_studio_template_by_key("reaction_top_25")
    if existing is not None:
        return existing
    definition = normalize_template_definition(
        default_reaction_top_25_definition()
    )
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
        existing = db.get_latest_studio_template_by_key("reaction_top_25")
        if existing is None:
            raise
        return existing
    created = db.get_studio_template(template_id)
    if created is None:
        raise RuntimeError("Studio template создан, но не найден.")
    return created


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
