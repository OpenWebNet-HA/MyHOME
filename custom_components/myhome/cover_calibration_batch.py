"""Explicit sequential automatic selection with one reviewed atomic profile save."""
import copy
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant.components.websocket_api.decorators import (
    async_response,
    require_admin,
    websocket_command,
)
from homeassistant.helpers import entity_registry as er

from . import cover_profiles as profiles
from .const import DOMAIN
from .cover_calibration import ready_cover, send_error
from .cover_calibration_automatic import SETTLE_SECONDS, AutomaticCalibrationSession
from .cover_profile_provenance import PROVENANCE

WS_TARGETS = "myhome/cover_calibration/targets"
WS_BATCH_START = "myhome/cover_calibration/batch_start"
MAX_BATCH = 20
SELECTION = vol.All([str], vol.Length(min=1, max=MAX_BATCH))


def gateway(hass: Any, entry_id: str) -> Any:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise profiles.ProfileError("target_not_found")
    return entry


async def read_targets(hass: Any, entry_id: str) -> Any:
    entry = gateway(hass, entry_id)
    store = profiles.get_store(hass, entry_id)
    async with store.lock:
        await store.load()
        if gateway(hass, entry_id) is not entry:
            raise profiles.ProfileError("target_not_found")
        targets = []
        for record in er.async_entries_for_config_entry(er.async_get(hass), entry_id):
            if record.domain != "cover" or record.platform != DOMAIN:
                continue
            reason = None
            try:
                ready_cover(hass, store, entry_id, record.entity_id)
            except profiles.ProfileError as error:
                reason = str(error)
            targets.append({"entity_id": record.entity_id,
                            "name": record.name or record.original_name or record.entity_id,
                            "reason": reason})
        return {"entry_id": entry_id, "revision": store.data["revision"], "max_batch": MAX_BATCH,
                "targets": sorted(targets, key=lambda row: row["entity_id"])}


class BatchCalibrationSession(AutomaticCalibrationSession):
    """One owner and lease throughout every selected cover and final review."""

    def __init__(self, hass: Any, store: Any, entry: Any, covers: Any, connection: Any, subscription_id: Any) -> None:
        super().__init__(hass, store, entry, covers[0], connection, subscription_id)
        self.covers = covers
        self.cover_index = 0
        self.results: list[dict[str, Any]] = []

    def view(self) -> Any:
        def label(cover: Any) -> Any:
            record = er.async_get(self.hass).async_get(cover.entity_id)
            return (record.name or record.original_name or record.entity_id) if record else cover.entity_id
        return {**super().view(), "batch": True, "cover_index": self.cover_index,
                "targets": [{"entity_id": cover.entity_id, "name": label(cover)} for cover in self.covers],
                "results": [{"index": index, "entity_id": self.covers[index].entity_id,
                             "values": dict(result["values"])} for index, result in enumerate(self.results)]}

    def finish_measurement(self) -> None:
        self.results.append({"values": dict(self.values), "provenance": copy.deepcopy(self.provenance)})
        if self.cover_index == len(self.covers) - 1:
            self.phase = "review"
        else:
            self.phase = "between_covers"
            self.settle = self.hass.loop.call_later(SETTLE_SECONDS, self.next_cover)

    def next_cover(self) -> None:
        self.settle = None
        if self.phase != "between_covers" or self.store.calibration is not self:
            return
        following = self.covers[self.cover_index + 1]
        try:
            if ready_cover(self.hass, self.store, self.entry_id, following.entity_id) is not following:
                raise profiles.ProfileError("cover_unavailable")
            self.cover._calibration = None
            self.cover = following
            self.cover._calibration = self
            self.cover_index += 1
            self.run_index = 0
            self.values.clear()
            self.provenance.clear()
            self.queue_move("open")
        except profiles.ProfileError as error:
            self.interrupt(str(error))

    def interrupt(self, reason: str, send_stop: Any=True) -> None:
        if self.active and self.phase != "saving":
            self.results.clear()
        super().interrupt(reason, send_stop)

    async def save_profiles(self, msg: dict[str, Any]) -> Any:
        names = msg.get("names", [])
        if len(names) != len(self.covers) or len(self.results) != len(self.covers):
            raise profiles.ProfileError("invalid_profile")
        async with self.store.lock:
            if self.store.calibration is not self or not self.active:
                raise profiles.ProfileError("calibration_expired")
            if self.revision != self.store.data["revision"]:
                raise profiles.ProfileError("revision_conflict")
            if len(self.store.data["profiles"]) + len(self.covers) > profiles.MAX_PROFILES:
                raise profiles.ProfileError("profile_limit")
            data = copy.deepcopy(self.store.data)
            for cover, result, name in zip(self.covers, self.results, names, strict=True):
                if ready_cover(self.hass, self.store, self.entry_id, cover.entity_id) is not cover:
                    raise profiles.ProfileError("cover_unavailable")
                profile = profiles.PROFILE({"name": name, **result["values"]})
                profile["provenance"] = copy.deepcopy(PROVENANCE(result["provenance"]))
                profile_id = uuid4().hex
                data["profiles"][profile_id] = profile
                data["assignments"][cover.unique_id] = profile_id
                data["covers"].pop(cover.unique_id, None)
            await profiles.commit_profiles(self.hass, self.store, self.entry_id, data,
                                           [cover.unique_id for cover in self.covers])
        return data["revision"]


async def begin_batch(hass: Any, connection: Any, msg: dict[str, Any]) -> Any:
    entity_ids = SELECTION(msg["entity_ids"])
    if len(set(entity_ids)) != len(entity_ids):
        raise profiles.ProfileError("invalid_selection")
    entry = gateway(hass, msg["entry_id"])
    for entity_id in entity_ids:
        profiles.target(hass, entry.entry_id, entity_id)
    store = profiles.get_store(hass, entry.entry_id)
    async with store.lock:
        await store.load()
        if gateway(hass, entry.entry_id) is not entry:
            raise profiles.ProfileError("target_not_found")
        if store.calibration is not None:
            raise profiles.ProfileError("calibration_busy")
        if msg["revision"] != store.data["revision"]:
            raise profiles.ProfileError("revision_conflict")
        if len(store.data["profiles"]) + len(entity_ids) > profiles.MAX_PROFILES:
            raise profiles.ProfileError("profile_limit")
        covers = [ready_cover(hass, store, entry.entry_id, entity_id) for entity_id in entity_ids]
        session = BatchCalibrationSession(hass, store, entry, covers, connection, msg["id"])
        store.calibration = covers[0]._calibration = session
        connection.subscriptions[msg["id"]] = session.close
        return session


@websocket_command({vol.Required("type"): WS_TARGETS, vol.Required("entry_id"): str})
@require_admin
@async_response
async def ws_targets(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    await profiles.respond(hass, connection, msg, read_targets(hass, msg["entry_id"]))


@websocket_command({
    vol.Required("type"): WS_BATCH_START, vol.Required("entry_id"): str,
    vol.Required("entity_ids"): SELECTION, vol.Required("revision"): vol.All(int, vol.Range(min=0)),
})
@require_admin
@async_response
async def ws_batch_start(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    try:
        session = await begin_batch(hass, connection, msg)
    except (profiles.ProfileError, vol.Invalid, OSError) as error:
        send_error(connection, msg, error)
        return
    connection.send_result(msg["id"])
    session.emit()
