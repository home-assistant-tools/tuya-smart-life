from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "tuya_smart_life_local"
PLATFORMS = [Platform.SWITCH]

CONF_APP_ID = "app_id"
CONF_APP_SECRET = "app_secret"
CONF_APP_VERSION = "app_version"
CONF_BMP_KEY = "bmp_key"
CONF_CERT_SHA256 = "cert_sha256"
CONF_COUNTRY_CODE = "country_code"
CONF_DEVICE_CORE_VERSION = "device_core_version"
CONF_NATIVE_KEY_TEXT = "native_key_text"
CONF_OS_SYSTEM = "os_system"
CONF_PACKAGE_NAME = "package_name"
CONF_SELECTED_HOME_IDS = "selected_home_ids"
CONF_SDK_VERSION = "sdk_version"

DEFAULT_APP_ID = "3cxxt3au9x33ytvq3h9j"
DEFAULT_APP_VERSION = "7.8.6"
DEFAULT_APP_RN_VERSION = "7.8"
DEFAULT_CH_KEY = "4d2696db"
DEFAULT_COUNTRY_CODE = "84"
DEFAULT_DEVICE_CORE_VERSION = "7.8.0"
DEFAULT_NATIVE_KEY_TEXT = (
    "com.tuya.smart_"
    "AC:F3:3A:AD:A7:F6:1C:85:CC:B4:4A:8C:FA:E8:AF:A3:"
    "73:1A:B3:B8:02:9D:B4:97:8C:BA:B2:64:B4:55:D9:54_"
    "f3hd7pet4p83kemjdf5wqsa5tavrv579_"
    "5gdtanjtf38vyxkqh87cjwfcqjhvjjqa"
)
DEFAULT_OS_SYSTEM = "15"
DEFAULT_PACKAGE_NAME = "com.tuya.smart"
DEFAULT_SDK_VERSION = "7.8.0"
DEFAULT_SCAN_INTERVAL_SECONDS = 1800

ENTRY_RUNTIME = "runtime"
