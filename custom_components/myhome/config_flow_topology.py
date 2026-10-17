"""Shared-bus topology steps and options helpers for MyHOME config flow."""

from __future__ import annotations

import typing
from types import SimpleNamespace

import voluptuous as vol
from homeassistant.config_entries import SOURCE_IGNORE, ConfigEntry, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector
from voluptuous import Required

from .const import (
    CONF_BUS_TOPOLOGY,
    CONF_DELEGATED_WHOS,
    CONF_GATEWAY_ROLE,
    CONF_PRIMARY_GATEWAY,
    DOMAIN,
    LOGGER,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    ROLE_STANDBY,
    TOPOLOGY_SHARED,
    TOPOLOGY_STANDALONE,
)
from .topology import (
    entry_for_mac,
    entry_is_follower,
    entry_mac,
    recommend_follower,
    validate_shared_bus_topology,
)
from .typing_compat import flow_schema


class ConfigFlowTopologyMixin:
    """Shared-bus onboarding step mixin for MyhomeFlowHandler."""

    hass: typing.Any
    _pending_entry: tuple[str, dict[str, typing.Any], dict[str, typing.Any]] | None

    def _bus_primaries(self) -> list[ConfigEntry]:
        """Configured gateways a new one could share an SCS bus with.

        IP gateways that are not followers themselves; a USB / serial gateway
        is not on an SCS bus.
        """
        return [
            entry
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.source != SOURCE_IGNORE
            and entry.data.get("transport_type") != "serial"
            and not entry_is_follower(entry)
        ]

    async def async_step_bus_topology(
        self, user_input: dict[str, typing.Any] | None = None
    ) -> ConfigFlowResult:
        """Ask whether the new gateway shares its SCS bus with a configured one (#524).

        Asked before the entry exists: a gateway set up as a standalone primary
        sweeps and discovers the whole bus at once, duplicating every device
        the other gateway already has. Joining as that gateway's secondary or
        standby from the start avoids it.
        """
        assert self._pending_entry is not None
        title, data, options = self._pending_entry
        primaries = self._bus_primaries()
        pending = SimpleNamespace(
            entry_id=None, data=data, options={}, unique_id=data[CONF_MAC], title=title
        )
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input.get(CONF_BUS_TOPOLOGY) != TOPOLOGY_SHARED:
                return self.async_create_entry(  # type: ignore[attr-defined,no-any-return]
                    title=title,
                    data=data,
                    options={**options, CONF_BUS_TOPOLOGY: TOPOLOGY_STANDALONE},
                )

            primary_mac = dr.format_mac(str(user_input.get(CONF_PRIMARY_GATEWAY) or ""))
            primary = entry_for_mac(self.hass, primary_mac) if primary_mac else None
            role = user_input.get(CONF_GATEWAY_ROLE, ROLE_SECONDARY)
            follower_options = {
                **options,
                CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
                CONF_GATEWAY_ROLE: role,
                CONF_PRIMARY_GATEWAY: primary_mac,
            }
            if role == ROLE_SECONDARY:
                follower_options[CONF_DELEGATED_WHOS] = sorted(
                    int(w)
                    for w in user_input.get(CONF_DELEGATED_WHOS, [])
                    if str(w).isdigit()
                )
            # A standalone gateway becomes the shared bus's primary.
            primary_options = dict(primary.options) if primary is not None else {}
            primary_options.update(
                {CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY}
            )
            primary_options.pop(CONF_PRIMARY_GATEWAY, None)
            primary_options.pop(CONF_DELEGATED_WHOS, None)

            errors = validate_shared_bus_topology(
                self.hass,
                pending,
                follower_options,
                target_primary_options=primary_options,
            )
            if not errors and primary is not None:
                if primary_options != dict(primary.options):
                    self.hass.config_entries.async_update_entry(
                        primary, options=primary_options
                    )
                from .repairs import async_delete_shared_bus_issue

                async_delete_shared_bus_issue(self.hass, data[CONF_MAC], primary_mac)
                return self.async_create_entry(  # type: ignore[attr-defined,no-any-return]
                    title=title, data=data, options=follower_options
                )

        if not primaries:  # the other gateway was removed while the form was open
            return self.async_create_entry(  # type: ignore[attr-defined,no-any-return]
                title=title, data=data, options=options
            )

        gw_options = [
            selector.SelectOptionDict(
                value=str(entry_mac(e)), label=f"{e.title} ({e.data.get(CONF_HOST)})"
            )
            for e in primaries
        ]
        suggested_primary = (
            (user_input or {}).get(CONF_PRIMARY_GATEWAY) or gw_options[0]["value"]
        )
        role, delegated = ROLE_STANDBY, set[int]()
        if (
            suggested_entry := entry_for_mac(
                self.hass, dr.format_mac(str(suggested_primary))
            )
        ) is not None:
            role, delegated = recommend_follower(suggested_entry, pending)

        return self.async_show_form(  # type: ignore[attr-defined,no-any-return]
            step_id="bus_topology",
            data_schema=flow_schema(
                {
                    Required(
                        CONF_BUS_TOPOLOGY,
                        default=(user_input or {}).get(
                            CONF_BUS_TOPOLOGY, TOPOLOGY_STANDALONE
                        ),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[TOPOLOGY_STANDALONE, TOPOLOGY_SHARED],
                            mode=selector.SelectSelectorMode.LIST,
                            translation_key=CONF_BUS_TOPOLOGY,
                        )
                    ),
                    Required(
                        CONF_PRIMARY_GATEWAY, default=suggested_primary
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=gw_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    Required(
                        CONF_GATEWAY_ROLE,
                        default=(user_input or {}).get(CONF_GATEWAY_ROLE, role),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[ROLE_SECONDARY, ROLE_STANDBY],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                            translation_key=CONF_GATEWAY_ROLE,
                        )
                    ),
                    vol.Optional(
                        CONF_DELEGATED_WHOS,
                        description={
                            "suggested_value": (user_input or {}).get(
                                CONF_DELEGATED_WHOS,
                                [str(w) for w in sorted(delegated)],
                            )
                        },
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                "1",
                                "2",
                                "4",
                                "5",
                                "9",
                                "15",
                                "16",
                                "18",
                                "22",
                                "25",
                            ],
                            multiple=True,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                            translation_key=CONF_DELEGATED_WHOS,
                        )
                    ),
                }
            ),
            description_placeholders={CONF_NAME: str(data.get(CONF_NAME) or "gateway")},
            errors=errors,
        )


class OptionsFlowTopologyMixin:
    """Shared-bus topology validation and schema builder for options flow."""

    hass: typing.Any
    config_entry: typing.Any
    options: dict[str, typing.Any] | None

    def _apply_topology(
        self, user_input: dict[str, typing.Any], errors: dict[str, str]
    ) -> None:
        """Validate the shared-bus settings (#453) and store them in the options."""
        top_errors = validate_shared_bus_topology(
            self.hass,
            self.config_entry,
            user_input,
            model_override=user_input.get(CONF_NAME),
        )
        if top_errors:
            errors.update(top_errors)
            return

        assert self.options is not None
        in_topo = user_input.get(
            CONF_BUS_TOPOLOGY,
            self.options.get(CONF_BUS_TOPOLOGY, TOPOLOGY_STANDALONE),
        )
        in_role = user_input.get(
            CONF_GATEWAY_ROLE, self.options.get(CONF_GATEWAY_ROLE, ROLE_PRIMARY)
        )
        in_pri = user_input.get(
            CONF_PRIMARY_GATEWAY, self.options.get(CONF_PRIMARY_GATEWAY)
        )
        shared = in_topo == TOPOLOGY_SHARED
        follower = shared and in_role in (ROLE_SECONDARY, ROLE_STANDBY)
        my_mac = entry_mac(self.config_entry)
        norm_pri = dr.format_mac(str(in_pri)) if in_pri else None

        self.options[CONF_BUS_TOPOLOGY] = (
            TOPOLOGY_SHARED if shared else TOPOLOGY_STANDALONE
        )
        self.options[CONF_GATEWAY_ROLE] = in_role if shared else ROLE_PRIMARY
        if follower:
            self.options[CONF_PRIMARY_GATEWAY] = norm_pri
        else:
            self.options.pop(CONF_PRIMARY_GATEWAY, None)
        if follower and in_role == ROLE_SECONDARY:
            delegated = [
                int(w)
                for w in user_input.get(CONF_DELEGATED_WHOS, [])
                if str(w).isdigit()
            ]
            self.options[CONF_DELEGATED_WHOS] = delegated
        else:
            self.options.pop(CONF_DELEGATED_WHOS, None)

        if follower and my_mac and norm_pri:
            from .repairs import async_delete_shared_bus_issue

            async_delete_shared_bus_issue(self.hass, my_mac, str(norm_pri))

    def _append_topology_schema(
        self, schema_dict: dict[typing.Any, typing.Any]
    ) -> None:
        """Append shared-bus topology fields when multiple gateways exist."""
        assert self.options is not None
        other_gateways = [
            e
            for e in self.hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != getattr(self.config_entry, "entry_id", None)
        ]
        if not other_gateways:
            return

        gw_options = [
            selector.SelectOptionDict(
                value=str(e.data.get(CONF_MAC) or e.unique_id),
                label=f"{e.title} ({e.data.get(CONF_HOST)})",
            )
            for e in other_gateways
        ]
        schema_dict[
            vol.Optional(
                CONF_BUS_TOPOLOGY,
                description={
                    "suggested_value": self.options.get(
                        CONF_BUS_TOPOLOGY, TOPOLOGY_STANDALONE
                    )
                },
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[TOPOLOGY_STANDALONE, TOPOLOGY_SHARED],
                mode=selector.SelectSelectorMode.DROPDOWN,
                translation_key=CONF_BUS_TOPOLOGY,
            )
        )
        suggested_role = self.options.get(CONF_GATEWAY_ROLE)
        suggested_whos = [str(w) for w in self.options.get(CONF_DELEGATED_WHOS, [])]
        selected_pri = self.options.get(CONF_PRIMARY_GATEWAY)
        if not selected_pri and gw_options:
            selected_pri = gw_options[0]["value"]
        if selected_pri and (suggested_role is None or not suggested_whos):
            from .topology import infer_shared_bus_topology

            pri_entry = entry_for_mac(self.hass, selected_pri)
            if pri_entry and self.config_entry:
                rec = infer_shared_bus_topology(pri_entry, self.config_entry)
                my_mac = entry_mac(self.config_entry)
                if suggested_role is None:
                    suggested_role = (
                        rec.role if rec.secondary_mac == my_mac else ROLE_PRIMARY
                    )
                if (
                    not suggested_whos
                    and rec.secondary_mac == my_mac
                    and rec.role == ROLE_SECONDARY
                ):
                    suggested_whos = [str(w) for w in sorted(rec.delegated_whos)]
                LOGGER.debug(
                    "Inferred shared-bus smart defaults for %s: role=%s, delegated_whos=%s (selected primary %s)",
                    my_mac,
                    suggested_role,
                    suggested_whos,
                    selected_pri,
                )

        if suggested_role is None:
            suggested_role = ROLE_PRIMARY

        schema_dict[
            vol.Optional(
                CONF_GATEWAY_ROLE,
                description={"suggested_value": suggested_role},
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[ROLE_PRIMARY, ROLE_SECONDARY, ROLE_STANDBY],
                mode=selector.SelectSelectorMode.DROPDOWN,
                translation_key=CONF_GATEWAY_ROLE,
            )
        )
        schema_dict[
            vol.Optional(
                CONF_PRIMARY_GATEWAY,
                description={"suggested_value": self.options.get(CONF_PRIMARY_GATEWAY)},
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=gw_options,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
        schema_dict[
            vol.Optional(
                CONF_DELEGATED_WHOS,
                description={"suggested_value": suggested_whos},
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=["1", "2", "4", "5", "9", "15", "16", "18", "22", "25"],
                multiple=True,
                mode=selector.SelectSelectorMode.DROPDOWN,
                translation_key=CONF_DELEGATED_WHOS,
            )
        )
