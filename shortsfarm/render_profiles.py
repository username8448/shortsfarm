"""Render profiles for Studio batch rendering on weak machines."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


RENDER_ENGINES = {"ffmpeg_fast", "remotion"}
DEFAULT_RENDER_ENGINE = "ffmpeg_fast"
DEFAULT_RENDER_PROFILE = "low_540p"


@dataclass(frozen=True)
class RenderProfile:
    key: str
    label: str
    width: int
    height: int
    fps: int
    crf: int
    preset: str
    max_duration_sec: int
    timeout_sec: int

    def payload(self) -> dict[str, Any]:
        return asdict(self)


RENDER_PROFILES: dict[str, RenderProfile] = {
    "draft_360p": RenderProfile(
        key="draft_360p",
        label="Черновик 360p — очень быстро",
        width=360,
        height=640,
        fps=20,
        crf=32,
        preset="ultrafast",
        max_duration_sec=30,
        timeout_sec=10 * 60,
    ),
    "low_540p": RenderProfile(
        key="low_540p",
        label="Низкое 540p — рекомендовано для слабого ноутбука",
        width=540,
        height=960,
        fps=24,
        crf=30,
        preset="veryfast",
        max_duration_sec=45,
        timeout_sec=15 * 60,
    ),
    "sd_720p": RenderProfile(
        key="sd_720p",
        label="SD 720p",
        width=720,
        height=1280,
        fps=30,
        crf=28,
        preset="faster",
        max_duration_sec=60,
        timeout_sec=25 * 60,
    ),
    "hd_1080p": RenderProfile(
        key="hd_1080p",
        label="HD 1080p",
        width=1080,
        height=1920,
        fps=30,
        crf=23,
        preset="medium",
        max_duration_sec=60,
        timeout_sec=45 * 60,
    ),
}


def get_render_profile(key: str | None) -> RenderProfile:
    normalized = str(key or DEFAULT_RENDER_PROFILE).strip()
    try:
        return RENDER_PROFILES[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown render_profile: {normalized}") from exc


def normalize_render_engine(value: str | None) -> str:
    normalized = str(value or DEFAULT_RENDER_ENGINE).strip().lower()
    if normalized not in RENDER_ENGINES:
        raise ValueError("renderer_engine должен быть ffmpeg_fast или remotion.")
    return normalized


def normalize_duration_limit(
    value: float | int | str | None,
    *,
    profile: RenderProfile,
    full_length: bool,
) -> float | None:
    if full_length:
        return None
    if value in {None, ""}:
        return float(profile.max_duration_sec)
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("duration_limit_sec должен быть числом секунд.") from exc
    if normalized <= 0:
        raise ValueError("duration_limit_sec должен быть больше 0.")
    return normalized


def normalize_start_offset(value: float | int | str | None) -> float:
    if value in {None, ""}:
        return 0.0
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("start_offset_sec должен быть числом секунд.") from exc
    if normalized < 0:
        raise ValueError("start_offset_sec не может быть отрицательным.")
    return normalized


def render_profiles_payload() -> dict[str, Any]:
    return {
        "default_engine": DEFAULT_RENDER_ENGINE,
        "default_profile": DEFAULT_RENDER_PROFILE,
        "engines": sorted(RENDER_ENGINES),
        "profiles": [profile.payload() for profile in RENDER_PROFILES.values()],
    }
