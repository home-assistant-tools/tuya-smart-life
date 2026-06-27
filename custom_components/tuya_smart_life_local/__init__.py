from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .config_flow import mobile_config_from_data
from .const import DOMAIN, PLATFORMS
from .coordinator import TuyaSmartLifeCoordinator, selected_home_ids_from_entry
from .local import TuyaLocalRuntime


@dataclass(slots=True)
class TuyaSmartLifeRuntime:
    coordinator: TuyaSmartLifeCoordinator
    local: TuyaLocalRuntime


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = {**entry.data, **entry.options}
    config = mobile_config_from_data(data)
    selected_home_ids = selected_home_ids_from_entry(entry)

    local_runtime = TuyaLocalRuntime(hass)
    await local_runtime.async_start()
    coordinator = TuyaSmartLifeCoordinator(
        hass,
        entry,
        local_runtime,
        config,
        selected_home_ids,
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await local_runtime.async_stop()
        raise

    _remove_stale_registry_entries(hass, entry, local_runtime)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = TuyaSmartLifeRuntime(
        coordinator=coordinator,
        local=local_runtime,
    )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    runtime: TuyaSmartLifeRuntime | None = hass.data.get(DOMAIN, {}).pop(
        entry.entry_id,
        None,
    )
    if runtime:
        await runtime.local.async_stop()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _remove_stale_registry_entries(
    hass: HomeAssistant,
    entry: ConfigEntry,
    local_runtime: TuyaLocalRuntime,
) -> None:
    active_unique_ids = {
        f"{device.dev_id}_{dp_id}"
        for device, dp_id, _, _ in local_runtime.switch_button_dps()
    }
    active_unique_ids.update(
        f"{device.dev_id}_fan" for device in local_runtime.fan_devices()
    )
    active_unique_ids.update(
        f"{device.dev_id}_online" for device in local_runtime.hub_devices()
    )
    entity_registry = er.async_get(hass)
    for entity in list(entity_registry.entities.values()):
        if entity.platform != DOMAIN or entity.config_entry_id != entry.entry_id:
            continue
        if entity.unique_id not in active_unique_ids:
            entity_registry.async_remove(entity.entity_id)

    active_device_ids = set(local_runtime.devices)
    device_registry = dr.async_get(hass)
    remove_device = getattr(device_registry, "async_remove_device", None)
    if not callable(remove_device):
        return
    for device in list(device_registry.devices.values()):
        if entry.entry_id not in device.config_entries:
            continue
        tuya_ids = {
            identifier
            for domain, identifier in device.identifiers
            if domain == DOMAIN
        }
        if tuya_ids and tuya_ids.isdisjoint(active_device_ids):
            remove_device(device.id)
