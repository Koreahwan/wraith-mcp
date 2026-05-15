import pytest

from wraith_mcp import browser_manager


OPTIONAL_ENV_NAMES = (
    "BROWSER_ALLOWED_DOMAINS",
    "BROWSER_PROHIBITED_DOMAINS",
    "BROWSER_BLOCK_IP_ADDRESSES",
    "BROWSER_STORAGE_STATE",
    "BROWSER_USER_DATA_DIR",
    "BROWSER_DOWNLOADS_PATH",
    "BROWSER_RECORD_HAR_PATH",
    "BROWSER_RECORD_VIDEO_DIR",
    "BROWSER_TRACES_DIR",
    "BROWSER_PERMISSIONS",
    "BROWSER_VIEWPORT",
    "BROWSER_MINIMUM_WAIT_PAGE_LOAD_TIME",
    "BROWSER_WAIT_FOR_NETWORK_IDLE_PAGE_LOAD_TIME",
    "BROWSER_WAIT_BETWEEN_ACTIONS",
)
OPTIONAL_FIELD_NAMES = frozenset({
    "allowed_domains",
    "prohibited_domains",
    "block_ip_addresses",
    "storage_state",
    "user_data_dir",
    "downloads_path",
    "record_har_path",
    "record_video_dir",
    "traces_dir",
    "permissions",
    "viewport",
    "minimum_wait_page_load_time",
    "wait_for_network_idle_page_load_time",
    "wait_between_actions",
})
BASE_BROWSER_ENV_NAMES = (
    "HEADLESS",
    "PROXY_SERVER",
    "BROWSER_LANG",
    "BROWSER_WINDOW_SIZE",
    "BROWSER_LOCALE",
    "BROWSER_TIMEZONE",
)
LIST_FIELD_ANNOTATIONS = {
    "allowed_domains": list[str],
    "prohibited_domains": list[str],
    "block_ip_addresses": bool,
    "permissions": list[str],
}


def _prepare_browser_kwargs(
    monkeypatch: pytest.MonkeyPatch,
    unsupported_fields: set[str] | None = None,
    field_annotations: dict[str, object] | None = None,
) -> None:
    for env_name in BASE_BROWSER_ENV_NAMES + OPTIONAL_ENV_NAMES:
        monkeypatch.delenv(env_name, raising=False)

    unsupported = unsupported_fields or set()
    supported_fields = OPTIONAL_FIELD_NAMES - unsupported
    annotations = field_annotations or LIST_FIELD_ANNOTATIONS

    monkeypatch.setattr(browser_manager, "chromium_path", lambda: "/tmp/chromium")
    monkeypatch.setattr(
        browser_manager,
        "_browser_profile_supported_fields",
        lambda: frozenset(supported_fields),
    )
    monkeypatch.setattr(
        browser_manager,
        "_browser_profile_field_annotations",
        lambda: annotations,
    )


def test_default_kwargs_do_not_include_absent_optional_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_browser_kwargs(monkeypatch)

    kwargs = browser_manager.browser_profile_kwargs()

    assert kwargs["executable_path"] == "/tmp/chromium"
    assert kwargs["headless"] is True
    assert kwargs["locale"] == "en-US"
    assert kwargs["timezone_id"] == "America/New_York"
    args = kwargs["args"]
    assert isinstance(args, list)
    assert "--lang=en-US" in args
    assert "--window-size=1920,1080" in args
    assert "proxy" not in kwargs
    for field_name in OPTIONAL_FIELD_NAMES:
        assert field_name not in kwargs


def test_browser_profile_env_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_browser_kwargs(monkeypatch)
    monkeypatch.setenv("HEADLESS", "false")
    monkeypatch.setenv("PROXY_SERVER", "http://user:pass@proxy:8080")
    monkeypatch.setenv("BROWSER_LANG", "fr-FR")
    monkeypatch.setenv("BROWSER_WINDOW_SIZE", "1440,900")
    monkeypatch.setenv("BROWSER_LOCALE", "fr-FR")
    monkeypatch.setenv("BROWSER_TIMEZONE", "Europe/Paris")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "example.com, *.example.org")
    monkeypatch.setenv("BROWSER_PROHIBITED_DOMAINS", "ads.example.com, trackers.test")
    monkeypatch.setenv("BROWSER_BLOCK_IP_ADDRESSES", "true")
    monkeypatch.setenv("BROWSER_STORAGE_STATE", ".wraith/storage.json")
    monkeypatch.setenv("BROWSER_USER_DATA_DIR", ".wraith/user-data")
    monkeypatch.setenv("BROWSER_DOWNLOADS_PATH", ".wraith/downloads")
    monkeypatch.setenv("BROWSER_RECORD_HAR_PATH", ".wraith/session.har")
    monkeypatch.setenv("BROWSER_RECORD_VIDEO_DIR", ".wraith/videos")
    monkeypatch.setenv("BROWSER_TRACES_DIR", ".wraith/traces")
    monkeypatch.setenv("BROWSER_PERMISSIONS", "clipboard-read, clipboard-write")
    monkeypatch.setenv("BROWSER_VIEWPORT", "1366,768")
    monkeypatch.setenv("BROWSER_MINIMUM_WAIT_PAGE_LOAD_TIME", "1.25")
    monkeypatch.setenv("BROWSER_WAIT_FOR_NETWORK_IDLE_PAGE_LOAD_TIME", "2.5")
    monkeypatch.setenv("BROWSER_WAIT_BETWEEN_ACTIONS", "0.75")

    kwargs = browser_manager.browser_profile_kwargs()

    assert kwargs["headless"] is False
    assert kwargs["locale"] == "fr-FR"
    assert kwargs["timezone_id"] == "Europe/Paris"
    assert kwargs["proxy"] == {"server": "http://user:pass@proxy:8080"}
    args = kwargs["args"]
    assert isinstance(args, list)
    assert "--lang=fr-FR" in args
    assert "--window-size=1440,900" in args
    assert kwargs["allowed_domains"] == ["example.com", "*.example.org"]
    assert kwargs["prohibited_domains"] == ["ads.example.com", "trackers.test"]
    assert kwargs["block_ip_addresses"] is True
    assert kwargs["storage_state"] == ".wraith/storage.json"
    assert kwargs["user_data_dir"] == ".wraith/user-data"
    assert kwargs["downloads_path"] == ".wraith/downloads"
    assert kwargs["record_har_path"] == ".wraith/session.har"
    assert kwargs["record_video_dir"] == ".wraith/videos"
    assert kwargs["traces_dir"] == ".wraith/traces"
    assert kwargs["permissions"] == ["clipboard-read", "clipboard-write"]
    assert kwargs["viewport"] == {"width": 1366, "height": 768}
    assert kwargs["minimum_wait_page_load_time"] == 1.25
    assert kwargs["wait_for_network_idle_page_load_time"] == 2.5
    assert kwargs["wait_between_actions"] == 0.75


@pytest.mark.parametrize(
    ("env_name", "field_name", "value"),
    [
        ("BROWSER_ALLOWED_DOMAINS", "allowed_domains", "example.com"),
        ("BROWSER_PROHIBITED_DOMAINS", "prohibited_domains", "example.com"),
        ("BROWSER_BLOCK_IP_ADDRESSES", "block_ip_addresses", "true"),
    ],
)
def test_security_policy_env_fails_closed_when_unsupported(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    field_name: str,
    value: str,
) -> None:
    _prepare_browser_kwargs(monkeypatch, unsupported_fields={field_name})
    monkeypatch.setenv(env_name, value)

    with pytest.raises(RuntimeError, match=env_name):
        _ = browser_manager.browser_profile_kwargs()


def test_security_policy_env_fails_closed_when_bool_shape_is_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_browser_kwargs(
        monkeypatch,
        field_annotations={"block_ip_addresses": list[str]},
    )
    monkeypatch.setenv("BROWSER_BLOCK_IP_ADDRESSES", "true")

    with pytest.raises(RuntimeError, match="BROWSER_BLOCK_IP_ADDRESSES"):
        _ = browser_manager.browser_profile_kwargs()


def test_unsupported_non_security_field_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_browser_kwargs(monkeypatch, unsupported_fields={"record_har_path"})
    monkeypatch.setenv("BROWSER_RECORD_HAR_PATH", ".wraith/session.har")

    kwargs = browser_manager.browser_profile_kwargs()

    assert "record_har_path" not in kwargs
