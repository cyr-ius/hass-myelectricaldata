"""MyElectrical entity definitions."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, URL
from .coordinator import EnedisDataUpdateCoordinator


class MyElectricalEntity(CoordinatorEntity[EnedisDataUpdateCoordinator]):
    """Representation of a MyElectrical entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EnedisDataUpdateCoordinator,
        description: EntityDescription,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description

        self._attr_unique_id = f"{coordinator.pdl}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.pdl)},
            name=f"{DOMAIN} ({coordinator.pdl})",
            configuration_url=URL,
            manufacturer=MANUFACTURER,
            model=coordinator.api.contract.subscribed_power
            if coordinator.api.contract is not None
            else None,
        )
