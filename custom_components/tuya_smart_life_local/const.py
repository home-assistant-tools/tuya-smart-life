from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "tuya_smart_life_local"
PLATFORMS = [
    Platform.SWITCH,
    Platform.FAN,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.CLIMATE,
    Platform.LIGHT,
    Platform.MEDIA_PLAYER,
    Platform.BUTTON,
]

CONF_APP_ID = "app_id"
CONF_APP_SECRET = "app_secret"
CONF_API_REGION = "api_region"
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
DEFAULT_API_REGION = "auto"
DEFAULT_APP_VERSION = "7.8.6"
DEFAULT_APP_RN_VERSION = "5.84"
DEFAULT_CH_KEY = "3f7060ea"
DEFAULT_COUNTRY_CODE = "84"
DEFAULT_DEVICE_CORE_VERSION = "5.17.0"
DEFAULT_NATIVE_KEY_TEXT = (
    "com.tuya.smart_"
    "93:21:9F:C2:73:E2:20:0F:4A:DE:E5:F7:19:1D:C6:56:"
    "BA:2A:2D:7B:2F:F5:D2:4C:D5:5C:4B:61:55:00:1E:40_"
    "f3hd7pet4p83kemjdf5wqsa5tavrv579_"
    "5gdtanjtf38vyxkqh87cjwfcqjhvjjqa"
)
DEFAULT_OS_SYSTEM = "15"
DEFAULT_PACKAGE_NAME = "com.tuya.smart"
DEFAULT_SDK_VERSION = "5.24.0"
DEFAULT_SCAN_INTERVAL_SECONDS = 1800

MOBILE_API_ENDPOINTS = {
    "us": "https://a1.tuyaus.com/api.json",
    "sg": "https://a1-sg.iotbing.com/api.json",
    "eu": "https://a1.tuyaeu.com/api.json",
    "cn": "https://a1.tuyacn.com/api.json",
    "in": "https://a1.tuyain.com/api.json",
}

ENTRY_RUNTIME = "runtime"
