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
    FAN_MEDIUM: {"2", "medium", "middle", "mid"},
    FAN_HIGH: {"3", "high"},
}
FAN_MODE_LABELS = {
    FAN_AUTO: ("auto", "automatic"),
    FAN_LOW: ("low", "small", "thap"),
    FAN_MEDIUM: ("medium", "middle", "mid", "vua"),
    FAN_HIGH: ("high", "cao", "strong"),
}
TEMP_UP_LABELS = (
    "temp up",
    "tempup",
    "temperature up",
    "plus",
    "increase",
    "higher",
    "tang",
    "tăng",
)
TEMP_DOWN_LABELS = (
    "temp down",
    "tempdown",
    "temperature down",
    "minus",
    "decrease",
    "lower",
    "giam",
    "giảm",
)
FIELD_ALIASES = {
    "power": {"101", "power", "poweropen", "switch", "onoff", "on_off"},
    "mode": {"102", "mode", "hvacmode", "hvac_mode", "workmode", "work_mode"},
    "temp": {"103", "temp", "temperature", "targettemp", "targettemperature"},
    "fan": {"104", "fan", "fanspeed", "fan_speed", "wind", "windspeed", "wind_speed"},
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
        temperature_bounds = _temperature_bounds(climate.actions)
        if temperature_bounds:
            self._attr_min_temp, self._attr_max_temp = temperature_bounds
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
        return bool(hub and hub.ip and hub.local_key)

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
        desired = {
            "power": True,
            "mode": hvac_mode,
            "temp": self._target_temperature,
            "fan": self._fan_mode,
        }
        await self._async_send(desired, "mode")
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
        desired = {
            "power": True,
            "mode": hvac_mode,
            "temp": target,
            "fan": self._fan_mode,
        }
        try:
            await self._async_send(desired, "temp")
        except RuntimeError:
            await self._async_send_temperature_step(target)
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

    async def _async_send(
        self,
        desired: dict[str, Any],
        primary: str,
    ) -> None:
        climate = self.current_climate
        if not climate:
            raise RuntimeError(f"IR climate {self.climate.unique_id} is no longer available")
        actions = _candidate_climate_actions(
            climate,
            desired,
            primary,
        )
        if not actions:
            raise RuntimeError(
                f"No Tuya IR action matched {primary}={desired.get(primary)} "
                f"for {climate.remote_name}"
            )

        last_error: str | None = None
        for action in actions:
            try:
                response = await self.runtime.local.async_publish_ir_action(action)
            except Exception as err:
                last_error = f"{action.action_name}: {err}"
                _LOGGER.debug(
                    "Unable to publish Tuya IR climate action %s; trying fallback",
                    action.action_name,
                    exc_info=True,
                )
                continue
            if isinstance(response, dict) and response.get("Error"):
                last_error = f"{action.action_name}: {response.get('Error')}"
                _LOGGER.debug(
                    "Tuya IR climate action %s failed with response %s; trying fallback",
                    action.action_name,
                    response,
                )
                continue
            self._local_ok = True
            _LOGGER.debug(
                "Published Tuya IR climate action %s via hub %s: %s",
                action.action_name,
                climate.hub_dev_id,
                response,
            )
            return

        self._local_ok = False
        self._async_write_state_if_added()
        raise RuntimeError(
            f"Unable to publish Tuya IR climate action for {climate.remote_name}: "
            f"{last_error or 'unknown error'}"
        )

    async def _async_send_temperature_step(
        self,
        target: float,
        *,
        force_nudge: bool = False,
    ) -> None:
        climate = self.current_climate
        if not climate:
            raise RuntimeError(f"IR climate {self.climate.unique_id} is no longer available")
        action = _find_temperature_step_action(
            climate.actions,
            current=self._target_temperature,
            target=target,
            force_nudge=force_nudge,
        )
        if not action:
            raise RuntimeError(
                f"No Tuya IR temperature step action matched target={target} "
                f"for {climate.remote_name}"
            )
        response = await self.runtime.local.async_publish_ir_action(action)
        if isinstance(response, dict) and response.get("Error"):
            raise RuntimeError(
                f"Unable to publish Tuya IR temperature step action "
                f"{action.action_name}: {response.get('Error')}"
            )

    def _async_write_state_if_added(self) -> None:
        if self.entity_id:
            self.async_write_ha_state()


def _supported_hvac_modes(actions: list[TuyaIrAction]) -> list[HVACMode]:
    modes = [HVACMode.OFF]
    report_values = _reported_values(actions, "102")
    for mode in (
        HVACMode.AUTO,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
    ):
        if report_values.intersection(HVAC_MODE_VALUES[mode]):
            modes.append(mode)

    text = _actions_text(actions)
    for mode in (
        HVACMode.AUTO,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
    ):
        if mode not in modes and any(label in text for label in HVAC_MODE_LABELS[mode]):
            modes.append(mode)
    if len(modes) == 1:
        modes.append(HVACMode.COOL)
    return modes


def _supported_fan_modes(actions: list[TuyaIrAction]) -> list[str]:
    report_values = _reported_values(actions, "104")
    modes = [
        mode
        for mode in (FAN_AUTO, FAN_LOW, FAN_MEDIUM, FAN_HIGH)
        if report_values.intersection(FAN_MODE_VALUES[mode])
    ]
    if modes:
        return modes

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
    best_priority = -1
    for action in actions:
        if _is_schema_action(action):
            continue
        score = _score_action(action, desired, primary)
        priority = _action_priority(action)
        if score > best_score or (score == best_score and priority > best_priority):
            best_score = score
            best_priority = priority
            best_action = action
    return best_action if best_score > 0 else None


def _candidate_climate_actions(
    climate: TuyaIrClimate,
    desired: dict[str, Any],
    primary: str,
) -> list[TuyaIrAction]:
    exact_action = _find_exact_state_action(climate.actions, desired, relax_temperature=False)
    relaxed_action = _find_exact_state_action(
        climate.actions,
        desired,
        relax_temperature=primary != "temp",
    )
    schema_action = _schema_climate_action(climate, desired)
    matched_action = _find_climate_action(climate.actions, desired, primary)
    # Prefer cached DP201 keydata commands. Schema commands are a last-resort fallback
    # because IR hub remotes are not real LAN child devices.
    ordered = (exact_action, relaxed_action, matched_action, schema_action)
    actions: list[TuyaIrAction] = []
    seen: set[tuple[str, str]] = set()
    for action in ordered:
        if not action:
            continue
        key = (action.action_id, json.dumps(action.action_dps, sort_keys=True, default=str))
        if key in seen:
            continue
        seen.add(key)
        actions.append(action)
    return actions


def _find_exact_state_action(
    actions: list[TuyaIrAction],
    desired: dict[str, Any],
    *,
    relax_temperature: bool,
) -> TuyaIrAction | None:
    expected = _desired_report_dps(desired)
    if not expected:
        return None
    if relax_temperature and "103" in expected:
        expected = {key: value for key, value in expected.items() if key != "103"}

    best_action: TuyaIrAction | None = None
    best_score = -1
    for action in actions:
        if set(map(str, action.action_dps)) != {"201"}:
            continue
        report = {str(key): value for key, value in action.report_dps.items()}
        if all(_report_value_matches(report.get(key), value) for key, value in expected.items()):
            score = _action_priority(action) + len(report)
            if "103" in report:
                score += 2
            if score > best_score:
                best_action = action
                best_score = score
    return best_action


def _desired_report_dps(desired: dict[str, Any]) -> dict[str, Any]:
    if desired.get("power") is False:
        return {"101": False}
    expected: dict[str, Any] = {"101": True}
    mode = desired.get("mode")
    if isinstance(mode, HVACMode):
        mode_value = _first_sorted_value(HVAC_MODE_VALUES.get(mode, set()))
        if mode_value is not None:
            expected["102"] = mode_value
    temp = desired.get("temp")
    if temp not in (None, ""):
        expected["103"] = int(float(temp))
    fan = desired.get("fan")
    if isinstance(fan, str):
        fan_value = _first_sorted_value(FAN_MODE_VALUES.get(fan, set()))
        if fan_value is not None:
            expected["104"] = fan_value
    return expected


def _first_sorted_value(values: set[str]) -> str | None:
    if not values:
        return None
    numeric = sorted(value for value in values if value.isdecimal())
    return numeric[0] if numeric else sorted(values)[0]


def _report_value_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool):
        if isinstance(actual, bool):
            return actual is expected
        return _normalize_value(actual) == ("true" if expected else "false")
    if isinstance(expected, int):
        try:
            return int(float(actual)) == expected
        except (TypeError, ValueError):
            return False
    return _normalize_value(actual) == _normalize_value(expected)


def _reported_values(actions: list[TuyaIrAction], dp_id: str) -> set[str]:
    values: set[str] = set()
    for action in actions:
        for key, value in action.report_dps.items():
            if str(key) == dp_id and value is not None:
                values.add(_normalize_value(value))
    return values


def _temperature_bounds(actions: list[TuyaIrAction]) -> tuple[float, float] | None:
    temperatures: list[float] = []
    for action in actions:
        for key, value in action.report_dps.items():
            if str(key) != "103":
                continue
            try:
                temperatures.append(float(value))
            except (TypeError, ValueError):
                continue
    if not temperatures:
        return None
    return min(temperatures), max(temperatures)


def _find_temperature_step_action(
    actions: list[TuyaIrAction],
    *,
    current: float,
    target: float,
    force_nudge: bool,
) -> TuyaIrAction | None:
    if target < current:
        labels = TEMP_DOWN_LABELS
    elif target > current:
        labels = TEMP_UP_LABELS
    elif force_nudge:
        labels = TEMP_DOWN_LABELS
    else:
        return None

    best_action: TuyaIrAction | None = None
    best_priority = -1
    for action in actions:
        if _is_schema_action(action):
            continue
        text = _action_text(action)
        if not any(label in text for label in labels):
            continue
        priority = _action_priority(action)
        if priority > best_priority:
            best_action = action
            best_priority = priority
    return best_action


def _action_priority(action: TuyaIrAction) -> int:
    priority = 0
    if "201" in {str(dp_id) for dp_id in action.action_dps}:
        priority += 20
    if str(action.action_id).startswith("keydata_"):
        priority += 10
    schema = action.raw.get("schema") if isinstance(action.raw, dict) else None
    if isinstance(schema, dict) and schema.get("kind") == "climate":
        priority -= 10
    return priority


def _is_schema_action(action: TuyaIrAction) -> bool:
    schema = action.raw.get("schema") if isinstance(action.raw, dict) else None
    return isinstance(schema, dict) and schema.get("kind") == "climate"


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
            return -1
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
