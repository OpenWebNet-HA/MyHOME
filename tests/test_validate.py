"""Comprehensive unit tests for custom_components.myhome.validate.

Tests all validators, schemas, device and sensor rekeying, auto-button generation,
and error boundaries to achieve 100% test coverage.
"""
import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.const import CONF_MAC, CONF_NAME
from voluptuous import ALLOW_EXTRA, Invalid

from custom_components.myhome.const import (
    CONF_ADVANCED_SHUTTER,
    CONF_BUS_INTERFACE,
    CONF_CENTRAL,
    CONF_DEVICE_CLASS,
    CONF_DEVICE_MODEL,
    CONF_DIMMABLE,
    CONF_ENTITIES,
    CONF_ENTITY_NAME,
    CONF_ICON,
    CONF_ICON_ON,
    CONF_INVERTED,
    CONF_MANUFACTURER,
    CONF_PLATFORMS,
    CONF_WHERE,
    CONF_WHO,
    CONF_ZONE,
)
from custom_components.myhome.validate import (
    Area,
    BusInterface,
    General,
    Group,
    MacAddress,
    MyHomeSensorSchema,
    PointToPoint,
    SpecialWhere,
    binary_sensor_schema,
    climate_schema,
    config_schema,
    cover_schema,
    format_mac,
    light_schema,
    sensor_schema,
    switch_schema,
)

# ============================================================================
# format_mac and MacAddress Tests
# ============================================================================


class TestMacValidation:
    """Test MAC address normalization and validation."""

    @pytest.mark.parametrize(
        "raw_mac,expected",
        [
            ("00:03:50:ab:cd:ef", "00:03:50:ab:cd:ef"),
            ("00-03-50-AB-CD-EF", "00:03:50:ab:cd:ef"),
            ("00.03.50.ab.cd.ef", "00:03:50:ab:cd:ef"),
            ("00 03 50 AB CD EF", "00:03:50:ab:cd:ef"),
            ("000350abcdef", "00:03:50:ab:cd:ef"),
            ("000350ABCDEF", "00:03:50:ab:cd:ef"),
        ],
    )
    def test_format_mac_valid(self, raw_mac, expected):
        """Test valid MAC address strings with various delimiters."""
        assert format_mac(raw_mac) == expected

    @pytest.mark.parametrize(
        "invalid_mac",
        [
            "00:03:50",  # too short
            "00:03:50:ab:cd:ef:12",  # too long
            "00:03:50:ab:cd:zz",  # non-hex chars > F
            "00:03:50:ab:cd:g1",  # non-hex
            "00:03:50:ab:cd:??",  # non-alphanumeric
            "",  # empty
            "   ",  # whitespace only
        ],
    )
    def test_format_mac_invalid(self, invalid_mac):
        """Test invalid MAC address strings return None."""
        assert format_mac(invalid_mac) is None

    def test_mac_address_callable_success(self):
        """Test MacAddress validator with valid inputs."""
        validator = MacAddress("Invalid MAC format")
        assert validator("00:03:50:11:22:33") == "00:03:50:11:22:33"
        assert repr(validator) == "MacAddress(String, msg='Invalid MAC format')"

    def test_mac_address_callable_failure(self):
        """Test MacAddress validator raises voluptuous Invalid on bad MAC."""
        validator = MacAddress()
        with pytest.raises(Invalid, match="Invalid MAC address"):
            validator("invalid-mac")


# ============================================================================
# WHERE Validator Classes Tests
# ============================================================================


class TestWhereValidators:
    """Test custom WHERE validator classes."""

    def test_general_validator(self):
        """Test General validator (must be '0')."""
        validator = General("custom general msg")
        assert validator("0") == "0"
        assert repr(validator) == "Where(String, msg='custom general msg')"

        with pytest.raises(Invalid, match="Invalid General WHERE 1, it must be 0"):
            validator("1")
        with pytest.raises(Invalid, match="Invalid General WHERE 00, it must be 0"):
            validator("00")
        with pytest.raises(Invalid, match="Invalid General WHERE 0, it must be 0"):
            validator(0)  # integer instead of str

    def test_area_validator(self):
        """Test Area validator (must be '00', '1'-'9', or '10')."""
        validator = Area("custom area msg")
        assert validator("00") == "00"
        for i in range(1, 11):
            assert validator(str(i)) == str(i)
        assert repr(validator) == "Where(String, msg='custom area msg')"

        with pytest.raises(Invalid, match="Invalid Area WHERE 0"):
            validator("0")
        with pytest.raises(Invalid, match="Invalid Area WHERE 11"):
            validator("11")
        with pytest.raises(Invalid, match="Invalid Area WHERE 1"):
            validator(1)  # int
        with pytest.raises(Invalid, match="Invalid Area WHERE None"):
            validator(None)

    def test_group_validator(self):
        """Test Group validator (must be '#[1-255]')."""
        validator = Group("custom group msg")
        assert validator("#1") == "#1"
        assert validator("#255") == "#255"
        assert validator("#01") == "#1"
        assert validator("#042") == "#42"
        assert repr(validator) == "Where(String, msg='custom group msg')"

        with pytest.raises(Invalid, match=r"Invalid Group WHERE #0"):
            validator("#0")
        with pytest.raises(Invalid, match=r"Invalid Group WHERE #256"):
            validator("#256")
        with pytest.raises(Invalid, match=r"Invalid Group WHERE 1"):
            validator("1")
        with pytest.raises(Invalid, match=r"Invalid Group WHERE #abc"):
            validator("#abc")
        with pytest.raises(Invalid, match=r"Invalid Group WHERE #"):
            validator("#")
        with pytest.raises(Invalid, match=r"Invalid Group WHERE 42"):
            validator(42)

    def test_point_to_point_validator(self):
        """Test PointToPoint validator (2 or 4 digits, A=[0-10], PL=[0-15])."""
        validator = PointToPoint("custom p2p msg")
        assert validator("11") == "11"
        assert validator("00") == "00"
        assert validator("99") == "99"
        assert validator("0101") == "0101"
        assert validator("1015") == "1015"
        assert validator("0000") == "0000"
        assert repr(validator) == "Where(String, msg='custom p2p msg')"

        # Non-digits / non-str
        with pytest.raises(Invalid, match="it must be a string of 2 or 4 digits"):
            validator("1a")
        with pytest.raises(Invalid, match="it must be a string of 2 or 4 digits"):
            validator(11)
        with pytest.raises(Invalid, match="it must be a string of 2 or 4 digits"):
            validator(None)

        # Invalid lengths
        with pytest.raises(Invalid, match="length, it must be a string of 2 or 4 digits"):
            validator("1")
        with pytest.raises(Invalid, match="length, it must be a string of 2 or 4 digits"):
            validator("123")
        with pytest.raises(Invalid, match="length, it must be a string of 2 or 4 digits"):
            validator("12345")

        # Range violations (A > 10 or PL > 15)
        # For 4-digit "9901": A="99" (>10), PL="01"
        with pytest.raises(Invalid, match="A must be \\[0-10\\] and PL must be \\[0-15\\]"):
            validator("9901")
        # For 4-digit "0199": A="01", PL="99" (>15)
        with pytest.raises(Invalid, match="A must be \\[0-10\\] and PL must be \\[0-15\\]"):
            validator("0199")

    def test_special_where_validator(self):
        """Test SpecialWhere validator (must be a string of digits)."""
        validator = SpecialWhere("custom special msg")
        assert validator("1") == "1"
        assert validator("123456") == "123456"
        assert repr(validator) == "Where(String, msg='custom special msg')"

        with pytest.raises(Invalid, match="it must be a string of digits"):
            validator("abc")
        with pytest.raises(Invalid, match="it must be a string of digits"):
            validator("#1")
        with pytest.raises(Invalid, match="it must be a string of digits"):
            validator("")
        with pytest.raises(Invalid, match="it must be a string of digits"):
            validator(123)

    def test_bus_interface_validator(self):
        """Test BusInterface validator (2 digits, 00-15, or None)."""
        validator = BusInterface("custom bus msg")
        assert validator("00") == "00"
        assert validator("05") == "05"
        assert validator("15") == "15"
        assert validator(None) is None
        assert repr(validator) == "BusInterface(String, msg='custom bus msg')"

        # Greater than 15
        with pytest.raises(Invalid, match="between 00 and 15"):
            validator("16")
        with pytest.raises(Invalid, match="between 00 and 15"):
            validator("99")

        # Invalid format / length
        with pytest.raises(Invalid, match="string of 2 digits"):
            validator("1")
        with pytest.raises(Invalid, match="string of 2 digits"):
            validator("001")
        with pytest.raises(Invalid, match="string of 2 digits"):
            validator("ab")
        with pytest.raises(Invalid, match="string of 2 digits"):
            validator(5)


# ============================================================================
# Device Schema & Rekeying Tests
# ============================================================================


class TestDeviceSchemas:
    """Test individual entity platform schemas and device rekeying."""

    def test_light_schema_point_to_point(self):
        """Test light schema with point-to-point WHERE and default injections."""
        raw = {
            "living_room": {
                CONF_WHERE: "11",
                CONF_NAME: "Living Room Light",
            }
        }
        res = light_schema(raw)
        key = "1-11"
        assert key in res
        device = res[key]
        assert device[CONF_WHO] == "1"
        assert device[CONF_WHERE] == "11"
        assert device[CONF_NAME] == "Living Room Light"
        assert device[CONF_DIMMABLE] is False
        assert device[CONF_MANUFACTURER] == "BTicino S.p.A."
        assert device[CONF_DEVICE_MODEL] is None
        assert device[CONF_ICON] is None
        assert device[CONF_ICON_ON] is None
        assert device[CONF_ENTITY_NAME] is None
        assert device[CONF_ENTITIES] == {}

    def test_light_schema_bus_interface_and_options(self):
        """Test light schema with bus interface and custom options."""
        raw = {
            "dining_room": {
                CONF_WHERE: "12",
                CONF_BUS_INTERFACE: "04",
                CONF_NAME: "Dining Light",
                CONF_DIMMABLE: True,
                CONF_MANUFACTURER: "Legrand",
                CONF_DEVICE_MODEL: "F411/2",
                CONF_ICON: "mdi:lightbulb",
                CONF_ICON_ON: "mdi:lightbulb-on",
                CONF_ENTITY_NAME: "Dining Ceiling",
            }
        }
        res = light_schema(raw)
        key = "1-12#4#04"
        assert key in res
        device = res[key]
        assert device[CONF_DIMMABLE] is True
        assert device[CONF_MANUFACTURER] == "Legrand"
        assert device[CONF_DEVICE_MODEL] == "F411/2"
        assert device[CONF_ICON] == "mdi:lightbulb"
        assert device[CONF_ICON_ON] == "mdi:lightbulb-on"
        assert device[CONF_ENTITY_NAME] == "Dining Ceiling"

    def test_switch_schema(self):
        """Test switch schema with device class outlet and switch."""
        raw = {
            "boiler": {
                CONF_WHERE: "21",
                CONF_NAME: "Boiler Relay",
                CONF_DEVICE_CLASS: SwitchDeviceClass.SWITCH,
            },
            "coffee_machine": {
                CONF_WHERE: "22",
                CONF_NAME: "Coffee Socket",
                CONF_DEVICE_CLASS: SwitchDeviceClass.OUTLET,
            },
        }
        res = switch_schema(raw)
        assert "1-21" in res
        assert res["1-21"][CONF_DEVICE_CLASS] == SwitchDeviceClass.SWITCH
        assert "1-22" in res
        assert res["1-22"][CONF_DEVICE_CLASS] == SwitchDeviceClass.OUTLET

    def test_switch_schema_invalid_device_class(self):
        """Test switch schema rejects invalid device classes."""
        raw = {
            "bad_switch": {
                CONF_WHERE: "21",
                CONF_NAME: "Bad Switch",
                CONF_DEVICE_CLASS: "motion",
            }
        }
        with pytest.raises(Invalid):
            switch_schema(raw)

    def test_cover_schema(self):
        """Test cover schema with advanced shutter boolean."""
        raw = {
            "bedroom_blind": {
                CONF_WHERE: "31",
                CONF_NAME: "Bedroom Blind",
                CONF_ADVANCED_SHUTTER: True,
            }
        }
        res = cover_schema(raw)
        assert "2-31" in res
        assert res["2-31"][CONF_WHO] == "2"
        assert res["2-31"][CONF_ADVANCED_SHUTTER] is True

    def test_binary_sensor_schema(self):
        """Test binary sensor schema across supported WHOs (1, 9, 25)."""
        raw = {
            "door_sensor": {
                CONF_WHO: "25",
                CONF_WHERE: "101",
                CONF_NAME: "Front Door",
                CONF_DEVICE_CLASS: BinarySensorDeviceClass.DOOR,
                CONF_INVERTED: True,
            },
            "motion_sensor": {
                CONF_WHO: "1",
                CONF_WHERE: "102",
                CONF_NAME: "Corridor Motion",
                CONF_DEVICE_CLASS: BinarySensorDeviceClass.MOTION,
            },
            "aux_sensor": {
                CONF_WHO: "9",
                CONF_WHERE: "103",
                CONF_NAME: "Aux Sensor",
            },
        }
        res = binary_sensor_schema(raw)
        assert "25-101" in res
        assert res["25-101"][CONF_INVERTED] is True
        assert "1-102" in res
        assert "9-103" in res

    def test_binary_sensor_schema_invalid_who(self):
        """Test binary sensor schema rejects unsupported WHO."""
        raw = {
            "bad_sensor": {
                CONF_WHO: "4",  # WHO 4 is climate, not binary sensor
                CONF_WHERE: "101",
                CONF_NAME: "Bad Sensor",
            }
        }
        with pytest.raises(Invalid):
            binary_sensor_schema(raw)

    def test_climate_schema_zones_and_naming(self):
        """Test climate schema handling zone formatting and default names."""
        raw = {
            "zone_standard": {
                CONF_ZONE: "1",
                CONF_CENTRAL: False,
            },
            "zone_central": {
                CONF_ZONE: "2",
                CONF_CENTRAL: True,
            },
            "central_zero": {
                CONF_ZONE: "#0",
                CONF_CENTRAL: True,
            },
            "custom_named": {
                CONF_ZONE: "3",
                CONF_NAME: "Master Bedroom Heat",
            },
        }
        res = climate_schema(raw)

        # zone_standard: name defaults to "Zone 1", zone stays "1"
        assert "4-1" in res
        assert res["4-1"][CONF_ZONE] == "1"
        assert res["4-1"][CONF_NAME] == "Zone 1"

        # zone_central: zone becomes "#0#2", name defaults to "Central unit"
        assert "4-2" in res
        assert res["4-2"][CONF_ZONE] == "#0#2"
        assert res["4-2"][CONF_NAME] == "Central unit"

        # central_zero: zone stays "#0", name defaults to "Central unit"
        assert "4-#0" in res
        assert res["4-#0"][CONF_ZONE] == "#0"
        assert res["4-#0"][CONF_NAME] == "Central unit"

        # custom_named: preserved
        assert "4-3" in res
        assert res["4-3"][CONF_NAME] == "Master Bedroom Heat"


# ============================================================================
# MyHomeSensorSchema Tests
# ============================================================================


class TestSensorSchema:
    """Test MyHomeSensorSchema with power, energy, temp, and illuminance."""

    def test_power_sensor_entities_injection(self):
        """Test power sensor sets WHO=18 and injects energy and power entities."""
        raw = {
            "main_power": {
                CONF_WHERE: "51",
                CONF_NAME: "Total Power",
                CONF_DEVICE_CLASS: SensorDeviceClass.POWER,
            }
        }
        res = sensor_schema(raw)
        assert "18-51" in res
        device = res["18-51"]
        assert device[CONF_WHO] == "18"
        assert "daily-energy" in device[CONF_ENTITIES]
        assert "monthly-energy" in device[CONF_ENTITIES]
        assert "total-energy" in device[CONF_ENTITIES]
        assert "power" in device[CONF_ENTITIES]

    def test_energy_sensor_entities_injection(self):
        """Test energy sensor sets WHO=18 and injects energy entities (without power)."""
        raw = {
            "solar_energy": {
                CONF_WHERE: "52",
                CONF_NAME: "Solar Energy",
                CONF_DEVICE_CLASS: SensorDeviceClass.ENERGY,
            }
        }
        res = sensor_schema(raw)
        assert "18-52" in res
        device = res["18-52"]
        assert device[CONF_WHO] == "18"
        assert "daily-energy" in device[CONF_ENTITIES]
        assert "monthly-energy" in device[CONF_ENTITIES]
        assert "total-energy" in device[CONF_ENTITIES]
        assert "power" not in device[CONF_ENTITIES]

    def test_temperature_sensor(self):
        """Test temperature sensor defaults WHO=4."""
        raw = {
            "outside_temp": {
                CONF_WHERE: "53",
                CONF_NAME: "Outside Temp",
                CONF_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
            }
        }
        res = sensor_schema(raw)
        assert "4-53" in res
        assert res["4-53"][CONF_WHO] == "4"

    def test_illuminance_sensor(self):
        """Test illuminance sensor defaults WHO=1."""
        raw = {
            "garden_lux": {
                CONF_WHERE: "54",
                CONF_NAME: "Garden Light Level",
                CONF_DEVICE_CLASS: SensorDeviceClass.ILLUMINANCE,
            }
        }
        res = sensor_schema(raw)
        assert "1-54" in res
        assert res["1-54"][CONF_WHO] == "1"

    def test_sensor_bus_interface_keying(self):
        """Test sensor rekeying includes bus interface when present."""
        raw = {
            "sub_bus_power": {
                CONF_WHERE: "55",
                CONF_NAME: "Sub Bus Power",
                CONF_DEVICE_CLASS: SensorDeviceClass.POWER,
            }
        }
        # Injects bus interface manually in raw schema dictionary
        raw["sub_bus_power"][CONF_BUS_INTERFACE] = "02"
        # Since sensor_schema does not define CONF_BUS_INTERFACE in its Schema,
        # test MyHomeSensorSchema directly:
        schema = MyHomeSensorSchema({}, extra=ALLOW_EXTRA)
        processed = schema(raw)
        assert "18-55#4#02" in processed

    @pytest.mark.parametrize(
        "device_class,wrong_who",
        [
            (SensorDeviceClass.POWER, "1"),
            (SensorDeviceClass.ENERGY, "4"),
            (SensorDeviceClass.TEMPERATURE, "18"),
            (SensorDeviceClass.ILLUMINANCE, "4"),
        ],
    )
    def test_sensor_mismatched_who_raises_invalid(self, device_class, wrong_who):
        """Test that passing an invalid WHO for a sensor device class raises Invalid."""
        raw = {
            "invalid_sensor": {
                CONF_WHERE: "60",
                CONF_NAME: "Mismatched Sensor",
                CONF_DEVICE_CLASS: device_class,
                CONF_WHO: wrong_who,
            }
        }
        with pytest.raises(Invalid, match="invalid sensor class for selected who"):
            sensor_schema(raw)


# ============================================================================
# Full Configuration & Gateway Schema Tests
# ============================================================================


class TestFullConfigSchema:
    """Test full configuration parsing, gateway schema, and automatic button generation."""

    def test_complete_valid_configuration(self):
        """Test an end-to-end multi-platform gateway YAML configuration."""
        raw_config = {
            "main_gateway": {
                CONF_MAC: "00:03:50:AA:BB:CC",
                "light": {
                    "kitchen": {
                        CONF_WHERE: "11",
                        CONF_NAME: "Kitchen Light",
                    },
                    "living_group": {
                        CONF_WHERE: "#1",
                        CONF_NAME: "Living Group",
                    },
                },
                "switch": {
                    "boiler": {
                        CONF_WHERE: "12",
                        CONF_NAME: "Boiler Relay",
                    },
                    "pump_group": {
                        CONF_WHERE: "#2",
                        CONF_NAME: "Pump Group",
                    },
                },
                "cover": {
                    "patio_cover": {
                        CONF_WHERE: "13",
                        CONF_NAME: "Patio Shutter",
                    },
                    "all_covers": {
                        CONF_WHERE: "#3",
                        CONF_NAME: "All Shutters",
                    },
                },
                "binary_sensor": {
                    "front_door": {
                        CONF_WHERE: "14",
                        CONF_NAME: "Front Door Contact",
                    }
                },
                "sensor": {
                    "mains_power": {
                        CONF_WHERE: "15",
                        CONF_NAME: "Mains Power",
                        CONF_DEVICE_CLASS: SensorDeviceClass.POWER,
                    }
                },
                "climate": {
                    "living_hvac": {
                        CONF_ZONE: "1",
                        CONF_NAME: "Living Climate",
                    }
                },
            }
        }

        rekeyed = config_schema(raw_config)
        mac_key = "00:03:50:aa:bb:cc"

        assert mac_key in rekeyed
        platforms = rekeyed[mac_key][CONF_PLATFORMS]

        # Verify platform rekeying
        assert "light" in platforms
        assert "switch" in platforms
        assert "cover" in platforms
        assert "binary_sensor" in platforms
        assert "sensor" in platforms
        assert "climate" in platforms

        # Verify automatic button generation:
        # P2P devices (where not starting with '#') are added to button platform,
        # while Group devices (where starting with '#') are excluded.
        assert "button" in platforms
        buttons = platforms["button"]

        assert "1-11" in buttons  # from kitchen light
        assert "1-#1" not in buttons  # living_group skipped

        assert "1-12" in buttons  # from boiler switch
        assert "1-#2" not in buttons  # pump_group skipped

        assert "2-13" in buttons  # from patio_cover
        assert "2-#3" not in buttons  # all_covers skipped

    def test_config_schema_invalid_mac(self):
        """Test config_schema raises Invalid on malformed MAC."""
        bad_config = {
            "gateway_1": {
                CONF_MAC: "00:03:INVALID",
            }
        }
        with pytest.raises(Invalid, match="Invalid MAC address"):
            config_schema(bad_config)

    def test_device_class_remapping_and_defaults(self):
        """Test string device_class remapping to CONF_DEVICE_CLASS and defaults in schemas."""
        # Switch with string device_class
        sw_data = {
            "sw1": {
                CONF_WHERE: "12",
                CONF_NAME: "Outlet 12",
                "device_class": SwitchDeviceClass.OUTLET,
            }
        }
        res_sw = switch_schema(sw_data)
        assert res_sw["1-12"][CONF_DEVICE_CLASS] == SwitchDeviceClass.OUTLET

        # Switch without device_class gets default SWITCH
        sw_no_dc = {
            "sw2": {
                CONF_WHERE: "13",
                CONF_NAME: "Switch 13",
            }
        }
        res_sw_no_dc = switch_schema(sw_no_dc)
        assert res_sw_no_dc["1-13"][CONF_DEVICE_CLASS] == SwitchDeviceClass.SWITCH

        # Binary sensor with string device_class
        bs_data = {
            "bs1": {
                CONF_WHERE: "21",
                CONF_NAME: "Front Door",
                "device_class": BinarySensorDeviceClass.DOOR,
            }
        }
        res_bs = binary_sensor_schema(bs_data)
        assert res_bs["25-21"][CONF_DEVICE_CLASS] == BinarySensorDeviceClass.DOOR

        # Cover with advanced_shutter
        cov_data = {
            "cov1": {
                CONF_WHERE: "22",
                CONF_NAME: "Living Cover",
                "advanced_shutter": True,
            }
        }
        res_cov = cover_schema(cov_data)
        assert res_cov["2-22"][CONF_ADVANCED_SHUTTER] is True

