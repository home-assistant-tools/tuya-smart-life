from __future__ import annotations

import json
import logging
import re
from typing import Any

from homeassistant.components.climate import (
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    ClimateEntity,
)
from homeassistant.components.climate.const import ClimateEntityFeature, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TuyaSmartLifeRuntime
from .const import DOMAIN
from .coordinator import TuyaSmartLifeCoordinator
from .models import TuyaIrAction, TuyaIrClimate

_LOGGER = logging.getLogger(__name__)

DEFAULT_TARGET_TEMPERATURE = 26.0
DEFAULT_MIN_TEMPERATURE = 16.0
DEFAULT_MAX_TEMPERATURE = 30.0
DEFAULT_TEMPERATURE_STEP = 1.0

HVAC_MODE_VALUES = {
    HVACMode.COOL: {"0", "cool"},
    HVACMode.HEAT: {"1", "heat"},
    HVACMode.AUTO: {"2", "auto"},
    HVACMode.FAN_ONLY: {"3", "fan", "fan_only", "wind"},
    HVACMode.DRY: {"4", "dry"},
}
HVAC_MODE_LABELS = {
    HVACMode.COOL: ("cool", "cooling", "lanh"),
    HVACMode.HEAT: ("heat", "heating", "suoi"),
    HVACMode.AUTO: ("auto", "automatic"),
    HVACMode.FAN_ONLY: ("fan", "fan only", "wind", "gio"),
    HVACMode.DRY: ("dry", "dehumid", "hut am"),
}
FAN_MODE_VALUES = {
    FAN_AUTO: {"0", "auto"},
    FAN_LOW: {"1", "low"},
    FAN_MEDIUM: {"2", "medium", "mid"},
    FAN_HIGH: {"3", "high"},
}
FAN_MODE_LABELS = {
    FAN_AUTO: ("auto", "automatic"),
    FAN_LOW: ("low", "small", "thap"),
    FAN_MEDIUM: ("medium", "mid", "vua"),
    FAN_HIGH: ("high", "cao", "strong"),
}
FIELD_ALIASES = {
    "power": {"power", "poweropen", "switch", "onoff", "on_off"},
    "mode": {"mode", "hvacmode", "hvac_mode", "workmode", "work_mode"},
    "temp": {"temp", "temperature", "targettemp", "targettemperature"},
    "fan": {"fan", "fanspeed", "fan_speed", "wind", "windspeed", "wind_speed"},
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: TuyaSmartLifeRuntime = hass.data[DOMAIN][entry.entry_id]
    entities = [
        TuyaIrClimateEntity(runtime.coordinator, runtime, climate)
        for climate in runtime.local.ir_climates()
    ]
    async_add_entities(entities)


class TuyaIrClimateEntity(
    CoordinatorEntity[TuyaSmartLifeCoordinator],
    ClimateEntity,
):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_icon = "mdi:air-conditioner"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = DEFAULT_MIN_TEMPERATURE
    _attr_max_temp = DEFAULT_MAX_TEMPERATURE
    _attr_target_temperature_step = DEFAULT_TEMPERATURE_STEP

    def __init__(
        self,
        coordinator: TuyaSmartLifeCoordinator,
        runtime: TuyaSmartLifeRuntime,
        climate: TuyaIrClimate,
    ) -> None:
        super().__init__(coordinator)
        self.runtime = runtime
        self.climate = climate
        self._local_ok: bool | None = None
        self._is_on = False
        self._hvac_mode = _default_hvac_mode(climate.actions)
        self._target_temperature = DEFAULT_TARGET_TEMPERATURE
        self._fan_mode = _default_fan_mode(climate.actions)
        self._attr_unique_id = climate.unique_id
        self._attr_hvac_modes = _supported_hvac_modes(climate.actions)
        self._attr_fan_modes = _supported_fan_modes(climate.actions)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, climate.remote_id)},
            "name": climate.remote_name,
            "manufacturer": "Tuya",
            "model": climate.product_id,
            "via_device": (DOMAIN, climate.hub_dev_id),
        }

    @property
    def current_climate(self) -> TuyaIrClimate | None:
        for climate in self.runtime.local.ir_climates():
            if climate.unique_id == self.climate.unique_id:
                self.climate = climate
                return climate
        return None

    @property
    def supported_features(self) -> ClimateEntityFeature:
        features = ClimateEntityFeature.TARGET_TEMPERATURE
        if self._attr_fan_modes:
            features |= ClimateEntityFeature.FAN_MODE
        if hasattr(ClimateEntityFeature, "TURN_ON"):
            features |= ClimateEntityFeature.TURN_ON
        if hasattr(ClimateEntityFeature, "TURN_OFF"):
            features |= ClimateEntityFeature.TURN_OFF
        return features

    @property
    def available(self) -> bool:
        climate = self.current_climate
        if not climate:
            return False
        hub = self.runtime.local.devices.get(climate.hub_dev_id)
        return bool(hub and hub.ip and hub.local_key) and self._local_ok is not False

    @property
    def hvac_mode(self) -> HVACMode:
        return self._hvac_mode if self._is_on else HVACMode.OFF

    @property
    def target_temperature(self) -> float:
        return self._target_temperature

    @property
    def fan_mode(self) -> str | None:
        return self._fan_mode

    async def async_turn_on(self) -> None:
        mode = self._hvac_mode if self._hvac_mode != HVACMode.OFF else HVACMode.COOL
        await self._async_send(
            {
                "power": True,
                "mode": mode,
                "temp": self._target_temperature,
                "fan": self._fan_mode,
            },
            "power",
        )
        self._is_on = True
        self._hvac_mode = mode
        self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        await self._async_send({"power": False}, "power")
        self._is_on = False
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
            return
        await self._async_send(
            {
                "power": True,
                "mode": hvac_mode,
                "temp": self._target_temperature,
                "fan": self._fan_mode,
            },
            "mode",
        )
        self._is_on = True
        self._hvac_mode = hvac_mode
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get("temperature")
        if temperature is None:
            return
        target = float(temperature)
        hvac_mode = kwargs.get("hvac_mode") or self._hvac_mode
        if hvac_mode == HVACMode.OFF:
            hvac_mode = HVACMode.COOL
        await self._async_send(
            {
                "power": True,
                "mode": hvac_mode,
                "temp": target,
                "fan": self._fan_mode,
            },
            "temp",
        )
        self._is_on = True
        self._hvac_mode = hvac_mode
        self._target_temperature = target
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self._async_send(
            {
                "power": True,
                "mode": self._hvac_mode,
                "temp": self._target_temperature,
                "fan": fan_mode,
            },
            "fan",
        )
        self._is_on = True
        self._fan_mode = fan_mode
        self.async_write_ha_state()

    async def _async_send(self, desired: dict[str, Any], primary: str) -> None:
        climate = self.current_climate
        if not climate:
            raise RuntimeError(f"IR climate {self.climate.unique_id} is no longer available")
        action = _find_climate_action(
            climate.actions,
            desired,
            primary,
        ) or _schema_climate_action(climate, desired)
        if not action:
            raise RuntimeError(
                f"No Tuya IR action matched {primary}={desired.get(primary)} "
                f"for {climate.remote_name}"
            )
        try:
            response = await self.runtime.local.async_publish_ir_action(action)
        except Exception as err:
            self._local_ok = False
            self._async_write_state_if_added()
            raise RuntimeError(
                f"Unable to publish Tuya IR climate action {action.action_name}: {err}"
            ) from err
        if isinstance(response, dict) and response.get("Error"):
            self._local_ok = False
            self._async_write_state_if_added()
            raise RuntimeError(
                f"Unable to publish Tuya IR climate action {action.action_name}: "
                f"{response.get('Error')}"
            )
        self._local_ok = True
        _LOGGER.debug(
            "Published Tuya IR climate action %s via hub %s: %s",
            action.action_name,
            climate.hub_dev_id,
            response,
        )

    def _async_write_state_if_added(self) -> None:
        if self.entity_id:
            self.async_write_ha_state()


def _supported_hvac_modes(actions: list[TuyaIrAction]) -> list[HVACMode]:
    modes = [HVACMode.OFF]
    text = _actions_text(actions)
    for mode in (
        HVACMode.AUTO,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
    ):
        if any(label in text for label in HVAC_MODE_LABELS[mode]):
            modes.append(mode)
    if len(modes) == 1:
        modes.append(HVACMode.COOL)
    return modes


def _supported_fan_modes(actions: list[TuyaIrAction]) -> list[str]:
    text = _actions_text(actions)
    modes = [
        mode
        for mode in (FAN_AUTO, FAN_LOW, FAN_MEDIUM, FAN_HIGH)
        if any(label in text for label in FAN_MODE_LABELS[mode])
    ]
    return modes or [FAN_AUTO, FAN_LOW, FAN_MEDIUM, FAN_HIGH]


def _default_hvac_mode(actions: list[TuyaIrAction]) -> HVACMode:
    modes = _supported_hvac_modes(actions)
    return HVACMode.COOL if HVACMode.COOL in modes else modes[-1]


def _default_fan_mode(actions: list[TuyaIrAction]) -> str:
    fan_modes = _supported_fan_modes(actions)
    return FAN_AUTO if FAN_AUTO in fan_modes else fan_modes[0]


def _find_climate_action(
    actions: list[TuyaIrAction],
    desired: dict[str, Any],
    primary: str,
) -> TuyaIrAction | None:
    best_action: TuyaIrAction | None = None
    best_score = -1
    for action in actions:
        score = _score_action(action, desired, primary)
        if score > best_score:
            best_score = score
            best_action = action
    return best_action if best_score > 0 else None


def _schema_climate_action(
    climate: TuyaIrClimate,
    desired: dict[str, Any],
) -> TuyaIrAction | None:
    schema = _climate_schema(climate.actions)
    if not schema:
        return None
    fields = schema.get("fields")
    if not isinstance(fields, dict):
        return None

    dps: dict[str, Any] = {}
    control_dp = schema.get("control_dp")
    if control_dp and schema.get("control_value") is not None:
        dps[str(control_dp)] = schema["control_value"]

    power = desired.get("power")
    if power is False:
        _schema_put(dps, fields, "power", False)
    else:
        _schema_put(dps, fields, "power", True if power is None else power)
        _schema_put(dps, fields, "mode", desired.get("mode"))
        _schema_put(dps, fields, "temp", desired.get("temp"))
        _schema_put(dps, fields, "fan", desired.get("fan"))

    if not dps:
        return None
    return TuyaIrAction(
        remote_id=climate.remote_id,
        remote_name=climate.remote_name,
        home_id=climate.home_id,
        home_name=climate.home_name,
        hub_dev_id=climate.hub_dev_id,
        action_id="climate_command",
        action_name="Climate Command",
        action_dps=dps,
        report_dps={
            key: value
            for key, value in dps.items()
            if key != str(control_dp)
        },
        product_id=climate.product_id,
        category=climate.category,
        raw={"schema": schema},
    )


def _climate_schema(actions: list[TuyaIrAction]) -> dict[str, Any] | None:
    for action in actions:
        schema = action.raw.get("schema") if isinstance(action.raw, dict) else None
        if isinstance(schema, dict) and schema.get("kind") == "climate":
            return schema
    return None


def _schema_put(
    dps: dict[str, Any],
    fields: dict[str, Any],
    field: str,
    desired: Any,
) -> None:
    if desired in (None, ""):
        return
    spec = fields.get(field)
    if not isinstance(spec, dict):
        return
    dp = spec.get("dp")
    if not dp:
        return
    dps[str(dp)] = _schema_value(field, desired, spec)


def _schema_value(field: str, desired: Any, spec: dict[str, Any]) -> Any:
    values = spec.get("values")
    pairs = values if isinstance(values, list) else []
    if field == "power":
        for pair in pairs:
            if isinstance(pair, dict) and pair.get("value") is desired:
                return pair["value"]
        return bool(desired)
    if field == "mode" and isinstance(desired, HVACMode):
        desired_values = HVAC_MODE_VALUES.get(desired, set())
        desired_labels = HVAC_MODE_LABELS.get(desired, ())
        found = _matching_schema_value(pairs, desired_values, desired_labels)
        return found if found is not None else next(iter(desired_values), str(desired))
    if field == "fan" and isinstance(desired, str):
        desired_values = FAN_MODE_VALUES.get(desired, set())
        desired_labels = FAN_MODE_LABELS.get(desired, ())
        found = _matching_schema_value(pairs, desired_values, desired_labels)
        return found if found is not None else desired
    if field == "temp":
        number = float(desired)
        return int(number) if number.is_integer() else number
    return desired


def _matching_schema_value(
    pairs: list[Any],
    desired_values: set[str],
    desired_labels: tuple[str, ...],
) -> Any:
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        value = pair.get("value")
        label = pair.get("label")
        if _normalize_value(value) in desired_values:
            return value
        label_text = _normalize_value(label)
        if any(alias == label_text for alias in desired_labels):
            return value
    return None


def _score_action(action: TuyaIrAction, desired: dict[str, Any], primary: str) -> int:
    fields = _action_fields(action)
    text = _action_text(action)
    primary_score = _field_match_score(primary, desired.get(primary), fields, text)
    if primary_score <= 0:
        return -1

    score = primary_score + 10
    for field, value in desired.items():
        if field == primary or value in (None, ""):
            continue
        field_score = _field_match_score(field, value, fields, text)
        if field_score > 0:
            score += field_score
        elif field in fields:
            score -= 4
    return score


def _field_match_score(
    field: str,
    desired: Any,
    fields: dict[str, set[str]],
    text: str,
) -> int:
    if desired in (None, ""):
        return 0
    values = fields.get(field, set())
    if field == "power":
        desired_values = {"1", "true", "on", "open"} if desired else {
            "0",
            "false",
            "off",
            "close",
        }
        if values.intersection(desired_values):
            return 8
        labels = ("on", "open", "power on") if desired else ("off", "close", "power off")
        return 4 if any(label in text for label in labels) else 0
    if field == "mode" and isinstance(desired, HVACMode):
        if values.intersection(HVAC_MODE_VALUES.get(desired, set())):
            return 8
        return 4 if any(label in text for label in HVAC_MODE_LABELS[desired]) else 0
    if field == "fan" and isinstance(desired, str):
        if values.intersection(FAN_MODE_VALUES.get(desired, set())):
            return 8
        return 4 if any(label in text for label in FAN_MODE_LABELS.get(desired, ())) else 0
    if field == "temp":
        wanted = _number_text(desired)
        if not wanted:
            return 0
        if any(_number_text(value) == wanted for value in values):
            return 8
        return 4 if wanted in set(re.findall(r"\d+(?:\.\d+)?", text)) else 0
    return 0


def _action_fields(action: TuyaIrAction) -> dict[str, set[str]]:
    fields: dict[str, set[str]] = {}
    for source in (action.action_dps, action.report_dps, action.raw):
        _collect_fields(source, fields)

    detail = action.raw.get("detail") if isinstance(action.raw, dict) else {}
    function = action.raw.get("function") if isinstance(action.raw, dict) else {}
    dp_code = ""
    if isinstance(detail, dict):
        dp_code = str(detail.get("dpCode") or detail.get("functionCode") or "")
    if not dp_code and isinstance(function, dict):
        dp_code = str(function.get("functionCode") or "")
    field = _field_for_key(dp_code)
    if field:
        for value in action.action_dps.values():
            fields.setdefault(field, set()).add(_normalize_value(value))
    return fields


def _collect_fields(value: Any, fields: dict[str, set[str]], active_field: str | None = None) -> None:
    if isinstance(value, dict):
        code = value.get("code") or value.get("dpCode") or value.get("functionCode")
        field = _field_for_key(str(code or "")) or active_field
        if field and "value" in value:
            fields.setdefault(field, set()).add(_normalize_value(value.get("value")))
        for key, child in value.items():
            child_field = _field_for_key(str(key)) or field
            if child_field and not isinstance(child, (dict, list)):
                fields.setdefault(child_field, set()).add(_normalize_value(child))
            _collect_fields(child, fields, child_field)
        return
    if isinstance(value, list):
        for child in value:
            _collect_fields(child, fields, active_field)


def _field_for_key(key: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", "", key.lower())
    for field, aliases in FIELD_ALIASES.items():
        if normalized in {re.sub(r"[^a-z0-9]+", "", alias) for alias in aliases}:
            return field
    return None


def _actions_text(actions: list[TuyaIrAction]) -> str:
    return " ".join(_action_text(action) for action in actions)


def _action_text(action: TuyaIrAction) -> str:
    return " ".join(
        (
            action.remote_name,
            action.action_name,
            action.category or "",
            json.dumps(action.action_dps, ensure_ascii=False, default=str),
            json.dumps(action.report_dps, ensure_ascii=False, default=str),
            json.dumps(action.raw, ensure_ascii=False, default=str),
        )
    ).lower()


def _normalize_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip().lower()


def _number_text(value: Any) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return str(int(number))
    return str(number)
