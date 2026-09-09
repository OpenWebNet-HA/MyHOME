""" This module contains OpenWebNet messages definition """  # pylint: disable=too-many-lines

import datetime
import re
from dateutil.relativedelta import relativedelta
import pytz

MESSAGE_TYPE_ACTIVE_POWER = "active_power"
MESSAGE_TYPE_ENERGY_TOTALIZER = "energy_totalizer"
MESSAGE_TYPE_HOURLY_CONSUMPTION = "hourly_consumption"
MESSAGE_TYPE_DAILY_CONSUMPTION = "daily_consumption"
MESSAGE_TYPE_MONTHLY_CONSUMPTION = "monthly_consumption"
MESSAGE_TYPE_CURRENT_DAY_CONSUMPTION = "current_day_partial_consumption"
MESSAGE_TYPE_CURRENT_MONTH_CONSUMPTION = "current_month_partial_consumption"
MESSAGE_TYPE_MAIN_TEMPERATURE = "main_temperature"
MESSAGE_TYPE_MAIN_HUMIDITY = "main_humidity"
MESSAGE_TYPE_SECONDARY_TEMPERATURE = "secondary_temperature"
MESSAGE_TYPE_TARGET_TEMPERATURE = "target_temperature"
MESSAGE_TYPE_LOCAL_OFFSET = "local_offset"
MESSAGE_TYPE_LOCAL_TARGET_TEMPERATURE = "local_targer_temperature"
MESSAGE_TYPE_MODE = "hvac_mode"
MESSAGE_TYPE_MODE_TARGET = "hvac_mode_target"
MESSAGE_TYPE_ACTION = "hvac_action"
MESSAGE_TYPE_MOTION = "motion_detected"
MESSAGE_TYPE_PIR_SENSITIVITY = "pir_sensitivity"
MESSAGE_TYPE_ILLUMINANCE = "illuminance_value"
MESSAGE_TYPE_MOTION_TIMEOUT = "motion_timeout"
MESSAGE_TYPE_FAN_SPEED = "fan_speed"

CLIMATE_MODE_OFF = "off"
CLIMATE_MODE_HEAT = "heat"
CLIMATE_MODE_COOL = "cool"
CLIMATE_MODE_AUTO = "auto"

PIR_SENSITIVITY_MAPPING = ["low", "medium", "high", "very high"]


class OWNMessage:
    _ACK = re.compile(r"^\*#\*1##$")  #  *#*1##
    _NACK = re.compile(r"^\*#\*0##$")  #  *#*0##
    _COMMAND_SESSION = re.compile(r"^\*99\*0##$")  #  *99*0##
    _EVENT_SESSION = re.compile(r"^\*99\*1##$")  #  *99*1##
    _NONCE = re.compile(r"^\*#(\d{5,})##$")  #  *#123456789##
    _SHA = re.compile(r"^\*98\*(\d)##$")  #  *98*SHA##

    _STATUS = re.compile(
        r"^\*(?P<who>\d+)\*(?P<what>\d+)(?P<what_param>(?:#\d+)*)\*(?P<where>\*|#?\d+)(?P<where_param>(?:#\d+)*)##$"  # pylint: disable=line-too-long
    )  #  *WHO*WHAT*WHERE##
    _STATUS_REQUEST = re.compile(
        r"^\*#(?P<who>\d+)(?:\*(?P<where>#?\d+)(?P<where_param>(?:#\d+)*))?##$"
    )  #  *#WHO*WHERE or *#WHO##
    _DIMENSION_WRITING = re.compile(
        r"^\*#(?P<who>\d+)\*(?P<where>#?\d+)?(?P<where_param>(?:#\d+)*)?\*#(?P<dimension>\d+)(?P<dimension_param>(?:#\d+)*)?(?P<dimension_value>(?:\*\d*)+)##$"  # pylint: disable=line-too-long
    )  #  *#WHO*WHERE*#DIMENSION*VAL1*VALn##
    _DIMENSION_REQUEST = re.compile(
        r"^\*#(?P<who>\d+)\*(?P<where>#?\d+)?(?P<where_param>(?:#\d+)*)?\*(?P<dimension>\d+)##$"
    )  #  *#WHO*WHERE*DIMENSION##
    _DIMENSION_REQUEST_REPLY = re.compile(
        r"^\*#(?P<who>\d+)\*(?P<where>#?\d+)?(?P<where_param>(?:#\d+)*)?\*(?P<dimension>\d+)(?P<dimension_param>(?:#\d+)*)?(?P<dimension_value>(?:\*\d*)+)##$"  # pylint: disable=line-too-long
    )  #  *#WHO*WHERE*DIMENSION*VAL1*VALn##

    """ Base class for all OWN messages """

    def __init__(self, data):
        self._raw = data
        self._human_readable_log = self._raw
        self._family = ""
        self._who = None
        self._what = None
        self._what_param = None
        self._where = None
        self._where_param = None
        self._dimension = None
        self._dimension_param = None
        self._dimension_value = None
        self._message_type = None
        self._is_valid_message = False

        if self._STATUS.match(self._raw):
            self._is_valid_message = True
            self._match = self._STATUS.match(self._raw)
            self._family = "EVENT"
            self._message_type = "STATUS"
            self._who = int(self._match.group("who"))
            self._what = int(self._match.group("what"))
            if self._what == 1000:
                self._family = "COMMAND_TRANSLATION"
            self._what_param = self._match.group("what_param").split("#")
            del self._what_param[0]
            self._where = self._match.group("where")
            self._where_param = self._match.group("where_param").split("#")
            del self._where_param[0]
            self._dimension = None
            self._dimension_param = None
            self._dimension_value = None

        elif self._STATUS_REQUEST.match(self._raw):
            self._is_valid_message = True
            self._match = self._STATUS_REQUEST.match(self._raw)
            self._family = "REQUEST"
            self._message_type = "STATUS_REQUEST"
            self._who = int(self._match.group("who"))
            self._what = None
            self._what_param = None
            self._where = self._match.group("where")
            if self._match.group("where_param"):
                self._where_param = self._match.group("where_param").split("#")
                del self._where_param[0]
            else:
                self._where_param = []
            self._dimension = None
            self._dimension_param = None
            self._dimension_value = None

        elif self._DIMENSION_REQUEST.match(self._raw):
            self._is_valid_message = True
            self._match = self._DIMENSION_REQUEST.match(self._raw)
            self._family = "REQUEST"
            self._message_type = "DIMENSION_REQUEST"
            self._who = int(self._match.group("who"))
            self._what = None
            self._what_param = None
            self._where = self._match.group("where")
            self._where_param = self._match.group("where_param").split("#")
            del self._where_param[0]
            self._dimension = int(self._match.group("dimension"))
            self._dimension_param = None
            self._dimension_value = None

        elif self._DIMENSION_REQUEST_REPLY.match(self._raw):
            self._is_valid_message = True
            self._match = self._DIMENSION_REQUEST_REPLY.match(self._raw)
            self._family = "EVENT"
            self._message_type = "DIMENSION_REQUEST_REPLY"
            self._who = int(self._match.group("who"))
            self._what = None
            self._what_param = None
            self._where = self._match.group("where")
            self._where_param = self._match.group("where_param").split("#")
            del self._where_param[0]
            self._dimension = int(self._match.group("dimension"))
            self._dimension_param = self._match.group("dimension_param").split("#")
            del self._dimension_param[0]
            self._dimension_value = self._match.group("dimension_value").split("*")
            del self._dimension_value[0]

        elif self._DIMENSION_WRITING.match(self._raw):
            self._is_valid_message = True
            self._match = self._DIMENSION_WRITING.match(self._raw)
            self._family = "COMMAND"
            self._message_type = "DIMENSION_WRITING"
            self._who = int(self._match.group("who"))
            self._what = None
            self._what_param = None
            self._where = self._match.group("where")
            self._where_param = self._match.group("where_param").split("#")
            del self._where_param[0]
            self._dimension = int(self._match.group("dimension"))
            self._dimension_param = self._match.group("dimension_param").split("#")
            del self._dimension_param[0]
            self._dimension_value = self._match.group("dimension_value").split("*")
            del self._dimension_value[0]

    @classmethod
    def parse(cls, data):
        if (
            cls._ACK.match(data)
            or cls._NACK.match(data)
            or cls._COMMAND_SESSION.match(data)
            or cls._EVENT_SESSION.match(data)
            or cls._NONCE.match(data)
            or cls._SHA.match(data)
        ):
            return OWNSignaling(data)
        elif cls._STATUS.match(data) or cls._DIMENSION_REQUEST_REPLY.match(data):
            return OWNEvent.parse(data)
        elif (
            cls._STATUS_REQUEST.match(data)
            or cls._DIMENSION_REQUEST.match(data)
            or cls._DIMENSION_WRITING.match(data)
        ):
            return OWNCommand.parse(data)
        else:
            return None

    @property
    def is_event(self) -> bool:
        return self._family == "EVENT"

    @property
    def is_command(self) -> bool:
        return self._family == "COMMAND"

    @property
    def is_request(self) -> bool:
        return self._family == "REQUEST"

    @property
    def is_translation(self) -> bool:
        return self._family == "COMMAND_TRANSLATION"

    @property
    def is_valid(self) -> bool:
        return self._is_valid_message

    @property
    def who(self) -> int:
        """The 'who' ID of the subject of this message"""
        return self._who

    @property
    def where(self) -> str:
        """The 'where' ID of the subject of this message"""
        return self._where  # [1:] if self._where.startswith('#') else self._where

    @property
    def interface(self) -> str:
        """The 'where' parameter corresponding to the bus interface of the subject of this message"""
        return (
            self._where_param[1]
            if self._who in [1, 2, 15]
            and self._where_param is not None
            and len(self._where_param) > 1
            and self._where_param[0] == "4"
            else None
        )

    @property
    def dimension(self) -> str:
        """The 'where' ID of the subject of this message"""
        return self._dimension

    @property
    def entity(self) -> str:
        """The ID of the subject of this message"""
        return self.unique_id

    @property
    def unique_id(self) -> str:
        """The ID of the subject of this message"""
        if self._where is None:
            return f"{self.who}"
        return (
            f"{self.who}-{self.where}#4#{self.interface}"
            if self.interface is not None
            else f"{self.who}-{self.where}"
        )

    @property
    def event_content(self) -> dict:
        _event = {
            "message": self._raw,
            "family": self._family.replace("_", " ").capitalize(),
            "type": self._message_type.replace("_", " ").capitalize(),
            "who": self._who,
        }
        if self._where:
            _event.update({"where": self._where})
        if self.interface:
            _event.update({"interface": self.interface})
            if self._where_param and len(self._where_param) > 2:
                _event.update({"where parameters": self._where_param[2:]})
        elif self._where_param:
            _event.update({"where parameters": self._where_param})
        if self._what:
            _event.update({"what": self._what})
        if self._what_param:
            _event.update({"what parameters": self._what_param})
        if self._dimension:
            _event.update({"dimension": self._dimension})
        if self._dimension_param:
            _event.update({"dimension parameters": self._dimension_param})
        if self._dimension_value:
            _event.update({"dimension values": self._dimension_value})

        return _event

    @property
    def human_readable_log(self) -> str:
        """A human readable log of the event"""
        return self._human_readable_log

    @property
    def _interface_log_text(self) -> str:
        return f" on interface {self.interface}" if self.interface is not None else ""

    @property
    def is_general(self) -> bool:
        if self.who == 1 or self.who == 2:
            return self._where == "0"
        return False

    @property
    def is_group(self) -> bool:
        if self.who == 1 or self.who == 2:
            if self._where.startswith("#"):
                return True
            else:
                return False
        else:
            return False

    @property
    def is_area(self) -> bool:
        if self.who == 1 or self.who == 2:
            try:
                if (
                    self._where == "00"
                    or self._where == "100"
                    or (
                        len(self._where) == 1
                        and int(self._where) > 0
                        and int(self._where) < 10
                    )
                ):
                    return True
                else:
                    return False
            except ValueError:
                return False
        else:
            return False

    @property
    def group(self) -> int:
        if self.is_group:
            return int(self._where[1:])
        else:
            return None

    @property
    def area(self) -> int:
        if self.is_area:
            return 10 if self._where == "100" else int(self._where)
        else:
            return None

    def __repr__(self) -> str:
        return self._raw

    def __str__(self) -> str:
        return self._raw


class OWNEvent(OWNMessage):
    """
    This class is a subclass of messages.
    All messages received during an event session are events.
    Dividing this in a subclass provides better clarity
    """

    @classmethod
    def parse(cls, data):
        _match = re.match(r"^\*#?(?P<who>\d+)(?:\*.+)?##$", data)

        if _match:
            _who = int(_match.group("who"))

            if _who == 0:
                return OWNScenarioEvent(data)
            elif _who == 1:
                return OWNLightingEvent(data)
            elif _who == 2:
                return OWNAutomationEvent(data)
            elif _who == 4:
                return OWNHeatingEvent(data)
            elif _who == 5:
                return OWNAlarmEvent(data)
            elif _who == 9:
                return OWNAuxEvent(data)
            elif _who == 13:
                return OWNGatewayEvent(data)
            elif _who == 15:
                return OWNCENEvent(data)
            elif _who == 16:
                return OWNSoundEvent(data)
            elif _who == 17:
                return OWNSceneEvent(data)
            elif _who == 18:
                return OWNEnergyEvent(data)
            elif _who == 25:
                if data.startswith("*#"):
                    return OWNDryContactEvent(data)
                _parts = data.strip("#").split("*")
                _what_part = _parts[2] if len(_parts) > 2 else ""
                _what_code = _what_part.split("#")[0]
                try:
                    _what_int = int(_what_code)
                except ValueError:
                    _what_int = None
                if _what_int is not None and 21 <= _what_int <= 28:
                    return OWNCENPlusEvent(data)
                return OWNDryContactEvent(data)
            elif _who > 1000:
                return cls(data)

        return data


class OWNScenarioEvent(OWNEvent):
    def __init__(self, data):
        super().__init__(data)

        self._scenario = self._what
        self._control_panel = self._where
        self._human_readable_log = f"Scenario {self._scenario} from control panel {self._control_panel} has been launched."  # pylint: disable=line-too-long

    @property
    def scenario(self):
        return self._scenario

    @property
    def control_panel(self):
        return self._control_panel


class OWNLightingEvent(OWNEvent):
    def __init__(self, data):
        super().__init__(data)

        self._type = None
        self._state = None
        self._brightness = None
        self._brightness_preset = None
        self._transition = None
        self._timer = None
        self._blinker = None
        self._illuminance = None
        self._motion = False
        self._pir_sensitivity = None
        self._motion_timeout = None

        if self._what is not None and self._what != 1000:
            self._state = self._what

            if self._state == 0:  # Light off
                self._human_readable_log = (
                    f"Light {self._where}{self._interface_log_text} is switched off."
                )
            elif self._state == 1:  # Light on
                self._human_readable_log = (
                    f"Light {self._where}{self._interface_log_text} is switched on."
                )
            elif self._state > 1 and self._state < 11:  # Light dimmed to preset value
                self._brightness_preset = self._state
                # self._brightness = self._state * 10
                self._human_readable_log = f"Light {self._where}{self._interface_log_text} is switched on at brightness level {self._state}."  # pylint: disable=line-too-long
            elif self._state == 11:  # Timer at 1m
                self._timer = 60
                self._human_readable_log = f"Light {self._where}{self._interface_log_text} is switched on for {self._timer}s."
            elif self._state == 12:  # Timer at 2m
                self._timer = 120
                self._human_readable_log = f"Light {self._where}{self._interface_log_text} is switched on for {self._timer}s."
            elif self._state == 13:  # Timer at 3m
                self._timer = 180
                self._human_readable_log = f"Light {self._where}{self._interface_log_text} is switched on for {self._timer}s."
            elif self._state == 14:  # Timer at 4m
                self._timer = 240
                self._human_readable_log = f"Light {self._where}{self._interface_log_text} is switched on for {self._timer}s."
            elif self._state == 15:  # Timer at 5m
                self._timer = 300
                self._human_readable_log = f"Light {self._where}{self._interface_log_text} is switched on for {self._timer}s."
            elif self._state == 16:  # Timer at 15m
                self._timer = 900
                self._human_readable_log = f"Light {self._where}{self._interface_log_text} is switched on for {self._timer}s."
            elif self._state == 17:  # Timer at 30s
                self._timer = 30
                self._human_readable_log = f"Light {self._where}{self._interface_log_text} is switched on for {self._timer}s."
            elif self._state == 18:  # Timer at 0.5s
                self._timer = 0.5
                self._human_readable_log = f"Light {self._where}{self._interface_log_text} is switched on for {self._timer}s."
            elif self._state >= 20 and self._state <= 29:  # Light blinking
                self._blinker = 0.5 * (self._state - 19)
                self._human_readable_log = f"Light {self._where}{self._interface_log_text} is blinking every {self._blinker}s."
            elif self._state == 34:  # Motion detected
                self._type = MESSAGE_TYPE_MOTION
                self._motion = True
                self._human_readable_log = f"Light/motion sensor {self._where}{self._interface_log_text} detected motion"

        if self._dimension is not None and self._dimension_value:
            if self._dimension == 1 or self._dimension == 4:  # Brightness value
                self._brightness = int(self._dimension_value[0]) - 100
                self._transition = int(self._dimension_value[1])
                if self._brightness == 0:
                    self._state = 0
                    self._human_readable_log = f"Light {self._where}{self._interface_log_text} is switched off."
                else:
                    self._state = 1
                    self._human_readable_log = f"Light {self._where}{self._interface_log_text} is switched on at {self._brightness}%."
            elif self._dimension == 2:  # Time value
                self._timer = (
                    int(self._dimension_value[0]) * 3600
                    + int(self._dimension_value[1]) * 60
                    + int(self._dimension_value[2])
                )
                self._human_readable_log = f"Light {self._where}{self._interface_log_text} is switched on for {self._timer}s."
            elif self._dimension == 5:  # PIR sensitivity
                self._type = MESSAGE_TYPE_PIR_SENSITIVITY
                self._pir_sensitivity = int(self._dimension_value[0])
                self._human_readable_log = f"Light/motion sensor {self._where}{self._interface_log_text} PIR sesitivity is {PIR_SENSITIVITY_MAPPING[self._pir_sensitivity]}."  # pylint: disable=line-too-long
            elif self._dimension == 6:  # Illuminance value
                self._type = MESSAGE_TYPE_ILLUMINANCE
                self._illuminance = int(self._dimension_value[0])
                self._human_readable_log = f"Light/motion sensor {self._where}{self._interface_log_text} detected an illuminance value of {self._illuminance} lx."  # pylint: disable=line-too-long
            elif self._dimension == 7:  # Motion timeout value
                self._type = MESSAGE_TYPE_MOTION_TIMEOUT
                self._motion_timeout = datetime.timedelta(
                    hours=int(self._dimension_value[0]),
                    minutes=int(self._dimension_value[1]),
                    seconds=int(self._dimension_value[2]),
                )
                self._human_readable_log = f"Light/motion sensor {self._where}{self._interface_log_text} has timeout set to {self._motion_timeout}."  # pylint: disable=line-too-long

    @property
    def message_type(self):
        return self._type

    @property
    def brightness_preset(self):
        return self._brightness_preset

    @property
    def brightness(self):
        return self._brightness

    @property
    def transition(self):
        return self._transition

    @property
    def is_on(self) -> bool:
        return self._state is not None and 0 < self._state < 32

    @property
    def is_sensor(self) -> bool:
        return (
            self._state == 34
            or (self._dimension is not None and self._dimension in (5, 6, 7))
            or self._type in (
                MESSAGE_TYPE_MOTION,
                MESSAGE_TYPE_ILLUMINANCE,
                MESSAGE_TYPE_PIR_SENSITIVITY,
                MESSAGE_TYPE_MOTION_TIMEOUT,
            )
        )

    @property
    def timer(self):
        return self._timer

    @property
    def blinker(self):
        return self._blinker

    @property
    def illuminance(self):
        return self._illuminance

    @property
    def motion(self) -> bool:
        return self._motion

    @property
    def pir_sensitivity(self):
        return self._pir_sensitivity

    @property
    def motion_timeout(self) -> datetime.timedelta:
        return self._motion_timeout


class OWNAutomationEvent(OWNEvent):
    def __init__(self, data):
        super().__init__(data)

        self._state = None
        self._position = None
        self._priority = None
        self._info = None
        self._is_opening = None
        self._is_closing = None
        self._is_closed = None

        if self._what is not None and self._what != 1000:
            self._state = self._what

        if self._dimension is not None:
            if self._dimension == 10:
                try:
                    self._state = (
                        int(self._dimension_value[0])
                        if len(self._dimension_value) > 0 and self._dimension_value[0] != ""
                        else None
                    )
                    self._position = (
                        int(self._dimension_value[1])
                        if len(self._dimension_value) > 1 and self._dimension_value[1] != ""
                        else None
                    )
                    self._priority = (
                        int(self._dimension_value[2])
                        if len(self._dimension_value) > 2 and self._dimension_value[2] != ""
                        else None
                    )
                    self._info = (
                        int(self._dimension_value[3])
                        if len(self._dimension_value) > 3 and self._dimension_value[3] != ""
                        else None
                    )
                except (ValueError, IndexError, TypeError):
                    pass

        if self._state == 0:
            self._human_readable_log = (
                f"Cover {self._where}{self._interface_log_text} stopped."
            )
            self._is_opening = False
            self._is_closing = False
        elif self._state == 10:
            self._is_opening = False
            self._is_closing = False
            if self._position == 0:
                self._human_readable_log = (
                    f"Cover {self._where}{self._interface_log_text} is closed."
                )
                self._is_closed = True
            else:
                self._human_readable_log = f"Cover {self._where}{self._interface_log_text} is opened at {self._position}%."
                self._is_closed = False
        else:
            if self._state == 1:
                self._human_readable_log = (
                    f"Cover {self._where}{self._interface_log_text} is opening."
                )
                self._is_opening = True
                self._is_closing = False
            elif self._state == 11 or self._state == 13:
                self._human_readable_log = f"Cover {self._where}{self._interface_log_text} is opening from initial position {self._position}."  # pylint: disable=line-too-long
                self._is_opening = True
                self._is_closing = False
                self._is_closed = False
            elif self._state == 2:
                self._human_readable_log = (
                    f"Cover {self._where}{self._interface_log_text} is closing."
                )
                self._is_closing = True
                self._is_opening = False
            elif self._state == 12 or self._state == 14:
                self._human_readable_log = f"Cover {self._where}{self._interface_log_text} is closing from initial position {self._position}."  # pylint: disable=line-too-long
                self._is_closing = True
                self._is_opening = False
                self._is_closed = False

    @property
    def state(self):
        return self._state

    @property
    def is_opening(self):
        return self._is_opening

    @property
    def is_closing(self):
        return self._is_closing

    @property
    def is_closed(self):
        return self._is_closed

    @property
    def current_position(self):
        return self._position


class OWNHeatingEvent(OWNEvent):
    def __init__(self, data):
        super().__init__(data)

        self._type = None

        self._zone = (
            int(self._where[1:]) if self._where.startswith("#") else int(self._where)
        )
        if self._zone == 0 and self._where_param:
            self._zone = int(self._where_param[0])
        self._sensor = None
        if self._zone > 99:
            self._sensor = int(str(self._zone)[:1])
            self._zone = int(str(self._zone)[1:])
        self._actuator = None

        self._mode = None
        self._mode_name = None
        self._set_temperature = None
        self._local_offset = None
        self._local_set_temperature = None
        self._measured_temperature = None
        self._secondary_temperature = None
        self._measured_humidity = None

        self._is_active = None
        self._is_heating = None
        self._is_cooling = None

        self._fan_on = None
        self._fan_speed = None
        self._cooling_fan_on = None
        self._cooling_fan_speed = None

        _valve_active_states = ["1", "2", "6", "7", "8"]
        _actuator_active_states = ["1", "2", "6", "7", "8", "9"]

        if self._what is not None:
            self._mode = int(self._what)
            if self._mode in [103, 203, 303, 102, 202, 302]:
                self._type = MESSAGE_TYPE_MODE
                self._mode_name = CLIMATE_MODE_OFF
                self._human_readable_log = (
                    f"Zone {self._zone}'s mode is set to '{self._mode_name}'"
                )
            elif (
                self._mode in [0, 210, 211, 215]
                or (self._mode >= 2101 and self._mode <= 2103)
                or (self._mode >= 2201 and self._mode <= 2216)
            ):
                self._type = MESSAGE_TYPE_MODE
                self._mode_name = CLIMATE_MODE_COOL
                self._human_readable_log = (
                    f"Zone {self._zone}'s mode is set to '{self._mode_name}'"
                )
            elif (
                self._mode in [1, 110, 111, 115]
                or (self._mode >= 1101 and self._mode <= 1103)
                or (self._mode >= 1201 and self._mode <= 1216)
            ):
                self._type = MESSAGE_TYPE_MODE
                self._mode_name = CLIMATE_MODE_HEAT
                self._human_readable_log = (
                    f"Zone {self._zone}'s mode is set to '{self._mode_name}'"
                )
            elif (
                self._mode in [310, 311, 315]
                or (self._mode >= 23001 and self._mode <= 23255)
                or (self._mode >= 13001 and self._mode <= 13255)
            ):
                self._type = MESSAGE_TYPE_MODE
                self._mode_name = CLIMATE_MODE_AUTO
                self._human_readable_log = (
                    f"Zone {self._zone}'s mode is set to '{self._mode_name}'"
                )
            elif self._mode == 20:
                self._mode_name = None
                self._human_readable_log = (
                    f"Zone {self._zone}'s remote control is disabled"
                )
            elif self._mode == 21:
                self._mode_name = None
                self._human_readable_log = (
                    f"Zone {self._zone}'s remote control is enabled"
                )
            else:
                self._mode_name = None
                self._human_readable_log = f"Zone {self._zone}'s mode is unknown"

            if (
                self._type == MESSAGE_TYPE_MODE
                and self._what_param
                and self._what_param[0] is not None
            ):
                self._type = MESSAGE_TYPE_MODE_TARGET
                self._set_temperature = float(
                    f"{self._what_param[0][1:3]}.{self._what_param[0][-1]}"
                )
                self._human_readable_log += f" at {self._set_temperature}°C."
            else:
                self._human_readable_log += "."

        if self._dimension == 0:  # Temperature
            if self._sensor is None:
                self._type = MESSAGE_TYPE_MAIN_TEMPERATURE
                self._measured_temperature = float(
                    f"{self._dimension_value[0][1:3]}.{self._dimension_value[0][-1]}"
                )
                self._human_readable_log = f"Zone {self._zone}'s main sensor is reporting a temperature of {self._measured_temperature}°C."  # pylint: disable=line-too-long
            else:
                self._type = MESSAGE_TYPE_SECONDARY_TEMPERATURE
                self._secondary_temperature = float(
                    f"{self._dimension_value[0][1:3]}.{self._dimension_value[0][-1]}"
                )
                self._human_readable_log = f"Zone {self._zone}'s secondary sensor {self._sensor} is reporting a temperature of {self._secondary_temperature}°C."  # pylint: disable=line-too-long

        elif self._dimension == 11:  # Fan speed
            self._type = MESSAGE_TYPE_FAN_SPEED
            _fan_mode = int(self._dimension_value[0])
            if _fan_mode < 4:
                self._fan_on = True
                self._is_active = True
                if _fan_mode > 0:
                    self._fan_speed = _fan_mode
                    self._human_readable_log = (
                        f"Zone {self._zone}'s fan is on at speed {self._fan_speed}."
                    )
                else:
                    self._fan_speed = 0
                    self._human_readable_log = (
                        f"Zone {self._zone}'s fan is on at 'Auto' speed."
                    )
            else:
                self._fan_on = False
                self._is_active = False
                self._fan_speed = None
                self._human_readable_log = f"Zone {self._zone}'s fan is off."

        elif self._dimension == 12:  # Local set temperature (set+offset)
            self._type = MESSAGE_TYPE_LOCAL_TARGET_TEMPERATURE
            self._local_set_temperature = float(
                f"{self._dimension_value[0][1:3]}.{self._dimension_value[0][-1]}"
            )
            self._human_readable_log = f"Zone {self._zone}'s local target temperature is set to {self._local_set_temperature}°C."  # pylint: disable=line-too-long

        elif self._dimension == 13:  # Local offset
            self._type = MESSAGE_TYPE_LOCAL_OFFSET
            if (
                self._dimension_value[0] == "0"
                or self._dimension_value[0] == "00"
                or self._dimension_value[0] == "4"
                or self._dimension_value[0] == "5"
            ):
                self._local_offset = 0
            elif self._dimension_value[0].startswith("0"):
                self._local_offset = int(f"{self._dimension_value[0][1:]}")
            else:
                self._local_offset = -int(f"{self._dimension_value[0][1:]}")
            self._human_readable_log = (
                f"Zone {self._zone}'s local offset is set to {self._local_offset}°C."
            )

        elif self._dimension == 14:  # Set temperature
            self._type = MESSAGE_TYPE_TARGET_TEMPERATURE
            self._set_temperature = float(
                f"{self._dimension_value[0][1:3]}.{self._dimension_value[0][-1]}"
            )
            self._human_readable_log = f"Zone {self._zone}'s target temperature is set to {self._set_temperature}°C."  # pylint: disable=line-too-long

        elif self._dimension == 19:  # Valves status
            self._type = MESSAGE_TYPE_ACTION
            self._is_cooling = self._dimension_value[0] in _valve_active_states
            self._is_heating = self._dimension_value[1] in _valve_active_states
            self._is_active = self._is_cooling | self._is_heating
            # Handle cooling valve status relative to fan speed/status
            _cooling_value = int(self._dimension_value[0])
            if _cooling_value == 0:
                self._human_readable_log = f"Zone {self._zone}'s cooling valve is off"
            elif _cooling_value == 1:
                self._human_readable_log = f"Zone {self._zone}'s cooling valve is on"
            elif _cooling_value == 2:
                self._human_readable_log = (
                    f"Zone {self._zone}'s cooling valve is opened"
                )
            elif _cooling_value == 3:
                self._human_readable_log = (
                    f"Zone {self._zone}'s cooling valve is closed"
                )
            elif _cooling_value == 4:
                self._human_readable_log = (
                    f"Zone {self._zone}'s cooling valve is stopped"
                )
            elif _cooling_value > 4:
                _fan_mode = _cooling_value - 5
                if _fan_mode > 0:
                    self._cooling_fan_on = True
                    self._is_active = True
                    self._cooling_fan_speed = _fan_mode
                    self._human_readable_log = f"Zone {self._zone}'s cooling fan is on at speed {self._fan_speed}"  # pylint: disable=line-too-long
                else:
                    self._cooling_fan_on = False
                    self._is_active = False
                    self._human_readable_log = f"Zone {self._zone}'s cooling fan is off"
            # Handle heating valve status relative to fan speed/status
            _heating_value = int(self._dimension_value[1])
            if _heating_value == 0:
                self._human_readable_log += "; heating valve is off."
            elif _heating_value == 1:
                self._human_readable_log += "; heating valve is on."
            elif _heating_value == 2:
                self._human_readable_log += "; heating valve is opened."
            elif _heating_value == 3:
                self._human_readable_log += "; heating valve is closed."
            elif _heating_value == 4:
                self._human_readable_log += "; heating valve is stopped."
            elif _heating_value > 4:
                _fan_mode = _heating_value - 5
                if _fan_mode > 0:
                    self._fan_on = True
                    self._is_active = True
                    self._fan_speed = _fan_mode
                    self._human_readable_log += (
                        f"; heating fan is on at speed {self._fan_speed}."
                    )
                else:
                    self._fan_on = False
                    self._is_active = False
                    self._human_readable_log += "; heating fan is off."

        elif self._dimension == 20:  # Actuator status
            self._type = MESSAGE_TYPE_ACTION
            self._is_active = self._dimension_value[0] in _actuator_active_states
            self._actuator = (
                self._where_param[0] if self._where_param[0] is not None else 1
            )
            _value = int(self._dimension_value[0])
            if _value == 0:
                self._human_readable_log = (
                    f"Zone {self._zone}'s actuator {self._actuator} is off."
                )
            elif _value == 1:
                self._human_readable_log = (
                    f"Zone {self._zone}'s actuator {self._actuator} is on."
                )
            elif _value == 2:
                self._human_readable_log = (
                    f"Zone {self._zone}'s actuator {self._actuator} is opened."
                )
            elif _value == 3:
                self._human_readable_log = (
                    f"Zone {self._zone}'s actuator {self._actuator} is closed."
                )
            elif _value == 4:
                self._human_readable_log = (
                    f"Zone {self._zone}'s actuator {self._actuator} is stopped."
                )
            elif _value > 4:
                _fan_mode = _value - 5
                if _fan_mode > 0:
                    self._fan_on = True
                    self._is_active = True
                    if _fan_mode < 4:
                        self._fan_speed = _fan_mode
                        self._human_readable_log = (
                            f"Zone {self._zone}'s fan is on at speed {self._fan_speed}."
                        )
                    else:
                        self._human_readable_log = (
                            f"Zone {self._zone}'s fan is on at 'Auto' speed."
                        )
                else:
                    self._fan_on = False
                    self._is_active = False
                    self._human_readable_log = f"Zone {self._zone}'s fan is off."

        elif self._dimension == 60:  # Humidity
            self._type = MESSAGE_TYPE_MAIN_HUMIDITY
            self._measured_humidity = float(self._dimension_value[0])
            self._human_readable_log = f"Zone {self._zone}'s main sensor is reporting a humidity of {self._measured_humidity}%."  # pylint: disable=line-too-long

    @property
    def unique_id(self) -> str:
        """The ID of the subject of this message"""
        if self._zone == 0:
            return f"{self._who}-#0"
        elif self._sensor is not None:
            return f"{self._who}-{self._where}"
        else:
            return f"{self._who}-{self._zone}"

    @property
    def message_type(self):
        return self._type

    @property
    def zone(self) -> int:
        return self._zone

    @property
    def mode(self) -> str:
        return self._mode_name

    def is_active(self) -> bool:
        return self._is_active

    def is_heating(self) -> bool:
        return self._is_heating

    def is_cooling(self) -> bool:
        return self._is_cooling

    @property
    def main_temperature(self) -> float:
        return self._measured_temperature

    @property
    def main_humidity(self) -> float:
        return self._measured_humidity

    @property
    def secondary_temperature(self):
        return [self._sensor, self._secondary_temperature]

    @property
    def set_temperature(self) -> float:
        return self._set_temperature

    @property
    def local_offset(self) -> int:
        return self._local_offset

    @property
    def local_set_temperature(self) -> float:
        return self._local_set_temperature

    @property
    def fan_speed(self):
        return self._fan_speed

    @property
    def fan_on(self):
        return self._fan_on


class OWNAlarmEvent(OWNEvent):
    def __init__(self, data):
        super().__init__(data)

        self._state_code = int(self._what)
        self._state = None
        self._system = False
        self._zone = None
        self._sensor = None

        if self._where == "*":
            self._system = True
            self._human_readable_log = "System is reporting: "
        elif self._where.startswith("#"):
            self._zone = self._where[1:]
            if self._zone == "12":
                self._zone = "c"
            elif self._zone == "15":
                self._zone = "f"
            self._human_readable_log = f"Zone {self._zone} is reporting: "
        elif len(self._where) > 1:
            self._zone = int(self._where[0])
            self._sensor = int(self._where[1:])
            if self._zone == 0:
                self._human_readable_log = (
                    f"Device {self._sensor} in input zone is reporting: "
                )
            else:
                self._human_readable_log = (
                    f"Sensor {self._sensor} in zone {self._zone} is reporting: "
                )
        else:
            self._system = True
            self._human_readable_log = "Control panel is reporting: "

        if self._state_code == 0:
            self._state = "maintenance"
        elif self._state_code == 1:
            self._state = "activation"
        elif self._state_code == 2:
            self._state = "deactivation"
        elif self._state_code == 3:
            self._state = "delay end"
        elif self._state_code == 4:
            self._state = "system battery fault"
        elif self._state_code == 5:
            self._state = "battery ok"
        elif self._state_code == 6:
            self._state = "no network"
        elif self._state_code == 7:
            self._state = "network present"
        elif self._state_code == 8:
            self._state = "engage"
        elif self._state_code == 9:
            self._state = "disengage"
        elif self._state_code == 10:
            self._state = "battery unloads"
        elif self._state_code == 11:
            self._state = "active zone"
        elif self._state_code == 12:
            self._state = "technical alarm"
        elif self._state_code == 13:
            self._state = "reset technical alarm"
        elif self._state_code == 14:
            self._state = "no reception"
        elif self._state_code == 15:
            self._state = "intrusion alarm"
        elif self._state_code == 16:
            self._state = "tampering"
        elif self._state_code == 17:
            self._state = "anti-panic alarm"
        elif self._state_code == 18:
            self._state = "non-active zone"
        elif self._state_code == 26:
            self._state = "start programming"
        elif self._state_code == 27:
            self._state = "stop programming"
        elif self._state_code == 31:
            self._state = "silent alarm"

        self._human_readable_log = f"{self._human_readable_log}'{self._state}'."

    @property
    def general(self):
        return self._system

    @property
    def zone(self):
        return self._zone

    @property
    def sensor(self):
        return self._sensor

    @property
    def is_active(self):
        return self._state_code == 1 or self._state_code == 11

    @property
    def is_engaged(self):
        return self._state_code == 8

    @property
    def is_disarmed(self):
        return self._state_code in (0, 2, 9)

    @property
    def is_armed_away(self):
        return self._state_code in (1, 8)

    @property
    def is_armed_home(self):
        return self._state_code in (11,)

    @property
    def state_name(self):
        return self._state

    @property
    def state_code(self):
        return self._state_code

    @property
    def is_alarm(self):
        return (
            self._state_code == 12
            or self._state_code == 15
            or self._state_code == 16
            or self._state_code == 17
            or self._state_code == 31
        )


class OWNAuxEvent(OWNEvent):
    def __init__(self, data):
        super().__init__(data)

        self._channel = self._where

        self._state = self._what
        if self._state == 0:
            self._human_readable_log = (
                f"Auxilliary channel {self._channel} is set to 'OFF'."
            )
        elif self._state == 1:
            self._human_readable_log = (
                f"Auxilliary channel {self._channel} is set to 'ON'."
            )
        elif self._state == 2:
            self._human_readable_log = (
                f"Auxilliary channel {self._channel} is set to 'TOGGLE'."
            )
        elif self._state == 3:
            self._human_readable_log = (
                f"Auxilliary channel {self._channel} is set to 'STOP'."
            )
        elif self._state == 4:
            self._human_readable_log = (
                f"Auxilliary channel {self._channel} is set to 'UP'."
            )
        elif self._state == 5:
            self._human_readable_log = (
                f"Auxilliary channel {self._channel} is set to 'DOWN'."
            )
        elif self._state == 6:
            self._human_readable_log = (
                f"Auxilliary channel {self._channel} is set to 'ENABLED'."
            )
        elif self._state == 7:
            self._human_readable_log = (
                f"Auxilliary channel {self._channel} is set to 'DISABLED'."
            )
        elif self._state == 8:
            self._human_readable_log = (
                f"Auxilliary channel {self._channel} is set to 'RESET_GEN'."
            )
        elif self._state == 9:
            self._human_readable_log = (
                f"Auxilliary channel {self._channel} is set to 'RESET_BI'."
            )
        elif self._state == 10:
            self._human_readable_log = (
                f"Auxilliary channel {self._channel} is set to 'RESET_TRI'."
            )

    @property
    def channel(self):
        return self._channel

    @property
    def state_code(self):
        return self._state

    @property
    def is_on(self):
        return self._state == 1


class OWNGatewayEvent(OWNEvent):
    def __init__(self, data):
        super().__init__(data)

        self._year = None
        self._month = None
        self._day = None
        self._hour = None
        self._minute = None
        self._second = None
        self._timezone = None

        self._time = None
        self._date = None
        self._datetime = None

        self._ip_address = None
        self._netmask = None
        self._mac_address = None

        self._device_type = None
        self._firmware_version = None

        self._uptime = None

        self._kernel_version = None
        self._distribution_version = None

        if self._dimension == 0:
            try:
                self._hour = self._dimension_value[0] if len(self._dimension_value) > 0 else "00"
                self._minute = self._dimension_value[1] if len(self._dimension_value) > 1 else "00"
                self._second = self._dimension_value[2] if len(self._dimension_value) > 2 else "00"
                # Timezone is sometimes missing from messages, assuming UTC
                if len(self._dimension_value) > 3 and self._dimension_value[3] != "":
                    self._timezone = (
                        f"+{self._dimension_value[3][1:]}:00"
                        if self._dimension_value[3][0] == "0"
                        else f"-{self._dimension_value[3][1:]}:00"
                    )
                else:
                    self._timezone = ""
                self._human_readable_log = f"Gateway's internal time is: {self._hour}:{self._minute}:{self._second} UTC {self._timezone}."  # pylint: disable=line-too-long
            except (ValueError, IndexError):
                pass

        elif self._dimension == 1:
            try:
                self._year = self._dimension_value[3] if len(self._dimension_value) > 3 else "2000"
                self._month = self._dimension_value[2] if len(self._dimension_value) > 2 else "1"
                self._day = self._dimension_value[1] if len(self._dimension_value) > 1 else "1"
                self._date = datetime.date(
                    year=int(self._year), month=int(self._month), day=int(self._day)
                )
                self._human_readable_log = (
                    f"Gateway's internal date is: {self._year}-{self._month}-{self._day}."
                )
            except (ValueError, IndexError):
                pass

        elif self._dimension == 10:
            if len(self._dimension_value) >= 4:
                self._ip_address = f"{self._dimension_value[0]}.{self._dimension_value[1]}.{self._dimension_value[2]}.{self._dimension_value[3]}"  # pylint: disable=line-too-long
                self._human_readable_log = f"Gateway's IP address is: {self._ip_address}."

        elif self._dimension == 11:
            if len(self._dimension_value) >= 4:
                self._netmask = f"{self._dimension_value[0]}.{self._dimension_value[1]}.{self._dimension_value[2]}.{self._dimension_value[3]}"  # pylint: disable=line-too-long
                self._human_readable_log = f"Gateway's netmask is: {self._netmask}."

        elif self._dimension == 12:
            if len(self._dimension_value) >= 6:
                try:
                    self._mac_address = f"{int(self._dimension_value[0]):02x}:{int(self._dimension_value[1]):02x}:{int(self._dimension_value[2]):02x}:{int(self._dimension_value[3]):02x}:{int(self._dimension_value[4]):02x}:{int(self._dimension_value[5]):02x}"  # pylint: disable=line-too-long
                    self._human_readable_log = f"Gateway's MAC address is: {self._mac_address}."
                except (ValueError, IndexError):
                    pass

        elif self._dimension == 15:
            if len(self._dimension_value) >= 1:
                if self._dimension_value[0] == "2":
                    self._device_type = "MHServer"
                elif self._dimension_value[0] == "4":
                    self._device_type = "MH200"
                elif self._dimension_value[0] == "6":
                    self._device_type = "F452"
                elif self._dimension_value[0] == "7":
                    self._device_type = "F452V"
                elif self._dimension_value[0] == "11":
                    self._device_type = "MHServer2"
                elif self._dimension_value[0] == "13":
                    self._device_type = "H4684"
                elif self._dimension_value[0] == "200":
                    self._device_type = "F454"
                else:
                    self._device_type = f"Unknown ({self._dimension_value[0]})"
                self._human_readable_log = f"Gateway device type is: {self._device_type}."

        elif self._dimension == 16:
            if len(self._dimension_value) >= 3:
                self._firmware_version = f"{self._dimension_value[0]}.{self._dimension_value[1]}.{self._dimension_value[2]}"  # pylint: disable=line-too-long
                self._human_readable_log = (
                    f"Gateway's firmware version is: {self._firmware_version}."
                )

        elif self._dimension == 19:
            try:
                self._uptime = datetime.timedelta(
                    days=int(self._dimension_value[0]) if len(self._dimension_value) > 0 else 0,
                    hours=int(self._dimension_value[1]) if len(self._dimension_value) > 1 else 0,
                    minutes=int(self._dimension_value[2]) if len(self._dimension_value) > 2 else 0,
                    seconds=int(self._dimension_value[3]) if len(self._dimension_value) > 3 else 0,
                )
                self._human_readable_log = f"Gateway's uptime is: {self._uptime}."
            except (ValueError, IndexError):
                pass

        elif self._dimension == 22:
            try:
                self._hour = self._dimension_value[0] if len(self._dimension_value) > 0 else "00"
                self._minute = self._dimension_value[1] if len(self._dimension_value) > 1 else "00"
                self._second = self._dimension_value[2] if len(self._dimension_value) > 2 else "00"
                # Timezone is sometimes missing from messages, assuming UTC
                if len(self._dimension_value) > 3 and self._dimension_value[3] != "":
                    self._timezone = (
                        f"+{self._dimension_value[3][1:]}:00"
                        if self._dimension_value[3][0] == "0"
                        else f"-{self._dimension_value[3][1:]}:00"
                    )
                else:
                    self._timezone = ""
                self._day = self._dimension_value[5] if len(self._dimension_value) > 5 else "01"
                self._month = self._dimension_value[6] if len(self._dimension_value) > 6 else "01"
                self._year = self._dimension_value[7] if len(self._dimension_value) > 7 else "2000"
                self._datetime = datetime.datetime.fromisoformat(
                    f"{self._year}-{self._month}-{self._day}*{self._hour}:{self._minute}:{self._second}{self._timezone}"  # pylint: disable=line-too-long
                )
                self._human_readable_log = (
                    f"Gateway's internal datetime is: {self._datetime}."
                )
            except (ValueError, IndexError):
                pass

        elif self._dimension == 23:
            if len(self._dimension_value) >= 3:
                self._kernel_version = f"{self._dimension_value[0]}.{self._dimension_value[1]}.{self._dimension_value[2]}"  # pylint: disable=line-too-long
                self._human_readable_log = (
                    f"Gateway's kernel version is: {self._kernel_version}."
                )

        elif self._dimension == 24:
            if len(self._dimension_value) >= 3:
                self._distribution_version = f"{self._dimension_value[0]}.{self._dimension_value[1]}.{self._dimension_value[2]}"  # pylint: disable=line-too-long
                self._human_readable_log = (
                    f"Gateway's distribution version is: {self._distribution_version}."
                )


class OWNCENEvent(OWNEvent):
    def __init__(self, data):
        super().__init__(data)

        try:
            self._state = self._what_param[0]
        except IndexError:
            self._state = None
        self.push_button = self._what
        self.object = self._where

        if self._state is None:
            self._human_readable_log = f"Button {self.push_button} of CEN object {self.object}{self._interface_log_text} has been pressed."  # pylint: disable=line-too-long
        elif int(self._state) == 3:
            self._human_readable_log = f"Button {self.push_button} of CEN object {self.object}{self._interface_log_text} is being held pressed."  # pylint: disable=line-too-long
        elif int(self._state) == 1:
            self._human_readable_log = f"Button {self.push_button} of CEN object {self.object}{self._interface_log_text} has been released after a short press."  # pylint: disable=line-too-long
        elif int(self._state) == 2:
            self._human_readable_log = f"Button {self.push_button} of CEN object {self.object}{self._interface_log_text} has been released after a long press."  # pylint: disable=line-too-long

    @property
    def is_pressed(self):
        return self._state is None

    @property
    def is_held(self):
        return int(self._state) == 3

    @property
    def is_released_after_short_press(self):
        return int(self._state) == 1

    @property
    def is_released_after_long_press(self):
        return int(self._state) == 2


class OWNSceneEvent(OWNEvent):
    def __init__(self, data):
        super().__init__(data)

        self._scene = self._where
        self._state = self._what

        if self._state == 1:
            _status = "started"
        elif self._state == 2:
            _status = "stoped"
        elif self._state == 3:
            _status = "enabled"
        elif self._state == 4:
            _status = "disabled"
        else:
            _status = f"unknonwn ({self._state})"

        self._human_readable_log = f"Scene {self._scene} is {_status}."

    @property
    def scenario(self):
        return self._scene

    @property
    def state(self):
        return self._state

    @property
    def is_on(self):
        if self._state == 1:
            return True
        elif self._state == 2:
            return False
        else:
            return None

    @property
    def is_enabled(self):
        if self._state == 3:
            return True
        elif self._state == 4:
            return False
        else:
            return None


class OWNEnergyEvent(OWNEvent):
    def __init__(self, data):
        super().__init__(data)

        if (
            not self._where
            or (
                not self._where.startswith("5")
                and not self._where.startswith("7")
                and not self._where.startswith("1")
            )
        ):
            return None

        self._type = None
        self._sensor = self._where[1:] if len(self._where) > 1 else self._where
        self._active_power = 0
        self._total_consumption = 0
        self._hourly_consumption = dict()
        self._daily_consumption = dict()
        self._current_day_partial_consumption = 0
        self._monthly_consumption = dict()
        self._current_month_partial_consumption = 0

        if self._dimension is not None:
            if self._dimension == 113:
                self._type = MESSAGE_TYPE_ACTIVE_POWER
                try:
                    self._active_power = int(self._dimension_value[0])
                    self._human_readable_log = f"Sensor {self._sensor} is reporting an active power draw of {self._active_power} W."  # pylint: disable=line-too-long
                except (ValueError, IndexError, TypeError):
                    pass
            elif self._dimension == 511:
                _now = datetime.date.today()
                try:
                    _raw_message_date = datetime.date(
                        _now.year,
                        int(self._dimension_param[0]),
                        int(self._dimension_param[1]),
                    )
                    if _raw_message_date > _now:
                        _message_date = datetime.date(
                            _now.year - 1,
                            int(self._dimension_param[0]),
                            int(self._dimension_param[1]),
                        )
                    else:
                        _message_date = datetime.date(
                            _now.year,
                            int(self._dimension_param[0]),
                            int(self._dimension_param[1]),
                        )
                except (ValueError, IndexError, TypeError):
                    return None

                try:
                    val0 = int(self._dimension_value[0])
                    val1 = int(self._dimension_value[1])
                    if val0 != 25:
                        self._type = MESSAGE_TYPE_HOURLY_CONSUMPTION
                        self._hourly_consumption["date"] = _message_date
                        self._hourly_consumption["hour"] = val0 - 1
                        self._hourly_consumption["value"] = val1
                        self._human_readable_log = f"Sensor {self._sensor} is reporting a power consumption of {val1} Wh for {_message_date} at {val0 - 1}."  # pylint: disable=line-too-long
                    else:
                        self._type = MESSAGE_TYPE_DAILY_CONSUMPTION
                        self._daily_consumption["date"] = _message_date
                        self._daily_consumption["value"] = val1
                        self._human_readable_log = f"Sensor {self._sensor} is reporting a power consumption of {val1} Wh for {_message_date}."  # pylint: disable=line-too-long
                except (ValueError, IndexError, TypeError):
                    return None
            elif self._dimension == 513 or self._dimension == 514:
                _now = datetime.date.today()
                try:
                    _raw_message_date = datetime.date(
                        _now.year, int(self._dimension_param[0]), 1
                    )
                    val0 = int(self._dimension_value[0])
                    val1 = int(self._dimension_value[1])
                    if self._dimension == 513 and _raw_message_date > _now:
                        _message_date = datetime.date(
                            _now.year - 1,
                            int(self._dimension_param[0]),
                            val0,
                        )
                    elif self._dimension == 514:
                        if _raw_message_date > _now:
                            _message_date = datetime.date(
                                _now.year - 2,
                                int(self._dimension_param[0]),
                                val0,
                            )
                        else:
                            _message_date = datetime.date(
                                _now.year - 1,
                                int(self._dimension_param[0]),
                                val0,
                            )
                    else:
                        _message_date = datetime.date(
                            _now.year,
                            int(self._dimension_param[0]),
                            val0,
                        )
                    self._type = MESSAGE_TYPE_DAILY_CONSUMPTION
                    self._daily_consumption["date"] = _message_date
                    self._daily_consumption["value"] = val1
                    self._human_readable_log = f"Sensor {self._sensor} is reporting a power consumption of {val1} Wh for {_message_date}."  # pylint: disable=line-too-long
                except (ValueError, IndexError, TypeError):
                    return None
            elif self._dimension == 51:
                self._type = MESSAGE_TYPE_ENERGY_TOTALIZER
                try:
                    self._total_consumption = int(self._dimension_value[0])
                    self._human_readable_log = f"Sensor {self._sensor} is reporting a total power consumption of {self._total_consumption} Wh."  # pylint: disable=line-too-long
                except (ValueError, IndexError, TypeError):
                    pass
            elif self._dimension == 54:
                self._type = MESSAGE_TYPE_CURRENT_DAY_CONSUMPTION
                try:
                    self._current_day_partial_consumption = int(self._dimension_value[0])
                    self._human_readable_log = f"Sensor {self._sensor} is reporting a power consumption of {self._current_day_partial_consumption} Wh up to now today."  # pylint: disable=line-too-long
                except (ValueError, IndexError, TypeError):
                    pass
            elif self._dimension == 52:
                self._type = MESSAGE_TYPE_MONTHLY_CONSUMPTION
                try:
                    _message_date = datetime.date(
                        int(f"20{self._dimension_param[0]}"), int(self._dimension_param[1]), 1
                    )
                    self._monthly_consumption["date"] = _message_date
                    self._monthly_consumption["value"] = int(self._dimension_value[0])
                    self._human_readable_log = f"Sensor {self._sensor} is reporting a power consumption of {self._monthly_consumption['value']} Wh for {self._monthly_consumption['date'].strftime('%B %Y')}."  # pylint: disable=line-too-long
                except (ValueError, IndexError, TypeError):
                    pass
            elif self._dimension == 53:
                self._type = MESSAGE_TYPE_CURRENT_MONTH_CONSUMPTION
                try:
                    self._current_month_partial_consumption = int(self._dimension_value[0])
                    self._human_readable_log = f"Sensor {self._sensor} is reporting a power consumption of {self._current_month_partial_consumption} Wh up to now this month."  # pylint: disable=line-too-long
                except (ValueError, IndexError, TypeError):
                    pass

    @property
    def message_type(self):
        return self._type

    @property
    def active_power(self):
        return self._active_power

    @property
    def total_consumption(self):
        return self._total_consumption

    @property
    def sensor(self):
        return self._sensor

    @property
    def hourly_consumption(self):
        return self._hourly_consumption

    @property
    def daily_consumption(self):
        return self._daily_consumption

    @property
    def current_day_partial_consumption(self):
        return self._current_day_partial_consumption

    @property
    def monthly_consumption(self):
        return self._monthly_consumption

    @property
    def current_month_partial_consumption(self):
        return self._current_month_partial_consumption

    @property
    def human_readable_log(self):
        return self._human_readable_log


class OWNDryContactEvent(OWNEvent):
    def __init__(self, data):
        super().__init__(data)

        self._state = 1 if self._what == 31 else 0
        try:
            self._detection = (
                int(self._what_param[0])
                if self._what_param and len(self._what_param) > 0
                else 1
            )
        except (ValueError, IndexError, TypeError):
            self._detection = 1
        where_str = str(self._where) if self._where is not None else ""
        self._sensor = (
            where_str[1:]
            if len(where_str) > 1 and where_str.startswith("3")
            else where_str
        )

        if self._detection == 1:
            self._human_readable_log = (
                f"Sensor {self._sensor} detected {'ON' if self._state == 1 else 'OFF'}."
            )
        else:
            self._human_readable_log = (
                f"Sensor {self._sensor} reported {'ON' if self._state == 1 else 'OFF'}."
            )

    @property
    def is_on(self):
        return self._state == 1

    @property
    def sensor(self):
        return self._sensor

    @property
    def is_detection(self):
        return self._detection == 1

    @property
    def human_readable_log(self):
        return self._human_readable_log


class OWNCENPlusEvent(OWNEvent):
    def __init__(self, data):
        super().__init__(data)

        self._state = self._what
        try:
            self.push_button = (
                int(self._what_param[0])
                if self._what_param and len(self._what_param) > 0
                else 0
            )
        except (ValueError, IndexError, TypeError):
            self.push_button = 0

        if self._where:
            self.object = (
                self._where[1:]
                if self._where.startswith("2") and len(self._where) > 1
                else self._where
            )
        else:
            self.object = ""

        if self._state == 21:
            self._human_readable_log = f"Button {self.push_button} of CEN+ object {self.object} has been pressed"  # pylint: disable=line-too-long
        elif self._state == 22:
            self._human_readable_log = f"Button {self.push_button} of CEN+ object {self.object} is being held pressed"  # pylint: disable=line-too-long
        elif self._state == 23:
            self._human_readable_log = f"Button {self.push_button} of CEN+ object {self.object} is still being held pressed"  # pylint: disable=line-too-long
        elif self._state == 24:
            self._human_readable_log = f"Button {self.push_button} of CEN+ object {self.object} has been released"  # pylint: disable=line-too-long
        elif self._state == 25:
            self._human_readable_log = f"Button {self.push_button} of CEN+ object {self.object} has been slowly rotated clockwise"  # pylint: disable=line-too-long
        elif self._state == 26:
            self._human_readable_log = f"Button {self.push_button} of CEN+ object {self.object} has been quickly rotated clockwise"  # pylint: disable=line-too-long
        elif self._state == 27:
            self._human_readable_log = f"Button {self.push_button} of CEN+ object {self.object} has been slowly rotated counter-clockwise"  # pylint: disable=line-too-long
        elif self._state == 28:
            self._human_readable_log = f"Button {self.push_button} of CEN+ object {self.object} has been quickly rotated counter-clockwise"  # pylint: disable=line-too-long
        else:
            self._human_readable_log = f"Button {self.push_button} of CEN+ object {self.object} state is {self._state}."

    @property
    def is_short_pressed(self):
        return self._state == 21

    @property
    def is_held(self):
        return self._state == 22

    @property
    def is_still_held(self):
        return self._state == 23

    @property
    def is_released(self):
        return self._state == 24

    @property
    def is_slowly_turned_cw(self):
        return self._state == 25

    @property
    def is_quickly_turned_cw(self):
        return self._state == 26

    @property
    def is_slowly_turned_ccw(self):
        return self._state == 27

    @property
    def is_quickly_turned_ccw(self):
        return self._state == 28

    @property
    def human_readable_log(self):
        return self._human_readable_log


class OWNSoundEvent(OWNEvent):
    def __init__(self, data):
        super().__init__(data)

        self._state = self._what
        self._zone = self._where
        self._is_source_event = (
            len(self._zone) == 3
            and self._zone.startswith("10")
            and self._zone != "100"
        )
        self._volume = None
        self._source_id = None

        if self._state is not None:
            if self._is_source_event:
                self._source_id = str(int(self._zone) - 100)
                if self._state in (0, 3):
                    self._human_readable_log = f"Audio Source {self._source_id} is switched ON."
                elif self._state in (10, 13):
                    self._human_readable_log = f"Audio Source {self._source_id} is switched OFF."
                else:
                    self._human_readable_log = f"Audio Source {self._source_id} received command: {self._state}."
            else:
                if self._state in (0, 3):
                    self._human_readable_log = f"Audio Zone {self._zone} is switched ON."
                elif self._state in (10, 13):
                    self._human_readable_log = f"Audio Zone {self._zone} is switched OFF."
                else:
                    self._human_readable_log = f"Audio Zone {self._zone} received command: {self._state}."
        elif self._dimension is not None:
            if self._dimension == 1:
                try:
                    self._volume = int(self._dimension_value[0])
                    self._human_readable_log = f"Audio Zone {self._zone} volume is set to {self._volume}."
                except (ValueError, IndexError):
                    pass

    @property
    def is_on(self):
        return self._state in (0, 3)

    @property
    def is_off(self):
        return self._state in (10, 13)

    @property
    def is_source_event(self):
        return self._is_source_event

    @property
    def source_id(self):
        return self._source_id if self._is_source_event else None

    @property
    def zone(self):
        return self._zone

    @property
    def volume(self):
        return self._volume


class OWNCommand(OWNMessage):
    """
    This class is a subclass of messages.
    All messages sent during a command session are commands.
    Dividing this in a subclass provides better clarity
    """

    @classmethod
    def parse(cls, data):
        _match = re.match(r"^\*#?(?P<who>\d+)(?:\*.+)?##$", data)

        if _match:
            _who = int(_match.group("who"))

            if _who == 0:
                return cls(data)
            elif _who == 1:
                return OWNLightingCommand(data)
            elif _who == 2:
                return OWNAutomationCommand(data)
            elif _who == 3:  # Charges / Loads ?
                return cls(data)
            elif _who == 4:
                return OWNHeatingCommand(data)
            elif _who == 5:
                return OWNAlarmCommand(data)
            elif _who == 6:  # VDES
                return cls(data)
            elif _who == 7:
                return cls(data)
            elif _who == 9:
                return (
                    OWNStatusRequest(data)
                    if cls._STATUS_REQUEST.match(data)
                    else cls(data)
                )
            elif _who == 13:
                return OWNGatewayCommand(data)
            elif _who == 14:
                return cls(data)
            elif _who == 15:
                return cls(data)
            elif _who == 16:
                return OWNSoundCommand(data)
            elif _who == 17:
                return cls(data)
            elif _who == 18:
                return OWNEnergyCommand(data)
            elif _who == 22:
                return cls(data)
            elif _who == 24:
                return cls(data)
            elif _who == 25:
                if data.startswith("*#"):
                    return OWNDryContactCommand(data)
                _parts = data.strip("#").split("*")
                _what_part = _parts[2] if len(_parts) > 2 else ""
                _what_code = _what_part.split("#")[0]
                try:
                    _what_int = int(_what_code)
                except ValueError:
                    _what_int = None
                if _what_int is not None and 21 <= _what_int <= 28:
                    return cls(data)
                return OWNDryContactCommand(data)
            elif _who > 1000:
                return cls(data)

        return None


class OWNStatusRequest(OWNCommand):
    """General status request message (e.g. *#5##, *#9##, *#1*WHERE##)."""

    def __init__(self, data):
        super().__init__(data)
        if self._where:
            self._human_readable_log = (
                f"Requesting status for WHO={self._who} WHERE={self._where}."
            )
        else:
            self._human_readable_log = f"Requesting global status for WHO={self._who}."

    @classmethod
    def request(cls, who: int, where: str = None):
        if where is not None and str(where) != "":
            return cls(f"*#{who}*{where}##")
        return cls(f"*#{who}##")


class OWNLightingCommand(OWNCommand):
    @classmethod
    def status(cls, where):
        message = cls(f"*#1*{where}##")
        message._human_readable_log = f"Requesting light or switch {message._where}{message._interface_log_text} status."
        return message

    @classmethod
    def get_brightness(cls, where):
        message = cls(f"*#1*{where}*1##")
        message._human_readable_log = f"Requesting light {message._where}{message._interface_log_text} brightness."
        return message

    @classmethod
    def get_pir_sensitivity(cls, where):
        message = cls(f"*#1*{where}*5##")
        message._human_readable_log = f"Requesting light/motion sensor {message._where}{message._interface_log_text} PIR sensitivity."
        return message

    @classmethod
    def get_illuminance(cls, where):
        message = cls(f"*#1*{where}*6##")
        message._human_readable_log = f"Requesting light/motion sensor {message._where}{message._interface_log_text} illuminance."
        return message

    @classmethod
    def get_motion_timeout(cls, where):
        message = cls(f"*#1*{where}*7##")
        message._human_readable_log = f"Requesting light/motion sensor {message._where}{message._interface_log_text} motion timeout."
        return message

    @classmethod
    def flash(cls, where, _freqency=0.5):
        if _freqency is not None and _freqency >= 0.5 and _freqency <= 5:
            _freqency = round(_freqency * 2) / 2
        else:
            _freqency = 0.5
        _what = int((_freqency / 0.5) + 19)
        message = cls(f"*1*{_what}*{where}##")
        message._human_readable_log = f"Flashing light {message._where}{message._interface_log_text} every {_freqency}s."
        return message

    @classmethod
    def switch_on(cls, where, _transition=None):
        if _transition is not None and _transition >= 0 and _transition <= 255:
            message = cls(f"*1*1#{_transition}*{where}##")
            message._human_readable_log = f"Switching ON light {message._where}{message._interface_log_text} with transition speed {_transition}."
        else:
            message = cls(f"*1*1*{where}##")
            message._human_readable_log = f"Switching ON light or switch {message._where}{message._interface_log_text}."
        return message

    @classmethod
    def switch_off(cls, where, _transition=None):
        if _transition is not None and _transition >= 0 and _transition <= 255:
            message = cls(f"*1*0#{_transition}*{where}##")
            message._human_readable_log = f"Switching OFF light {message._where}{message._interface_log_text} with transition speed {_transition}."
        else:
            message = cls(f"*1*0*{where}##")
            message._human_readable_log = f"Switching OFF light or switch {message._where}{message._interface_log_text}."
        return message

    @classmethod
    def set_brightness(cls, where, _level=30, _transition=0):
        command_level = int(_level) + 100
        transition_speed = _transition if _transition >= 0 and _transition <= 255 else 0
        message = cls(f"*#1*{where}*#1*{command_level}*{transition_speed}##")
        message._human_readable_log = (
            f"Setting light {message._where}{message._interface_log_text} brightness to {_level}% with transition speed {transition_speed}."  # pylint: disable=line-too-long
            if transition_speed > 0
            else f"Setting light {message._where}{message._interface_log_text} brightness to {_level}%."
        )
        return message


class OWNAutomationCommand(OWNCommand):
    @classmethod
    def status(cls, where):
        message = cls(f"*#2*{where}##")
        message._human_readable_log = (
            f"Requesting shutter {message._where}{message._interface_log_text} status."
        )
        return message

    @classmethod
    def get_shutter_status(cls, where):
        message = cls(f"*#2*{where}*10##")
        message._human_readable_log = (
            f"Requesting shutter {message._where}{message._interface_log_text} advanced status (dimension 10)."
        )
        return message

    @classmethod
    def raise_shutter(cls, where):
        message = cls(f"*2*1*{where}##")
        message._human_readable_log = (
            f"Raising shutter {message._where}{message._interface_log_text}."
        )
        return message

    @classmethod
    def lower_shutter(cls, where):
        message = cls(f"*2*2*{where}##")
        message._human_readable_log = (
            f"Lowering shutter {message._where}{message._interface_log_text}."
        )
        return message

    @classmethod
    def stop_shutter(cls, where):
        message = cls(f"*2*0*{where}##")
        message._human_readable_log = (
            f"Stoping shutter {message._where}{message._interface_log_text}."
        )
        return message

    @classmethod
    def set_shutter_level(cls, where, level=30):
        message = cls(f"*#2*{where}*#11#001*{level}##")
        message._human_readable_log = f"Setting shutter {message._where}{message._interface_log_text} position to {level}%."
        return message


class OWNHeatingCommand(OWNCommand):
    @classmethod
    def status(cls, where):
        message = cls(f"*#4*{where}##")
        message._human_readable_log = f"Requesting climate status update for {message._where}{message._interface_log_text}."
        return message

    @classmethod
    def get_temperature(cls, where):
        message = cls(f"*#4*{where}*0##")
        message._human_readable_log = f"Requesting climate status update for {message._where}{message._interface_log_text}."
        return message

    @classmethod
    def set_mode(cls, where, mode: str, standalone=False):
        central_local = re.compile(r"^#0#\d+$")
        if central_local.match(str(where)):
            zone = where
            zone_name = f"zone {int(where.split('#')[-1])}"
        else:
            zone = int(where.split("#")[-1]) if where.startswith("#") else int(where)
            zone_name = f"zone {zone}" if zone > 0 else "general"

            if standalone:
                zone = f"#{zone}" if zone == 0 else str(zone)
            else:
                zone = f"#{zone}"

        mode_name = mode
        if mode == CLIMATE_MODE_OFF:
            mode = 303
        elif mode == CLIMATE_MODE_AUTO:
            mode = 311
        else:
            return None

        message = cls(f"*4*{mode}*{zone}##")
        message._human_readable_log = f"Setting {zone_name} mode to '{mode_name}'."
        return message

    @classmethod
    def turn_off(cls, where, standalone=False):
        return cls.set_mode(where=where, mode=CLIMATE_MODE_OFF, standalone=standalone)

    @classmethod
    def set_temperature(cls, where, temperature: float, mode: str, standalone=False):
        central_local = re.compile(r"^#0#\d+$")
        if central_local.match(str(where)):
            zone = where
            zone_name = f"zone {int(where.split('#')[-1])}"
        else:
            zone = int(where.split("#")[-1]) if where.startswith("#") else int(where)
            zone_name = f"zone {zone}" if zone > 0 else "general"

            if standalone:
                zone = f"#{zone}" if zone == 0 else str(zone)
            else:
                zone = f"#{zone}"

        temperature = round(temperature * 2) / 2
        if temperature < 5.0:
            temperature = 5.0
        elif temperature > 40.0:
            temperature = 40.0
        temperature_print = f"{temperature}"
        temperature = int(temperature * 10)

        mode_name = mode
        if mode == CLIMATE_MODE_HEAT:
            mode = 1
        elif mode == CLIMATE_MODE_COOL:
            mode = 2
        elif mode == CLIMATE_MODE_AUTO:
            mode = 3

        message = cls(f"*#4*{zone}*#14*{temperature:04d}*{mode}##")
        message._human_readable_log = (
            f"Setting {zone_name} to {temperature_print}°C in mode '{mode_name}'."
        )
        return message

    @classmethod
    def set_fan_speed(cls, where, speed: int, standalone=False):
        central_local = re.compile(r"^#0#\d+$")
        if central_local.match(str(where)):
            zone = where
            zone_name = f"zone {int(where.split('#')[-1])}"
        else:
            zone = int(where.split("#")[-1]) if where.startswith("#") else int(where)
            zone_name = f"zone {zone}" if zone > 0 else "general"
            if standalone:
                zone = f"#{zone}" if zone == 0 else str(zone)
            else:
                zone = f"#{zone}"

        speed_val = int(speed)
        message = cls(f"*#4*{zone}*#11*{speed_val}##")
        message._human_readable_log = f"Setting {zone_name} fan speed to {speed_val}."
        return message


class OWNAlarmCommand(OWNCommand):
    @classmethod
    def status(cls, where="0"):
        if where is None or where == "":
            message = cls("*#5##")
            message._human_readable_log = "Querying burglar alarm central status."
            return message
        where_str = str(where)
        if where_str == "0":
            message = cls("*#5*0##")
            message._human_readable_log = "Querying burglar alarm central status."
            return message
        if where_str.startswith("#"):
            target = where_str
        else:
            target = f"#{where_str}"
        message = cls(f"*#5*{target}##")
        message._human_readable_log = f"Querying burglar alarm status for zone {where}."
        return message

    @classmethod
    def disarm(cls, where="0"):
        message = cls(f"*5*2*{where}##")
        message._human_readable_log = f"Disarming burglar alarm for zone {where}."
        return message

    @classmethod
    def arm_away(cls, where="0"):
        message = cls(f"*5*1*{where}##")
        message._human_readable_log = f"Arming burglar alarm (away) for zone {where}."
        return message

    @classmethod
    def arm_home(cls, where="0"):
        message = cls(f"*5*1*{where}##")
        message._human_readable_log = f"Arming burglar alarm (home) for zone {where}."
        return message

    @classmethod
    def trigger(cls, where="0"):
        message = cls(f"*5*17*{where}##")
        message._human_readable_log = (
            f"Triggering panic burglar alarm for zone {where}."
        )
        return message

    @classmethod
    def panic(cls, where="0"):
        return cls.trigger(where=where)


class OWNAVCommand(OWNCommand):
    @classmethod
    def receive_video(cls, where):
        camera_id = where
        if int(where) < 100:
            where = f"40{camera_id}"
        elif int(where) >= 4000 and int(where) < 5000:
            camera_id = where[2:]
        else:
            return None

        message = cls(f"*7*0*{where}##")
        message._human_readable_log = f"Opening video stream for camera {camera_id}."
        return message

    @classmethod
    def close_video(cls):
        message = cls("*7*9**##")
        message._human_readable_log = "Closing video stream."
        return message


class OWNGatewayCommand(OWNCommand):
    def __init__(self, data):
        super().__init__(data)

        self._year = None
        self._month = None
        self._day = None
        self._hour = None
        self._minute = None
        self._second = None
        self._timezone = None

        self._time = None
        self._date = None
        self._datetime = None

        if self._dimension == 0 and self._dimension_value:
            try:
                self._hour = self._dimension_value[0] if len(self._dimension_value) > 0 else "00"
                self._minute = self._dimension_value[1] if len(self._dimension_value) > 1 else "00"
                self._second = self._dimension_value[2] if len(self._dimension_value) > 2 else "00"
                # Timezone is sometimes missing from messages, assuming UTC
                if len(self._dimension_value) > 3 and self._dimension_value[3] != "":
                    self._timezone = (
                        f"+{self._dimension_value[3][1:]}:00"
                        if self._dimension_value[3][0] == "0"
                        else f"-{self._dimension_value[3][1:]}:00"
                    )
                else:
                    self._timezone = ""
                self._time = datetime.time.fromisoformat(
                    f"{self._hour}:{self._minute}:{self._second}{self._timezone}"
                )
                self._human_readable_log = (
                    f"Gateway broadcasting internal time: {self._time}."
                )
            except (ValueError, IndexError):
                pass

        elif self._dimension == 1 and self._dimension_value:
            try:
                self._year = self._dimension_value[3] if len(self._dimension_value) > 3 else "2000"
                self._month = self._dimension_value[2] if len(self._dimension_value) > 2 else "1"
                self._day = self._dimension_value[1] if len(self._dimension_value) > 1 else "1"
                self._date = datetime.date(
                    year=int(self._year), month=int(self._month), day=int(self._day)
                )
                self._human_readable_log = (
                    f"Gateway broadcasting internal date: {self._date}."
                )
            except (ValueError, IndexError):
                pass

        elif self._dimension == 22 and self._dimension_value:
            try:
                self._hour = self._dimension_value[0] if len(self._dimension_value) > 0 else "00"
                self._minute = self._dimension_value[1] if len(self._dimension_value) > 1 else "00"
                self._second = self._dimension_value[2] if len(self._dimension_value) > 2 else "00"
                # Timezone is sometimes missing from messages, assuming UTC
                if len(self._dimension_value) > 3 and self._dimension_value[3] != "":
                    self._timezone = (
                        f"+{self._dimension_value[3][1:]}:00"
                        if self._dimension_value[3][0] == "0"
                        else f"-{self._dimension_value[3][1:]}:00"
                    )
                else:
                    self._timezone = ""
                self._day = self._dimension_value[5] if len(self._dimension_value) > 5 else "01"
                self._month = self._dimension_value[6] if len(self._dimension_value) > 6 else "01"
                self._year = self._dimension_value[7] if len(self._dimension_value) > 7 else "2000"
                self._datetime = datetime.datetime.fromisoformat(
                    f"{self._year}-{self._month}-{self._day}*{self._hour}:{self._minute}:{self._second}{self._timezone}"  # pylint: disable=line-too-long
                )
                self._human_readable_log = (
                    f"Gateway broadcasting internal datetime: {self._datetime}."
                )
            except (ValueError, IndexError):
                pass

    @classmethod
    def set_datetime_to_now(cls, time_zone: str):
        timezone = pytz.timezone(time_zone)
        now = timezone.localize(datetime.datetime.now())
        timezone_offset = (
            f"0{now.strftime('%z')[1:3]}"
            if now.strftime("%z")[0] == "+"
            else f"1{now.strftime('%z')[1:3]}"
        )
        message = cls(
            f"*#13**#22*{now.strftime('%H*%M*%S')}*{timezone_offset}*0{now.strftime('%w*%d*%m*%Y##')}"  # pylint: disable=line-too-long
        )
        message._human_readable_log = f"Setting gateway time to: {message._datetime}."
        return message

    @classmethod
    def set_date_to_today(cls, time_zone: str):
        timezone = pytz.timezone(time_zone)
        now = timezone.localize(datetime.datetime.now())
        message = cls(f"*#13**#1*0{now.strftime('%w*%d*%m*%Y##')}")
        message._human_readable_log = f"Setting gateway date to: {message._date}."
        return message

    @classmethod
    def set_time_to_now(cls, time_zone: str):
        timezone = pytz.timezone(time_zone)
        now = timezone.localize(datetime.datetime.now())
        timezone_offset = (
            f"0{now.strftime('%z')[1:3]}"
            if now.strftime("%z")[0] == "+"
            else f"1{now.strftime('%z')[1:3]}"
        )
        message = cls(f"*#13**#0*{now.strftime('%H*%M*%S')}*{timezone_offset}##")
        message._human_readable_log = f"Setting gateway time to: {message._time}."
        return message


class OWNSoundCommand(OWNCommand):
    @classmethod
    def status(cls, where):
        message = cls(f"*#16*{where}##")
        message._human_readable_log = f"Requesting audio zone {where} status."
        return message

    @classmethod
    def turn_on(cls, where):
        message = cls(f"*16*3*{where}##")
        message._human_readable_log = f"Turning ON audio zone {where}."
        return message

    @classmethod
    def turn_off(cls, where):
        message = cls(f"*16*13*{where}##")
        message._human_readable_log = f"Turning OFF audio zone {where}."
        return message

    @classmethod
    def select_source(cls, where, source_id):
        """Select source for a specific zone.

        The F441M matrix requires TWO commands (captured from wall-panel
        bus traffic):

        1. **Activate the source device** on the bus:
           ``*16*3*10{source}##``  (WHERE = 100 + source number)
           This powers on / makes audible the source module that feeds
           the matrix input.

        2. **Route the amplifier output** to that source using the
           compound stereo address:
           ``*16*3*1{source}{zone_digit}##``
           e.g. Source 2, Output 1 → ``*16*3*121##``

        Both commands use WHAT = 3 (stereo channel ON).
        """
        zone_digit = str(where)[-1]
        source_num = int(source_id)

        # Command 1: activate the source device (WHERE = 101–109)
        source_device_addr = 100 + source_num
        activate = cls(f"*16*3*{source_device_addr}##")
        activate._human_readable_log = (
            f"Activating source device {source_num} (address {source_device_addr})."
        )

        # Command 2: route the amplifier output to that source (compound 1XY)
        source_base = 10 + source_num
        source_addr = f"{source_base}{zone_digit}"
        route = cls(f"*16*3*{source_addr}##")
        route._human_readable_log = (
            f"Routing zone {where} to source {source_num} (address {source_addr})."
        )

        return [activate, route]

    @classmethod
    def source_cycle(cls):
        """Cycle to the next source on the F441M matrix.

        Sends the official SOURCES CYCLE command (WHAT=23, WHERE=100).
        This is the only safe way to change the active source via the
        gateway.  Direct source-activation or compound-routing commands
        corrupt the F441M audio path and produce hiss.

        The matrix will report the new active source on the bus
        (e.g. ``*16*3*101##`` for Source 1, ``*16*3*102##`` for Source 2).
        """
        message = cls("*16*23*100##")
        message._human_readable_log = "Cycling to next audio source."
        return message

    @classmethod
    def volume_up(cls, where):
        message = cls(f"*16*1001*{where}##")
        message._human_readable_log = f"Turning UP volume for audio zone {where}."
        return message

    @classmethod
    def volume_down(cls, where):
        message = cls(f"*16*1000*{where}##")
        message._human_readable_log = f"Turning DOWN volume for audio zone {where}."
        return message

    @classmethod
    def set_volume(cls, where, volume):
        message = cls(f"*#16*{where}*#1*{volume}##")
        message._human_readable_log = f"Setting volume to {volume} for audio zone {where}."
        return message


class OWNEnergyCommand(OWNCommand):
    @classmethod
    def start_sending_instant_power(cls, where, duration: int = 65):
        where = f"{where}#0" if str(where).startswith("7") else str(where)
        duration = 255 if duration > 255 else duration
        message = cls(f"*#18*{where}*#1200#1*{duration}##")
        message._human_readable_log = f"Requesting instant power draw update from sensor {where} for {duration} minutes."  # pylint: disable=line-too-long
        return message

    @classmethod
    def get_hourly_consumption(cls, where, date: datetime.date):
        where = f"{where}#0" if str(where).startswith("7") else str(where)
        today = datetime.date.today()
        one_year_ago = datetime.date(
            year=today.year - 1, month=today.month, day=today.day
        )
        if date < one_year_ago:
            return None
        message = cls(f"*#18*{where}*511#{date.month}#{date.day}##")
        message._human_readable_log = (
            f"Requesting hourly power consumption from sensor {where} for {date}."
        )
        return message

    @classmethod
    def get_partial_daily_consumption(cls, where):
        where = f"{where}#0" if str(where).startswith("7") else str(where)
        message = cls(f"*#18*{where}*54##")
        message._human_readable_log = (
            f"Requesting today's partial power consumption from sensor {where}."
        )
        return message

    @classmethod
    def get_daily_consumption(cls, where, year, month):
        where = f"{where}#0" if str(where).startswith("7") else str(where)
        today = datetime.date.today()
        one_year_ago = today - relativedelta(years=1)
        two_year_ago = today - relativedelta(years=2)
        target = datetime.date(year=year, month=month, day=1)
        if target > today:
            return None
        elif target > one_year_ago:
            message = cls(f"*18*59#{month}*{where}##")
        elif target > two_year_ago:
            message = cls(f"*18*510#{month}*{where}##")
        else:
            return None
        message._human_readable_log = f"Requesting daily power consumption for {year}-{month} from sensor {where}."  # pylint: disable=line-too-long
        return message

    @classmethod
    def get_partial_monthly_consumption(cls, where):
        where = f"{where}#0" if str(where).startswith("7") else str(where)
        message = cls(f"*#18*{where}*53##")
        message._human_readable_log = (
            f"Requesting this month's partial power consumption from sensor {where}."
        )
        return message

    @classmethod
    def get_monthly_consumption(cls, where, year, month):
        where = f"{where}#0" if str(where).startswith("7") else str(where)
        message = cls(f"*#18*{where}*52#{str(year)[2:]}#{month}##")
        message._human_readable_log = f"Requesting monthly power consumption for {year}-{month} from sensor {where}."  # pylint: disable=line-too-long
        return message

    @classmethod
    def get_total_consumption(cls, where):
        where = f"{where}#0" if str(where).startswith("7") else str(where)
        message = cls(f"*#18*{where}*51##")
        message._human_readable_log = (
            f"Requesting total power consumption from sensor {where}."
        )
        return message


class OWNDryContactCommand(OWNCommand):
    @classmethod
    def status(cls, where):
        message = cls(f"*#25*{where}##")
        message._human_readable_log = f"Requesting dry contact {where} status."
        return message


class OWNSignaling(OWNMessage):
    """
    This class is a subclass of messages.
    It is dedicated to signaling messages such as ACK or Authentication negotiation
    """

    def __init__(self, data):  # pylint: disable=super-init-not-called
        self._raw = data
        self._family = None
        self._type = "UNKNOWN"
        self._human_readable_log = data

        if self._ACK.match(self._raw):
            self._match = self._ACK.match(self._raw)
            self._family = "SIGNALING"
            self._type = "ACK"
            self._human_readable_log = "ACK."
        elif self._NACK.match(self._raw):
            self._match = self._NACK.match(self._raw)
            self._family = "SIGNALING"
            self._type = "NACK"
            self._human_readable_log = "NACK."
        elif self._NONCE.match(self._raw):
            self._match = self._NONCE.match(self._raw)
            self._family = "SIGNALING"
            self._type = "NONCE"
            self._human_readable_log = (
                f"Nonce challenge received: {self._match.group(1)}."
            )
        elif self._SHA.match(self._raw):
            self._match = self._SHA.match(self._raw)
            self._family = "SIGNALING"
            self._type = f"SHA{'-1' if self._match.group(1) == '1' else '-256'}"
            self._human_readable_log = f"SHA{'-1' if self._match.group(1) == '1' else '-256'} challenge received."  # pylint: disable=line-too-long
        elif self._COMMAND_SESSION.match(self._raw):
            self._match = self._COMMAND_SESSION.match(self._raw)
            self._family = "SIGNALING"
            self._type = "COMMAND_SESSION"
            self._human_readable_log = "Command session requested."
        elif self._EVENT_SESSION.match(self._raw):
            self._match = self._EVENT_SESSION.match(self._raw)
            self._family = "SIGNALING"
            self._type = "EVENT_SESSION"
            self._human_readable_log = "Event session requested."

    @property
    def nonce(self):
        """Return the authentication nonce IF the message is a nonce message"""
        if self.is_nonce():
            return self._match.group(1)
        else:
            return None

    @property
    def sha_version(self):
        """Return the authentication SHA version IF the message is a SHA challenge message"""
        if self.is_sha():
            return self._match.group(1)
        else:
            return None

    def is_ack(self) -> bool:
        return self._type == "ACK"

    def is_nack(self) -> bool:
        return self._type == "NACK"

    def is_nonce(self) -> bool:
        return self._type == "NONCE"

    def is_sha(self) -> bool:
        return self._type == "SHA-1" or self._type == "SHA-256"

    def is_sha_1(self) -> bool:
        return self._type == "SHA-1"

    def is_sha_256(self) -> bool:
        return self._type == "SHA-256"
