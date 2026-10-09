"""Validator for the MyHome configuration file."""
import re
import typing

from homeassistant.components.alarm_control_panel import (  # type: ignore[attr-defined]
    DOMAIN as ALARM_CONTROL_PANEL,
)
from homeassistant.components.binary_sensor import (  # type: ignore[attr-defined, unused-ignore]
    DOMAIN as BINARY_SENSOR,
)
from homeassistant.components.binary_sensor import (  # type: ignore[attr-defined, unused-ignore]
    BinarySensorDeviceClass,
)
from homeassistant.components.button import DOMAIN as BUTTON  # type: ignore
from homeassistant.components.climate import DOMAIN as CLIMATE  # type: ignore
from homeassistant.components.cover import (
    DOMAIN as COVER,
)
from homeassistant.components.cover import (
    CoverDeviceClass,
)
from homeassistant.components.light import DOMAIN as LIGHT  # type: ignore
from homeassistant.components.lock import DOMAIN as LOCK  # type: ignore
from homeassistant.components.sensor import (
    DOMAIN as SENSOR,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
)
from homeassistant.components.switch import (  # type: ignore
    DOMAIN as SWITCH,
)
from homeassistant.components.switch import (  # type: ignore[attr-defined, unused-ignore]
    SwitchDeviceClass,
)
from homeassistant.const import CONF_CODE, CONF_MAC, CONF_NAME
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import format_mac as ha_format_mac
from voluptuous import (
    All,
    Any,
    Boolean,
    Coerce,
    In,
    Invalid,
    Optional,
    Range,
    Required,
    Schema,
)

from .const import (
    BUS_ROUTING,
    CONF_ACCESS,
    CONF_ADVANCED_SHUTTER,
    CONF_ALLOWED_USERS,
    CONF_APPROVAL_TIMEOUT,
    CONF_APPROVERS,
    CONF_BUS_INTERFACE,
    CONF_CAMERA,
    CONF_CENTRAL,
    CONF_CLOSE_BLOCK_ENTITY,
    CONF_COLOR_TEMP,
    CONF_COOLING_SUPPORT,
    CONF_DEVICE_CLASS,
    CONF_DEVICE_MODEL,
    CONF_DIMMABLE,
    CONF_ENTITIES,
    CONF_ENTITY_NAME,
    CONF_FAN_SUPPORT,
    CONF_HEATING_SUPPORT,
    CONF_HS,
    CONF_ICON,
    CONF_ICON_ON,
    CONF_INVERTED,
    CONF_LOCK_FEATURES,
    CONF_MANUFACTURER,
    CONF_MEMBERS,
    CONF_MIN_CYCLE_TIME,
    CONF_PIN_CODE,
    CONF_PLATFORMS,
    CONF_PREWARN_LIGHT,
    CONF_PREWARN_SECONDS,
    CONF_PULSE_DURATION,
    CONF_REMOTE_CLOSE,
    CONF_RGB,
    CONF_SAFETY_CHECK_DAYS,
    CONF_SAFETY_DEVICES_VERIFIED,
    CONF_STANDALONE,
    CONF_STATE_SENSOR,
    CONF_TRAVEL_TIME,
    CONF_TYPE,
    CONF_WATCHDOG_MARGIN,
    CONF_WHERE,
    CONF_WHO,
    CONF_ZONE,
    TYPE_IMPULSE_RELAY,
)


def format_mac(address: object) -> str:
    if isinstance(address, str):
        mac = "".join(address.split())
        for sep in (":", "-", "."):
            if sep in mac:
                parts = mac.split(sep)
                if len(parts) == 6 and all(1 <= len(p) <= 2 for p in parts):
                    mac = "".join(p.zfill(2) for p in parts)
                break
        mac = re.sub("[.:-]", "", mac).upper()
    else:
        mac = ""
    if len(mac) != 12 or not mac.isalnum() or re.search("[G-Z]", mac) is not None:
        return None  # type: ignore
    return ha_format_mac(mac)


class MacAddress(object):
    def __init__(self, msg=None):  # type: ignore
        self.msg = msg

    def __call__(self, v):  # type: ignore
        v = format_mac(v)
        if v is None:
            raise Invalid("Invalid MAC address")
        return format_mac(v)

    def __repr__(self):  # type: ignore
        return "MacAddress(%s, msg=%r)" % ("String", self.msg)


class General(object):
    def __init__(self, msg=None):  # type: ignore
        self.msg = msg

    def __call__(self, v):  # type: ignore
        if isinstance(v, str) and v == "0":
            return v
        else:
            raise Invalid(f"Invalid General WHERE {v}, it must be 0.")

    def __repr__(self):  # type: ignore
        return "Where(%s, msg=%r)" % ("String", self.msg)


class Area(object):
    def __init__(self, msg=None):  # type: ignore
        self.msg = msg

    def __call__(self, v):  # type: ignore
        if isinstance(v, str) and v in ["00", "1", "2", "3", "4", "5", "6", "7", "8", "9", "100"]:
            return v
        else:
            raise Invalid(f"Invalid Area WHERE {v}, it must be a string in [00, 1-9, 100].")

    def __repr__(self):  # type: ignore
        return "Where(%s, msg=%r)" % ("String", self.msg)


class Group(object):
    def __init__(self, msg=None):  # type: ignore
        self.msg = msg

    def __call__(self, v):  # type: ignore
        if isinstance(v, str) and v.startswith("#") and v[1:].isdigit() and int(v[1:]) >= 1 and int(v[1:]) <= 255:
            return f"#{int(v[1:])}"
        else:
            raise Invalid(f"Invalid Group WHERE {v}, it must be a string like '#[1-255]'.")

    def __repr__(self):  # type: ignore
        return "Where(%s, msg=%r)" % ("String", self.msg)


class PointToPoint:
    def __init__(self, msg: str | None = None) -> None:
        self.msg = msg

    def __call__(self, v: typing.Any) -> typing.Any:
        if isinstance(v, str) and v.isdigit():
            _length = len(v)
            if _length == 2 or _length == 4:
                _a = v[0 : _length // 2]
                _pl = v[_length // 2 :]
                if int(_a) >= 0 and int(_a) <= 10 and int(_pl) >= 0 and int(_pl) <= 15:
                    return f"{_a}{_pl}"
                else:
                    raise Invalid(f"Invalid WHERE {v}, A must be [0-10] and PL must be [0-15].")
            else:
                raise Invalid(f"Invalid WHERE {v} length, it must be a string of 2 or 4 digits.")
        else:
            raise Invalid(f"Invalid WHERE {v}, it must be a string of 2 or 4 digits.")

    def __repr__(self):  # type: ignore
        return "Where(%s, msg=%r)" % ("String", self.msg)


class SpecialWhere(object):
    def __init__(self, msg=None):  # type: ignore
        self.msg = msg

    def __call__(self, v):  # type: ignore
        if isinstance(v, str) and v.isdigit():
            return v
        else:
            raise Invalid(f"Invalid WHERE {v}, it must be a string of digits.")

    def __repr__(self):  # type: ignore
        return "Where(%s, msg=%r)" % ("String", self.msg)


class BusInterface(object):
    def __init__(self, msg=None):  # type: ignore
        self.msg = msg

    def __call__(self, v):  # type: ignore
        if v is None:
            return v
        # ``interface: 3`` and an unquoted ``interface: 03`` both reach here as "3"
        if isinstance(v, str) and v.isdigit() and 1 <= len(v) <= 2:
            if int(v) > 15:
                raise Invalid(f"Invalid Bus Interface number {v}, it must be between 00 and 15.")
            return v.zfill(2)
        raise Invalid(f"Invalid Bus Interface number {v}, it must be a string of 2 digits.")

    def __repr__(self):  # type: ignore
        return "BusInterface(%s, msg=%r)" % ("String", self.msg)


class MyHomeConfigSchema(Schema):
    def __call__(self, data):  # type: ignore
        data = super().__call__(data)  # type: ignore
        _rekeyed_data = {}  # type: ignore
        for gateway in data:
            gateway_mac = data[gateway].get(CONF_MAC) or format_mac(gateway) or gateway
            _rekeyed_data[gateway_mac] = {}
            _rekeyed_data[gateway_mac][CONF_PLATFORMS] = {}
            for platform in data[gateway]:
                if platform != CONF_MAC:
                    _rekeyed_data[gateway_mac][CONF_PLATFORMS][platform] = data[gateway][platform]

            if (
                (LIGHT in _rekeyed_data[gateway_mac][CONF_PLATFORMS])
                or (SWITCH in _rekeyed_data[gateway_mac][CONF_PLATFORMS])
                or (COVER in _rekeyed_data[gateway_mac][CONF_PLATFORMS])
            ):
                _rekeyed_data[gateway_mac][CONF_PLATFORMS][BUTTON] = {}
                if LIGHT in _rekeyed_data[gateway_mac][CONF_PLATFORMS]:
                    for key, value in _rekeyed_data[gateway_mac][CONF_PLATFORMS][LIGHT].items():
                        if not value[CONF_WHERE].startswith("#"):
                            _rekeyed_data[gateway_mac][CONF_PLATFORMS][BUTTON][key] = value
                if SWITCH in _rekeyed_data[gateway_mac][CONF_PLATFORMS]:
                    for key, value in _rekeyed_data[gateway_mac][CONF_PLATFORMS][SWITCH].items():
                        if not value[CONF_WHERE].startswith("#"):
                            _rekeyed_data[gateway_mac][CONF_PLATFORMS][BUTTON][key] = value
                if COVER in _rekeyed_data[gateway_mac][CONF_PLATFORMS]:
                    for key, value in _rekeyed_data[gateway_mac][CONF_PLATFORMS][COVER].items():
                        if not value[CONF_WHERE].startswith("#"):
                            _rekeyed_data[gateway_mac][CONF_PLATFORMS][BUTTON][key] = value

        return _rekeyed_data


class MyHomeDeviceSchema(Schema):
    def __call__(self, data):  # type: ignore
        data = super().__call__(data)  # type: ignore
        _rekeyed_data = {}

        for device in data:
            data[device][CONF_ENTITIES] = {}
            interface = data[device].get(CONF_BUS_INTERFACE)
            routing = f"{BUS_ROUTING}{interface}" if interface is not None else ""
            if CONF_WHERE in data[device]:
                _rekeyed_data[f"{data[device][CONF_WHO]}-{data[device][CONF_WHERE]}{routing}"] = data[device]
            elif CONF_ZONE in data[device]:
                _new_key = f"{data[device][CONF_WHO]}-{data[device][CONF_ZONE]}{routing}"
                data[device][CONF_ZONE] = f"#0#{data[device][CONF_ZONE]}" if data[device][CONF_CENTRAL] and data[device][CONF_ZONE] != "#0" else data[device][CONF_ZONE]
                data[device][CONF_NAME] = (
                    data[device][CONF_NAME] if CONF_NAME in data[device] else "Central unit" if data[device][CONF_ZONE].startswith("#0") else f"Zone {data[device][CONF_ZONE]}"
                )
                _rekeyed_data[_new_key] = data[device]
                _rekeyed_data[str(device)] = data[device]
                clean_zone = str(data[device][CONF_ZONE]).split("#")[-1]
                _rekeyed_data[f"{clean_zone}{routing}"] = data[device]
                _rekeyed_data[f"{data[device][CONF_ZONE]}{routing}"] = data[device]
                if not routing:
                    _rekeyed_data[f"zone_{clean_zone}"] = data[device]
            if CONF_DEVICE_MODEL not in data[device]:
                data[device][CONF_DEVICE_MODEL] = None
            if CONF_ICON not in data[device]:
                data[device][CONF_ICON] = None
            if CONF_ICON_ON not in data[device]:
                data[device][CONF_ICON_ON] = None
            if CONF_ENTITY_NAME not in data[device]:
                data[device][CONF_ENTITY_NAME] = None
            if "advanced_shutter" in data[device] and data[device]["advanced_shutter"]:
                data[device][CONF_ADVANCED_SHUTTER] = True
            if "device_class" in data[device] and CONF_DEVICE_CLASS not in data[device]:
                data[device][CONF_DEVICE_CLASS] = data[device]["device_class"]
            if CONF_DEVICE_CLASS not in data[device]:
                data[device][CONF_DEVICE_CLASS] = SwitchDeviceClass.SWITCH

        return _rekeyed_data


class MyHomeSensorSchema(Schema):
    def __call__(self, data):  # type: ignore
        data = super().__call__(data)  # type: ignore
        _rekeyed_data = {}

        for device in data:
            data[device][CONF_ENTITIES] = {}
            if CONF_DEVICE_CLASS in data[device]:
                if data[device][CONF_DEVICE_CLASS] in [
                    SensorDeviceClass.POWER,
                    SensorDeviceClass.ENERGY,
                ]:
                    if CONF_WHO not in data[device]:
                        data[device][CONF_WHO] = "18"
                    elif data[device][CONF_WHO] != "18":
                        raise Invalid("invalid sensor class for selected who")
                    data[device][CONF_ENTITIES][f"daily-{SensorDeviceClass.ENERGY}"] = {}
                    data[device][CONF_ENTITIES][f"monthly-{SensorDeviceClass.ENERGY}"] = {}
                    data[device][CONF_ENTITIES][f"total-{SensorDeviceClass.ENERGY}"] = {}
                    if data[device][CONF_DEVICE_CLASS] in [SensorDeviceClass.POWER]:
                        data[device][CONF_ENTITIES][f"{SensorDeviceClass.POWER}"] = {}
                elif data[device][CONF_DEVICE_CLASS] in [SensorDeviceClass.TEMPERATURE]:
                    if CONF_WHO not in data[device]:
                        data[device][CONF_WHO] = "4"
                    elif data[device][CONF_WHO] != "4":
                        raise Invalid("invalid sensor class for selected who")
                elif data[device][CONF_DEVICE_CLASS] in [SensorDeviceClass.ILLUMINANCE]:
                    if CONF_WHO not in data[device]:
                        data[device][CONF_WHO] = "1"
                    elif data[device][CONF_WHO] != "1":
                        raise Invalid("invalid sensor class for selected who")
            if CONF_WHERE in data[device]:
                interface = data[device].get(CONF_BUS_INTERFACE)
                routing = f"{BUS_ROUTING}{interface}" if interface is not None else ""
                _rekeyed_data[f"{data[device][CONF_WHO]}-{data[device][CONF_WHERE]}{routing}"] = data[device]
            if CONF_DEVICE_MODEL not in data[device]:
                data[device][CONF_DEVICE_MODEL] = None

        return _rekeyed_data


light_schema = MyHomeDeviceSchema(
    {
        Required(str): {
            Optional(CONF_WHO, default="1"): "1",
            Required(CONF_WHERE): All(
                Coerce(str), Any(General(), Area(), Group(), PointToPoint(), msg="Invalid <WHERE>, expecting a valid General, Area, Group or Point-to-Point <WHERE>")  # type: ignore
            ),
            Optional(CONF_BUS_INTERFACE): All(Coerce(str), BusInterface()),  # type: ignore
            Required(CONF_NAME): str,
            Optional(CONF_ENTITY_NAME): str,
            Optional(CONF_ICON): str,
            Optional(CONF_ICON_ON): str,
            Optional(CONF_DIMMABLE, default=False): Boolean(),
            # DALI DT8 capabilities (issue #273 / #288).  Without lock_features
            # these are only the starting point: the light still learns
            # dimming, tunable white and HSV colour from the bus.  With
            # lock_features the three flags are the whole truth.
            Optional(CONF_COLOR_TEMP): Boolean(),
            Optional(CONF_RGB): Boolean(),
            Optional(CONF_HS): Boolean(),
            Optional(CONF_LOCK_FEATURES): Boolean(),
            Optional(CONF_MANUFACTURER, default="BTicino S.p.A."): str,
            Optional(CONF_DEVICE_MODEL): Coerce(str),
            Optional(CONF_MEMBERS): [All(Coerce(str), PointToPoint())],
        }
    }
)

def _validate_light_members(data: dict[str, typing.Any]) -> dict[str, typing.Any]:
    for device, cfg in data.items():
        if CONF_MEMBERS in cfg:
            where = cfg.get(CONF_WHERE)
            if not where or not str(where).startswith("#"):
                raise Invalid("Members can only be defined on a group light (where must start with #)")
    return data

light_schema = All(light_schema, _validate_light_members)  # type: ignore[assignment]


switch_schema = MyHomeDeviceSchema(
    {
        Required(str): {
            Optional(CONF_WHO, default="1"): "1",
            Required(CONF_WHERE): All(
                Coerce(str), Any(General(), Area(), Group(), PointToPoint(), msg="Invalid <WHERE>, expecting a valid General, Area, Group or Point-to-Point <WHERE>")  # type: ignore
            ),
            Optional(CONF_BUS_INTERFACE): All(Coerce(str), BusInterface()),  # type: ignore
            Required(CONF_NAME): str,
            Optional(CONF_ENTITY_NAME): str,
            Optional(CONF_ICON): str,
            Optional(CONF_ICON_ON): str,
            Optional(CONF_DEVICE_CLASS): In(
                [
                    SwitchDeviceClass.OUTLET,
                    SwitchDeviceClass.SWITCH,
                ]
            ),
            Optional("device_class"): In(
                [
                    SwitchDeviceClass.OUTLET,
                    SwitchDeviceClass.SWITCH,
                ]
            ),
            Optional(CONF_MANUFACTURER, default="BTicino S.p.A."): str,
            Optional(CONF_DEVICE_MODEL): Coerce(str),
        }
    }
)

def _validate_access(access: dict) -> dict:
    """Cross-field rules of an impulse cover's access policy."""
    allowed = set(access[CONF_ALLOWED_USERS])
    approvers = access[CONF_APPROVERS]
    missing = sorted(allowed - set(approvers))
    if missing:
        raise Invalid(f"every allowed user needs an approver (notify service); missing for {missing}")
    extra = sorted(set(approvers) - allowed)
    if extra:
        raise Invalid(f"approvers lists users that are not in allowed_users: {extra}")
    for user, service in approvers.items():
        if not service.startswith("notify.") or service == "notify.":
            raise Invalid(f"approver of {user} must be a notify service such as notify.mobile_app_phone")
    if access[CONF_REMOTE_CLOSE] == "with_camera" and CONF_CAMERA not in access:
        raise Invalid("remote_close: with_camera needs a camera entity")
    return access


access_schema = All(
    Schema(
        {
            Optional(CONF_ALLOWED_USERS, default=[]): [Coerce(str)],
            Optional(CONF_APPROVERS, default={}): {Coerce(str): Coerce(str)},
            Optional(CONF_REMOTE_CLOSE, default="never"): In(["never", "at_home", "with_camera"]),
            Optional(CONF_SAFETY_DEVICES_VERIFIED): cv.date,
            Optional(CONF_SAFETY_CHECK_DAYS, default=31): All(Coerce(int), Range(min=1, max=366)),
            Optional(CONF_CAMERA): cv.entity_id,
            Optional(CONF_PREWARN_LIGHT): cv.entity_id,
            Optional(CONF_PREWARN_SECONDS, default=5): All(Coerce(float), Range(min=3, max=30)),
            Optional(CONF_CLOSE_BLOCK_ENTITY): cv.entity_id,
            Optional(CONF_WATCHDOG_MARGIN, default=10): All(Coerce(float), Range(min=1, max=120)),
            Optional(CONF_APPROVAL_TIMEOUT, default=60): All(Coerce(float), Range(min=15, max=300)),
        }
    ),
    _validate_access,
)


cover_schema = MyHomeDeviceSchema(
    {
        Required(str): {
            Optional(CONF_WHO, default="2"): In(["1", "2"]),
            Required(CONF_WHERE): All(
                Coerce(str), Any(General(), Area(), Group(), PointToPoint(), msg="Invalid <WHERE>, expecting a valid General, Area, Group or Point-to-Point <WHERE>")  # type: ignore
            ),
            Optional(CONF_BUS_INTERFACE): All(Coerce(str), BusInterface()),  # type: ignore
            Required(CONF_NAME): str,
            Optional(CONF_ENTITY_NAME): str,
            Optional(CONF_DEVICE_CLASS): In(
                [
                    CoverDeviceClass.AWNING,
                    CoverDeviceClass.BLIND,
                    CoverDeviceClass.CURTAIN,
                    CoverDeviceClass.DAMPER,
                    CoverDeviceClass.DOOR,
                    CoverDeviceClass.GARAGE,
                    CoverDeviceClass.GATE,
                    CoverDeviceClass.SHADE,
                    CoverDeviceClass.SHUTTER,
                    CoverDeviceClass.WINDOW,
                ]
            ),
            Optional("device_class"): In(
                [
                    CoverDeviceClass.AWNING,
                    CoverDeviceClass.BLIND,
                    CoverDeviceClass.CURTAIN,
                    CoverDeviceClass.DAMPER,
                    CoverDeviceClass.DOOR,
                    CoverDeviceClass.GARAGE,
                    CoverDeviceClass.GATE,
                    CoverDeviceClass.SHADE,
                    CoverDeviceClass.SHUTTER,
                    CoverDeviceClass.WINDOW,
                ]
            ),
            Optional(CONF_TYPE): In([TYPE_IMPULSE_RELAY, "shutter", "standard"]),
            Optional("type"): In([TYPE_IMPULSE_RELAY, "shutter", "standard"]),
            Optional(CONF_PIN_CODE): Coerce(str),
            Optional("pin_code"): Coerce(str),
            Optional(CONF_STATE_SENSOR): cv.entity_id,
            Optional("state_sensor"): cv.entity_id,
            Optional(CONF_PULSE_DURATION, default=0.5): All(Coerce(float), Range(min=0.2, max=2.0)),
            Optional("pulse_duration", default=0.5): All(Coerce(float), Range(min=0.2, max=2.0)),
            Optional(CONF_MIN_CYCLE_TIME, default=5.0): All(Coerce(float), Range(min=1.0, max=120.0)),
            Optional("min_cycle_time", default=5.0): All(Coerce(float), Range(min=1.0, max=120.0)),
            Optional(CONF_ACCESS): access_schema,
            Optional(CONF_ADVANCED_SHUTTER, default=False): Boolean(),
            Optional("advanced_shutter", default=False): Boolean(),
            Optional(CONF_TRAVEL_TIME, default=25): Coerce(int),
            Optional(CONF_MANUFACTURER, default="BTicino S.p.A."): str,
            Optional(CONF_DEVICE_MODEL): Coerce(str),
            Optional(CONF_MEMBERS): [All(Coerce(str), PointToPoint())],
        }
    }
)


def _validate_cover_members(data: dict[str, typing.Any]) -> dict[str, typing.Any]:
    # A general or area cover's members follow from the addresses (issue #433);
    # a group's membership is programmed in the actuators, never seen on the bus.
    for device, cfg in data.items():
        if CONF_MEMBERS in cfg:
            where = cfg.get(CONF_WHERE)
            if not where or not str(where).startswith("#"):
                raise Invalid("Members can only be defined on a group cover (where must start with #)")
    return data


def _validate_impulse_covers(data: dict) -> dict:
    """An impulse cover pulses exactly one relay; access settings only apply to impulse covers."""
    for device, cfg in data.items():
        is_impulse = str(cfg.get(CONF_WHO)) == "1"
        if is_impulse:
            try:
                PointToPoint()(str(cfg[CONF_WHERE]))  # type: ignore
            except Invalid as err:
                raise Invalid(
                    f"{device}: an impulse cover (who: 1) needs a point-to-point <WHERE>; a general, area or "
                    "group address would pulse several motors at once"
                ) from err
        elif CONF_ACCESS in cfg or CONF_PIN_CODE in cfg or CONF_STATE_SENSOR in cfg:
            raise Invalid(f"{device}: access, pin_code and state_sensor only apply to impulse covers (who: 1)")
    return data


cover_schema = All(cover_schema, _validate_cover_members, _validate_impulse_covers)  # type: ignore[assignment]

binary_sensor_schema = MyHomeDeviceSchema(
    {
        Required(str): {
            Optional(CONF_WHO, default="25"): In(["1", "9", "25"]),
            Required(CONF_WHERE): All(Coerce(str), SpecialWhere()),  # type: ignore
            Required(CONF_NAME): str,
            Optional(CONF_ENTITY_NAME): str,
            Optional(CONF_INVERTED, default=False): Boolean(),
            Optional(CONF_DEVICE_CLASS): In(
                [
                    BinarySensorDeviceClass.BATTERY,
                    BinarySensorDeviceClass.BATTERY_CHARGING,
                    BinarySensorDeviceClass.COLD,
                    BinarySensorDeviceClass.CONNECTIVITY,
                    BinarySensorDeviceClass.DOOR,
                    BinarySensorDeviceClass.GARAGE_DOOR,
                    BinarySensorDeviceClass.GAS,
                    BinarySensorDeviceClass.HEAT,
                    BinarySensorDeviceClass.LIGHT,
                    BinarySensorDeviceClass.LOCK,
                    BinarySensorDeviceClass.MOISTURE,
                    BinarySensorDeviceClass.MOTION,
                    BinarySensorDeviceClass.MOVING,
                    BinarySensorDeviceClass.OCCUPANCY,
                    BinarySensorDeviceClass.OPENING,
                    BinarySensorDeviceClass.PLUG,
                    BinarySensorDeviceClass.POWER,
                    BinarySensorDeviceClass.PRESENCE,
                    BinarySensorDeviceClass.PROBLEM,
                    BinarySensorDeviceClass.SAFETY,
                    BinarySensorDeviceClass.SMOKE,
                    BinarySensorDeviceClass.SOUND,
                    BinarySensorDeviceClass.VIBRATION,
                    BinarySensorDeviceClass.WINDOW,
                ]
            ),
            Optional("device_class"): In(
                [
                    BinarySensorDeviceClass.BATTERY,
                    BinarySensorDeviceClass.BATTERY_CHARGING,
                    BinarySensorDeviceClass.COLD,
                    BinarySensorDeviceClass.CONNECTIVITY,
                    BinarySensorDeviceClass.DOOR,
                    BinarySensorDeviceClass.GARAGE_DOOR,
                    BinarySensorDeviceClass.GAS,
                    BinarySensorDeviceClass.HEAT,
                    BinarySensorDeviceClass.LIGHT,
                    BinarySensorDeviceClass.LOCK,
                    BinarySensorDeviceClass.MOISTURE,
                    BinarySensorDeviceClass.MOTION,
                    BinarySensorDeviceClass.MOVING,
                    BinarySensorDeviceClass.OCCUPANCY,
                    BinarySensorDeviceClass.OPENING,
                    BinarySensorDeviceClass.PLUG,
                    BinarySensorDeviceClass.POWER,
                    BinarySensorDeviceClass.PRESENCE,
                    BinarySensorDeviceClass.PROBLEM,
                    BinarySensorDeviceClass.SAFETY,
                    BinarySensorDeviceClass.SMOKE,
                    BinarySensorDeviceClass.SOUND,
                    BinarySensorDeviceClass.VIBRATION,
                    BinarySensorDeviceClass.WINDOW,
                ]
            ),
            Optional(CONF_MANUFACTURER, default="BTicino S.p.A."): str,
            Optional(CONF_DEVICE_MODEL): Coerce(str),
        }
    }
)

sensor_schema = MyHomeSensorSchema(
    {
        Required(str): {
            Optional(CONF_WHO): In(["1", "4", "18"]),
            Required(CONF_WHERE): All(Coerce(str), SpecialWhere()),  # type: ignore
            Required(CONF_NAME): str,
            Required(CONF_DEVICE_CLASS): In(
                [
                    SensorDeviceClass.TEMPERATURE,
                    SensorDeviceClass.POWER,
                    SensorDeviceClass.ENERGY,
                    SensorDeviceClass.ILLUMINANCE,
                ]
            ),
            Optional(CONF_MANUFACTURER, default="BTicino S.p.A."): str,
            Optional(CONF_DEVICE_MODEL): Coerce(str),
        }
    }
)

climate_schema = MyHomeDeviceSchema(
    {
        Required(str): {
            Optional(CONF_WHO, default="4"): "4",
            Optional(CONF_ZONE, default="#0"): Coerce(str),
            Optional(CONF_BUS_INTERFACE): All(Coerce(str), BusInterface()),  # type: ignore
            Optional(CONF_NAME): str,
            Optional(CONF_HEATING_SUPPORT, default=True): Boolean(),
            Optional(CONF_COOLING_SUPPORT, default=False): Boolean(),
            Optional(CONF_FAN_SUPPORT, default=False): Boolean(),
            Optional(CONF_STANDALONE, default=False): Boolean(),
            Optional(CONF_CENTRAL, default=False): Boolean(),
            Optional(CONF_MANUFACTURER, default="BTicino S.p.A."): str,
            Optional(CONF_DEVICE_MODEL): Coerce(str),
        }
    }
)

alarm_control_panel_schema = MyHomeDeviceSchema(
    {
        Required(str): {
            Optional(CONF_WHO, default="5"): "5",
            Required(CONF_WHERE): All(Coerce(str), Any(General(), Area(), Group(), PointToPoint(), SpecialWhere())),  # type: ignore
            Required(CONF_NAME): str,
            Optional(CONF_ENTITY_NAME): str,
            Optional(CONF_MANUFACTURER, default="BTicino S.p.A."): str,
            Optional(CONF_DEVICE_MODEL, default="F4201"): Coerce(str),
        }
    }
)

lock_schema = MyHomeDeviceSchema(
    {
        Required(str): {
            Optional(CONF_WHO, default="6"): In(["1", "6"]),
            Required(CONF_WHERE): All(Coerce(str), Any(General(), Area(), Group(), PointToPoint(), SpecialWhere())),  # type: ignore
            Optional(CONF_BUS_INTERFACE): BusInterface(),
            Required(CONF_NAME): str,
            Optional(CONF_ENTITY_NAME): str,
            Optional(CONF_CODE): Coerce(str),
            Optional("code"): Coerce(str),
            Optional(CONF_PULSE_DURATION, default=0.5): Coerce(float),
            Optional("pulse_duration", default=0.5): Coerce(float),
            Optional(CONF_MANUFACTURER, default="BTicino S.p.A."): str,
            Optional(CONF_DEVICE_MODEL, default="Door Entry Lock"): Coerce(str),
        }
    }
)

# The device schemas are Schema subclasses whose overridden __call__ performs
# post-processing (rekeying to "who-where" and injecting default keys). Nested
# schema instances are not guaranteed to be invoked through __call__ by the
# validation engine (HA Core 2026.9 replaced voluptuous with probatio, whose
# compatibility shim compiles nested Schema declarations directly), so wrap
# them in plain callables to force the subclass __call__ to run.
gateway_schema = Schema(
    {
        Optional(CONF_MAC): MacAddress(),  # type: ignore
        Optional(LIGHT): lambda v: light_schema(v),
        Optional(SWITCH): lambda v: switch_schema(v),
        Optional(COVER): lambda v: cover_schema(v),
        Optional(BINARY_SENSOR): lambda v: binary_sensor_schema(v),
        Optional(SENSOR): lambda v: sensor_schema(v),
        Optional(CLIMATE): lambda v: climate_schema(v),
        Optional(ALARM_CONTROL_PANEL): lambda v: alarm_control_panel_schema(v),
        Optional(LOCK): lambda v: lock_schema(v),
    }
)

config_schema = MyHomeConfigSchema({Required(str): gateway_schema})
