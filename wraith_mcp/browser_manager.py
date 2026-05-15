"""Patchright Chromium management and browser stealth hardening."""

import asyncio
import inspect
import os
import random
import re
import subprocess
import sys
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast, get_args, get_origin

from .stealth_scripts import STEALTH_INIT_SCRIPTS

_cached_path: str | None = None
_cached_version: tuple[str, str] | None = None
_patched_context_ids: set[int] = set()
_patched_page_ids: set[int] = set()
_page_handler_context_ids: set[int] = set()
_humanized_page_ids: set[int] = set()
_mouse_positions: dict[int, tuple[float, float]] = {}

_DISABLE_FEATURES = (
    "IsolateOrigins",
    "site-per-process",
    "CrossSiteDocumentBlockingIfIsolating",
    "CrossSiteDocumentBlockingAlways",
    "TranslateUI",
)

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}
_EnvParser = Callable[[str], object]

_SECURITY_POLICY_PROFILE_FIELDS = frozenset({
    "allowed_domains",
    "prohibited_domains",
    "block_ip_addresses",
})
_COMMA_LIST_PROFILE_FIELDS = frozenset({
    "allowed_domains",
    "prohibited_domains",
    "permissions",
})
_BOOL_PROFILE_FIELDS = frozenset({"block_ip_addresses"})


def _comma_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _viewport(value: str) -> dict[str, int]:
    parts = _comma_list(value)
    if len(parts) != 2:
        raise ValueError("BROWSER_VIEWPORT must be WIDTH,HEIGHT.")

    try:
        width = int(parts[0])
        height = int(parts[1])
    except ValueError as exc:
        raise ValueError("BROWSER_VIEWPORT must use integer WIDTH,HEIGHT.") from exc

    if width <= 0 or height <= 0:
        raise ValueError("BROWSER_VIEWPORT width and height must be positive.")
    return {"width": width, "height": height}


def _float_value(value: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"Expected float BrowserProfile env value, got {value!r}.") from exc


def _bool_value(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in _TRUTHY:
        return True
    if normalized in _FALSY:
        return False
    raise ValueError(
        f"Expected boolean BrowserProfile env value, got {value!r}. "
        "Use one of: true, false, 1, 0, yes, no, on, off."
    )


_OPTIONAL_BROWSER_PROFILE_ENVS: tuple[tuple[str, str, _EnvParser], ...] = (
    ("BROWSER_ALLOWED_DOMAINS", "allowed_domains", _comma_list),
    ("BROWSER_PROHIBITED_DOMAINS", "prohibited_domains", _comma_list),
    ("BROWSER_BLOCK_IP_ADDRESSES", "block_ip_addresses", _bool_value),
    ("BROWSER_STORAGE_STATE", "storage_state", str),
    ("BROWSER_USER_DATA_DIR", "user_data_dir", str),
    ("BROWSER_DOWNLOADS_PATH", "downloads_path", str),
    ("BROWSER_RECORD_HAR_PATH", "record_har_path", str),
    ("BROWSER_RECORD_VIDEO_DIR", "record_video_dir", str),
    ("BROWSER_TRACES_DIR", "traces_dir", str),
    ("BROWSER_PERMISSIONS", "permissions", _comma_list),
    ("BROWSER_VIEWPORT", "viewport", _viewport),
    (
        "BROWSER_MINIMUM_WAIT_PAGE_LOAD_TIME",
        "minimum_wait_page_load_time",
        _float_value,
    ),
    (
        "BROWSER_WAIT_FOR_NETWORK_IDLE_PAGE_LOAD_TIME",
        "wait_for_network_idle_page_load_time",
        _float_value,
    ),
    ("BROWSER_WAIT_BETWEEN_ACTIONS", "wait_between_actions", _float_value),
)


def ensure_chromium() -> None:
    """Install Patchright's patched Chromium if not already present."""
    subprocess.run(
        [sys.executable, "-m", "patchright", "install", "chromium"],
        check=True,
    )


def chromium_path() -> str:
    """Return the absolute path to Patchright's patched Chromium binary.

    Installs automatically on first call if the binary is missing.
    """
    global _cached_path
    if _cached_path and Path(_cached_path).exists():
        return _cached_path

    try:
        path = _resolve_path()
    except (OSError, RuntimeError):
        ensure_chromium()
        path = _resolve_path()

    if not Path(path).exists():
        ensure_chromium()
        path = _resolve_path()

    if not Path(path).exists():
        raise FileNotFoundError(
            f"Patchright Chromium not found at {path}. "
            "Run: patchright install chromium"
        )

    _cached_path = path
    return path


def browser_profile_kwargs() -> dict[str, object]:
    """Return BrowserProfile keyword arguments with hardened Chromium launch args."""
    headless = os.environ.get("HEADLESS", "true").lower() == "true"
    proxy = os.environ.get("PROXY_SERVER")
    locale = os.environ.get("BROWSER_LOCALE", "en-US")
    timezone = os.environ.get("BROWSER_TIMEZONE", "America/New_York")

    kwargs: dict[str, object] = {
        "executable_path": chromium_path(),
        "headless": headless,
        "args": stealth_launch_args(),
        "locale": locale,
        "timezone_id": timezone,
    }
    if proxy:
        kwargs["proxy"] = {"server": proxy}
    _apply_optional_browser_profile_env(kwargs)
    return kwargs


def _apply_optional_browser_profile_env(kwargs: dict[str, object]) -> None:
    supported_fields = _browser_profile_supported_fields()
    field_annotations = _browser_profile_field_annotations()

    for env_name, field_name, parser in _OPTIONAL_BROWSER_PROFILE_ENVS:
        raw_value = os.environ.get(env_name)
        if raw_value is None:
            continue

        if not _browser_profile_env_supported(
            field_name,
            supported_fields,
            field_annotations,
        ):
            if field_name in _SECURITY_POLICY_PROFILE_FIELDS:
                raise RuntimeError(
                    _unsupported_browser_profile_option(env_name, field_name)
                )
            continue

        kwargs[field_name] = parser(raw_value)


def _browser_profile_env_supported(
    field_name: str,
    supported_fields: frozenset[str],
    field_annotations: dict[str, object],
) -> bool:
    if field_name not in supported_fields:
        return False
    if field_name in _COMMA_LIST_PROFILE_FIELDS:
        return _annotation_accepts_sequence(field_annotations.get(field_name))
    if field_name in _BOOL_PROFILE_FIELDS:
        return _annotation_accepts_bool(field_annotations.get(field_name))
    return True


def _unsupported_browser_profile_option(env_name: str, field_name: str) -> str:
    return (
        f"{env_name} maps to BrowserProfile.{field_name}, but this Browser Use "
        "version does not support that option shape. "
        f"Upgrade browser-use or unset {env_name}."
    )


def _browser_profile_supported_fields() -> frozenset[str]:
    return frozenset(_browser_profile_field_annotations())


def _browser_profile_field_annotations() -> dict[str, object]:
    browser_profile = _browser_profile_class()
    if browser_profile is None:
        return {}

    model_fields = getattr(browser_profile, "model_fields", None)
    if isinstance(model_fields, Mapping):
        typed_fields = cast(Mapping[object, object], model_fields)
        return {
            str(name): getattr(field, "annotation", None)
            for name, field in typed_fields.items()
        }

    try:
        signature = inspect.signature(browser_profile)
    except (TypeError, ValueError):
        return {}

    allowed_kinds = {
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    }
    return {
        name: cast(object, parameter.annotation)
        for name, parameter in signature.parameters.items()
        if name != "self" and parameter.kind in allowed_kinds
    }


def _browser_profile_class() -> type[object] | None:
    try:
        from browser_use.browser.profile import BrowserProfile
    except ImportError:
        return None
    return BrowserProfile


def _annotation_accepts_sequence(annotation: object) -> bool:
    if (
        annotation is None
        or annotation is inspect.Signature.empty
        or annotation is Any
    ):
        return True
    if isinstance(annotation, str):
        sequence_names = ("Any", "Collection", "Iterable", "Sequence", "list", "set")
        return any(name in annotation for name in sequence_names)

    origin = get_origin(annotation)
    if origin in {list, set, tuple, Collection, Iterable, Sequence}:
        return True

    args = cast(tuple[object, ...], get_args(annotation))
    if args:
        return any(
            arg is not type(None) and _annotation_accepts_sequence(arg)
            for arg in args
        )

    sequence_types = (list, set, tuple, Collection, Iterable, Sequence)
    return any(annotation is sequence_type for sequence_type in sequence_types)


def _annotation_accepts_bool(annotation: object) -> bool:
    if (
        annotation is None
        or annotation is inspect.Signature.empty
        or annotation is Any
    ):
        return True
    if isinstance(annotation, str):
        return "bool" in annotation or "Any" in annotation

    origin = get_origin(annotation)
    if origin is bool:
        return True

    args = cast(tuple[object, ...], get_args(annotation))
    if args:
        return any(arg is not type(None) and _annotation_accepts_bool(arg) for arg in args)

    return annotation is bool


def stealth_launch_args() -> list[str]:
    """Chromium flags from Patchright plus common botright/camoufox stealth practice."""
    lang = os.environ.get("BROWSER_LANG") or os.environ.get("BROWSER_LOCALE", "en-US")
    window_size = os.environ.get("BROWSER_WINDOW_SIZE", "1920,1080")
    return [
        "--disable-blink-features=AutomationControlled",
        "--disable-features=" + ",".join(_DISABLE_FEATURES),
        "--disable-web-security",
        "--allow-running-insecure-content",
        "--disable-client-side-phishing-detection",
        "--disable-component-extensions-with-background-pages",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-hang-monitor",
        "--disable-infobars",
        "--disable-ipc-flooding-protection",
        "--disable-popup-blocking",
        "--disable-prompt-on-repost",
        "--disable-renderer-backgrounding",
        "--disable-sync",
        "--force-color-profile=srgb",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--hide-scrollbars",
        "--metrics-recording-only",
        "--mute-audio",
        "--no-first-run",
        "--no-service-autorun",
        "--password-store=basic",
        "--use-mock-keychain",
        "--export-tagged-pdf",
        "--webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--lang=" + lang,
        "--window-size=" + window_size,
    ]


def stealth_headers() -> dict[str, str]:
    """HTTP headers aligned with locale and the installed Chromium major version."""
    _, major = chromium_version()
    headers = {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "sec-ch-ua": (
            f'"Google Chrome";v="{major}", '
            f'"Chromium";v="{major}", '
            '"Not.A/Brand";v="99"'
        ),
        "sec-ch-ua-mobile": "?0",
    }
    platform = os.environ.get("BROWSER_SEC_CH_UA_PLATFORM")
    if platform:
        headers["sec-ch-ua-platform"] = f'"{platform}"'
    if os.environ.get("BROWSER_DNT", "").lower() in _TRUTHY:
        headers["DNT"] = "1"
    return headers


async def apply_stealth(session: Any) -> None:
    """Apply JS, headers, and interaction humanization to a BrowserSession.

    Patchright does not expose a clean Python hook for intercepting and suppressing
    the Runtime.enable CDP command that some detectors use as an automation signal.
    This layer stays at launch args, browser context, page init scripts, and input
    event wrapping so it remains compatible with browser-use's agent loop.
    """
    page = await session.get_current_page()
    context = _context_from(session, page)

    if context is not None:
        await _apply_context_stealth(context)
        _install_new_page_handler(context)

    if page is not None:
        await _apply_page_stealth(page)


def _resolve_path() -> str:
    from patchright.sync_api import sync_playwright

    with sync_playwright() as p:
        return p.chromium.executable_path


def chromium_version() -> tuple[str, str]:
    """Return (full_version, major_version) for the Patchright Chromium binary."""
    global _cached_version
    if _cached_version is not None:
        return _cached_version

    version = "120.0.0.0"
    try:
        result = subprocess.run(
            [chromium_path(), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = (result.stdout or result.stderr).strip()
        match = re.search(r"(\d+\.\d+\.\d+\.\d+)", output)
        if match:
            version = match.group(1)
    except (OSError, subprocess.SubprocessError, ImportError, RuntimeError):
        pass

    major = version.split(".", 1)[0]
    _cached_version = (version, major)
    return _cached_version


async def _apply_context_stealth(context: Any) -> None:
    context_id = id(context)
    if context_id in _patched_context_ids:
        return

    if hasattr(context, "set_extra_http_headers"):
        await _maybe_await(context.set_extra_http_headers(stealth_headers()))

    if hasattr(context, "add_init_script"):
        for script in STEALTH_INIT_SCRIPTS:
            await _maybe_await(context.add_init_script(script))

    _patched_context_ids.add(context_id)


async def _apply_page_stealth(page: Any) -> None:
    page_id = id(page)
    if page_id not in _patched_page_ids and hasattr(page, "add_init_script"):
        for script in STEALTH_INIT_SCRIPTS:
            await _maybe_await(page.add_init_script(script))
        if hasattr(page, "set_extra_http_headers"):
            await _maybe_await(page.set_extra_http_headers(stealth_headers()))
        _patched_page_ids.add(page_id)

    _install_humanization(page)


def _install_new_page_handler(context: Any) -> None:
    context_id = id(context)
    if context_id in _page_handler_context_ids:
        return
    if not hasattr(context, "on"):
        return

    def on_page(page: Any) -> None:
        try:
            asyncio.create_task(_apply_page_stealth(page))
        except RuntimeError:
            pass

    try:
        context.on("page", on_page)
        _page_handler_context_ids.add(context_id)
    except Exception:
        return


def _install_humanization(page: Any) -> None:
    page_id = id(page)
    if page_id in _humanized_page_ids:
        return

    mouse = getattr(page, "mouse", None)
    keyboard = getattr(page, "keyboard", None)

    if mouse is not None and hasattr(mouse, "click"):
        original_mouse_click = mouse.click

        async def human_click(x: float, y: float, *args: Any, **kwargs: Any) -> Any:
            await _human_mouse_move(page, float(x), float(y))
            await asyncio.sleep(random.uniform(0.05, 0.15))
            result = original_mouse_click(x, y, *args, **kwargs)
            _mouse_positions[page_id] = (float(x), float(y))
            return await _maybe_await(result)

        try:
            mouse.click = human_click
        except Exception:
            pass

    if hasattr(page, "click"):
        original_page_click = page.click

        async def human_page_click(selector: str, *args: Any, **kwargs: Any) -> Any:
            await _move_to_selector_center(page, selector)
            await asyncio.sleep(random.uniform(0.05, 0.15))
            return await _maybe_await(original_page_click(selector, *args, **kwargs))

        try:
            page.click = human_page_click
        except Exception:
            pass

    if keyboard is not None and hasattr(keyboard, "type"):
        original_type = keyboard.type

        async def human_type(text: str, *args: Any, **kwargs: Any) -> Any:
            if isinstance(text, str) and len(text) > 1 and not args and "delay" not in kwargs:
                for char in text:
                    await _maybe_await(original_type(char, delay=random.randint(35, 120), **kwargs))
                return None
            kwargs.setdefault("delay", random.randint(35, 120))
            return await _maybe_await(original_type(text, *args, **kwargs))

        try:
            keyboard.type = human_type
        except Exception:
            pass

    if keyboard is not None and hasattr(keyboard, "insert_text"):
        original_insert_text = keyboard.insert_text

        async def human_insert_text(text: str, *args: Any, **kwargs: Any) -> Any:
            if isinstance(text, str) and len(text) > 1 and not args:
                for char in text:
                    await asyncio.sleep(random.uniform(0.035, 0.12))
                    await _maybe_await(original_insert_text(char, **kwargs))
                return None
            await asyncio.sleep(random.uniform(0.035, 0.12))
            return await _maybe_await(original_insert_text(text, *args, **kwargs))

        try:
            keyboard.insert_text = human_insert_text
        except Exception:
            pass

    _humanized_page_ids.add(page_id)


async def _human_mouse_move(page: Any, target_x: float, target_y: float) -> None:
    mouse = getattr(page, "mouse", None)
    if mouse is None or not hasattr(mouse, "move"):
        return

    page_id = id(page)
    start_x, start_y = _mouse_positions.get(page_id, _initial_mouse_position(page))
    control_1 = (
        start_x + (target_x - start_x) * 0.35 + random.uniform(-80, 80),
        start_y + random.uniform(-60, 60),
    )
    control_2 = (
        start_x + (target_x - start_x) * 0.70 + random.uniform(-80, 80),
        target_y + random.uniform(-60, 60),
    )
    steps = random.randint(8, 16)

    for index in range(1, steps + 1):
        t = index / steps
        x = _cubic_bezier(start_x, control_1[0], control_2[0], target_x, t)
        y = _cubic_bezier(start_y, control_1[1], control_2[1], target_y, t)
        await _maybe_await(mouse.move(x, y))
        await asyncio.sleep(random.uniform(0.003, 0.014))

    _mouse_positions[page_id] = (target_x, target_y)


async def _move_to_selector_center(page: Any, selector: str) -> None:
    if not hasattr(page, "locator"):
        return
    try:
        locator = page.locator(selector).first
        locator = locator() if callable(locator) else locator
        try:
            box = await _maybe_await(locator.bounding_box(timeout=1000))
        except TypeError:
            box = await _maybe_await(locator.bounding_box())
    except Exception:
        return
    if not box:
        return
    target_x = float(box["x"]) + float(box["width"]) / 2
    target_y = float(box["y"]) + float(box["height"]) / 2
    await _human_mouse_move(page, target_x, target_y)


def _context_from(session: Any, page: Any) -> Any | None:
    if page is not None:
        context = getattr(page, "context", None)
        if context is not None:
            return context
    for attr in ("browser_context", "context", "_browser_context", "_context"):
        context = getattr(session, attr, None)
        if context is not None:
            return context
    return None


def _initial_mouse_position(page: Any) -> tuple[float, float]:
    viewport = getattr(page, "viewport_size", None) or {}
    width = float(viewport.get("width", 1920))
    height = float(viewport.get("height", 1080))
    return (
        width * random.uniform(0.35, 0.65),
        height * random.uniform(0.35, 0.65),
    )


def _cubic_bezier(start: float, control_1: float, control_2: float, end: float, t: float) -> float:
    return (
        ((1 - t) ** 3) * start
        + 3 * ((1 - t) ** 2) * t * control_1
        + 3 * (1 - t) * (t**2) * control_2
        + (t**3) * end
    )


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value
