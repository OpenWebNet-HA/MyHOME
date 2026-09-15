"""Validator for the MyHome configuration file."""
import re

from homeassistant.components.alarm_control_panel import DOMAIN as ALARM_CONTROL_PANEL
from homeassistant.components.binary_sensor import (
    DOMAIN as BINARY_SENSOR,
)
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
)
from homeassistant.components.button import DOMAIN as BUTTON
from homeassistant.components.climate import DOMAIN as CLIMATE
from homeassistant.components.cover import DOMAIN as COVER
from homeassistant.components.light import DOMAIN as LIGHT
from homeassistant.components.sensor import (
    DOMAIN as SENSOR,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
)
from homeassistant.components.switch import (
    DOMAIN as SWITCH,
)
from homeassistant.components.switch import (
    SwitchDeviceClass,
)
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.helpers.device_registry import format_mac as ha_format_mac
from voluptuous import (
    All,
    Any,
    Boolean,
    Coerce,
    In,
    Invalid,
    Optional,
    Required,
    Schema,
)

from .const import (
    CONF_ADVANCED_SHUTTER,
    CONF_BUS_INTERFACE,
    CONF_CENTRAL,
    CONF_COOLING_SUPPORT,
    CONF_DEVICE_CLASS,
    CONF_DEVICE_MODEL,
    CONF_DIMMABLE,
    CONF_ENTITIES,
    CONF_ENTITY_NAME,
    CONF_FAN_SUPPORT,
    CONF_HEATING_SUPPORT,
    CONF_ICON,
    CONF_ICON_ON,
    CONF_INVERTED,
    CONF_MANUFACTURER,
    CONF_PLATFORMS,
    CONF_STANDALONE,
    CONF_TRAVEL_TIME,
    CONF_WHERE,
    CONF_WHO,
    CONF_ZONE,
)


def format_mac(address: str) -> str:
    mac = re.sub("[.:-]", "", address).upper()
    mac = "".join(mac.split())
    if len(mac) != 12 or not mac.isalnum() or re.search("[G-Z]", mac) is not None:
        return None
    return ha_format_mac(mac)


class MacAddress(object):
    def __init__(self, msg=None):
        self.msg = msg

    def __call__(self, v):
        v = format_mac(v)
        if v is None:
            raise Invalid("Invalid MAC address")
        return format_mac(v)

    def __repr__(self):
        return "MacAddress(%s, msg=%r)" % ("String", self.msg)


class General(object):
    def __init__(self, msg=None):
        self.msg = msg

    def __call__(self, v):
        if isinstance(v, str) and v == "0":
            return v
        else:
            raise Invalid(f"Invalid General WHERE {v}, it must be 0.")

    def __repr__(self):
        return "Where(%s, msg=%r)" % ("String", self.msg)


class Area(object):
    def __init__(self, msg=None):
        self.msg = msg

    def __call__(self, v):
        if isinstance(v, str) and v in ["00", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]:
            return v
        else:
            raise Invalid(f"Invalid Area WHERE {v}, it must be a string in [00, 1-9, 10].")

    def __repr__(self):
        return "Where(%s, msg=%r)" % ("String", self.msg)


class Group(object):
    def __init__(self, msg=None):
        self.msg = msg

    def __call__(self, v):
        if isinstance(v, str) and v.startswith("#") and v[1:].isdigit() and int(v[1:]) >= 1 and int(v[1:]) <= 255:
            return f"#{int(v[1:])}"
        else:
            raise Invalid(f"Invalid Group WHERE {v}, it must be a string like '#[1-255]'.")

    def __repr__(self):
        return "Where(%s, msg=%r)" % ("String", self.msg)


class PointToPoint(object):
    def __init__(self, msg=None):
        self.msg = msg

    def __call__(self, v):
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

    def __repr__(self):
        return "Where(%s, msg=%r)" % ("String", self.msg)


class SpecialWhere(object):
    def __init__(self, msg=None):
        self.msg = msg

    def __call__(self, v):
        if isinstance(v, str) and v.isdigit():
            return v
        else:
            raise Invalid(f"Invalid WHERE {v}, it must be a string of digits.")

    def __repr__(self):
        return "Where(%s, msg=%r)" % ("String", self.msg)


class BusInterface(object):
    def __init__(self, msg=None):
        self.msg = msg

    def __call__(self, v):
        if isinstance(v, str) and v.isdigit() and len(v) == 2:
            if int(v) > 15:
                raise Invalid(f"Invalid Bus Interface number {v}, it must be between 00 and 15.")
        elif v is not None:
            raise Invalid(f"Invalid Bus Interface number {v}, it must be a string of 2 digits.")
        return v

    def __repr__(self):
        return "BusInterface(%s, msg=%r)" % ("String", self.msg)


class MyHomeConfigSchema(Schema):
    def __call__(self, data):
        data = super().__call__(data)
        _rekeyed_data = {}
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
    def __call__(self, data):
        data = super().__call__(data)
        _rekeyed_data = {}

        for device in data:
            data[device][CONF_ENTITIES] = {}
            if CONF_WHERE in data[device]:
                _new_key = (
                    f"{data[device][CONF_WHO]}-{data[device][CONF_WHERE]}#4#{data[device][CONF_BUS_INTERFACE]}"
                    if CONF_BUS_INTERFACE in data[device] and data[device][CONF_BUS_INTERFACE] is not None
                    else f"{data[device][CONF_WHO]}-{data[device][CONF_WHERE]}"
                )
                _rekeyed_data[_new_key] = data[device]
            elif CONF_ZONE in data[device]:
                _new_key = f"{data[device][CONF_WHO]}-{data[device][CONF_ZONE]}"
                data[device][CONF_ZONE] = f"#0#{data[device][CONF_ZONE]}" if data[device][CONF_CENTRAL] and data[device][CONF_ZONE] != "#0" else data[device][CONF_ZONE]
                data[device][CONF_NAME] = (
                    data[device][CONF_NAME] if CONF_NAME in data[device] else "Central unit" if data[device][CONF_ZONE].startswith("#0") else f"Zone {data[device][CONF_ZONE]}"
                )
                _rekeyed_data[_new_key] = data[device]
                _rekeyed_data[str(device)] = data[device]
                clean_zone = str(data[device][CONF_ZONE]).split("#")[-1]
                _rekeyed_data[clean_zone] = data[device]
                _rekeyed_data[str(data[device][CONF_ZONE])] = data[device]
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
    def __call__(self, data):
        data = super().__call__(data)
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
                _new_key = (
                    f"{data[device][CONF_WHO]}-{data[device][CONF_WHERE]}#4#{data[device][CONF_BUS_INTERFACE]}"
                    if CONF_BUS_INTERFACE in data[device] and data[device][CONF_BUS_INTERFACE] is not None
                    else f"{data[device][CONF_WHO]}-{data[device][CONF_WHERE]}"
                )
                _rekeyed_data[_new_key] = data[device]
            if CONF_DEVICE_MODEL not in data[device]:
                data[device][CONF_DEVICE_MODEL] = None

        return _rekeyed_data


light_schema = MyHomeDeviceSchema(
    {
        Required(str): {
            Optional(CONF_WHO, default="1"): "1",
            Required(CONF_WHERE): All(
                Coerce(str), Any(General(), Area(), Group(), PointToPoint(), msg="Invalid <WHERE>, expecting a valid General, Area, Group or Point-to-Point <WHERE>")
            ),
            Optional(CONF_BUS_INTERFACE): All(Coerce(str), BusInterface()),
            Required(CONF_NAME): str,
            Optional(CONF_ENTITY_NAME): str,
            Optional(CONF_ICON): str,
            Optional(CONF_ICON_ON): str,
            Optional(CONF_DIMMABLE, default=False): Boolean(),
            Optional(CONF_MANUFACTURER, default="BTicino S.p.A."): str,
            Optional(CONF_DEVICE_MODEL): Coerce(str),
        }
    }
)

switch_schema = MyHomeDeviceSchema(
    {
        Required(str): {
            Optional(CONF_WHO, default="1"): "1",
            Required(CONF_WHERE): All(
                Coerce(str), Any(General(), Area(), Group(), PointToPoint(), msg="Invalid <WHERE>, expecting a valid General, Area, Group or Point-to-Point <WHERE>")
            ),
            Optional(CONF_BUS_INTERFACE): All(Coerce(str), BusInterface()),
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

cover_schema = MyHomeDeviceSchema(
    {
        Required(str): {
            Optional(CONF_WHO, default="2"): "2",
            Required(CONF_WHERE): All(
                Coerce(str), Any(General(), Area(), Group(), PointToPoint(), msg="Invalid <WHERE>, expecting a valid General, Area, Group or Point-to-Point <WHERE>")
            ),
            Optional(CONF_BUS_INTERFACE): All(Coerce(str), BusInterface()),
            Required(CONF_NAME): str,
            Optional(CONF_ENTITY_NAME): str,
            Optional(CONF_ADVANCED_SHUTTER, default=False): Boolean(),
            Optional("advanced_shutter", default=False): Boolean(),
            Optional(CONF_TRAVEL_TIME, default=25): Coerce(int),
            Optional(CONF_MANUFACTURER, default="BTicino S.p.A."): str,
            Optional(CONF_DEVICE_MODEL): Coerce(str),
        }
    }
)

binary_sensor_schema = MyHomeDeviceSchema(
    {
        Required(str): {
            Optional(CONF_WHO, default="25"): In(["1", "9", "25"]),
            Required(CONF_WHERE): All(Coerce(str), SpecialWhere()),
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
            Required(CONF_WHERE): All(Coerce(str), SpecialWhere()),
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
            Required(CONF_WHERE): All(Coerce(str), Any(General(), Area(), Group(), PointToPoint(), SpecialWhere())),
            Required(CONF_NAME): str,
            Optional(CONF_ENTITY_NAME): str,
            Optional(CONF_MANUFACTURER, default="BTicino S.p.A."): str,
            Optional(CONF_DEVICE_MODEL, default="F4201"): Coerce(str),
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
        Optional(CONF_MAC): MacAddress(),
        Optional(LIGHT): lambda v: light_schema(v),
        Optional(SWITCH): lambda v: switch_schema(v),
        Optional(COVER): lambda v: cover_schema(v),
        Optional(BINARY_SENSOR): lambda v: binary_sensor_schema(v),
        Optional(SENSOR): lambda v: sensor_schema(v),
        Optional(CLIMATE): lambda v: climate_schema(v),
        Optional(ALARM_CONTROL_PANEL): lambda v: alarm_control_panel_schema(v),
    }
)

config_schema = MyHomeConfigSchema({Required(str): gateway_schema})
