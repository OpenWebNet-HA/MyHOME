from unittest.mock import MagicMock

from homeassistant.components.light.const import (
    ColorMode,
    LightEntityFeature,
)

from custom_components.myhome.light import MyHOMELight
from custom_components.myhome.light_dali import DaliFeatureLock


def test_dali_feature_lock_initialization():
    """Test allowed color modes initialization based on config."""
    lock = DaliFeatureLock(lock_features=True, rgb=True)
    assert lock.allowed_color_modes == {ColorMode.HS, ColorMode.BRIGHTNESS}

    lock = DaliFeatureLock(lock_features=True, color_temp=True)
    assert lock.allowed_color_modes == {ColorMode.COLOR_TEMP, ColorMode.BRIGHTNESS}

    lock = DaliFeatureLock(lock_features=True, rgb=True, color_temp=True)
    assert lock.allowed_color_modes == {ColorMode.HS, ColorMode.COLOR_TEMP, ColorMode.BRIGHTNESS}

    lock = DaliFeatureLock(lock_features=True, dimmable=True)
    assert lock.allowed_color_modes == {ColorMode.BRIGHTNESS}

    lock = DaliFeatureLock(lock_features=True)
    assert lock.allowed_color_modes == {ColorMode.ONOFF}

    lock = DaliFeatureLock(lock_features=False, rgb=True, color_temp=True)
    assert lock.allowed_color_modes == set()


def test_dali_feature_lock_is_mode_forbidden():
    """Test forbidden mode checks."""
    lock = DaliFeatureLock(lock_features=True, dimmable=True)
    assert lock.is_mode_forbidden(ColorMode.HS) is True
    assert lock.is_mode_forbidden(ColorMode.BRIGHTNESS) is False

    lock_open = DaliFeatureLock(lock_features=False)
    assert lock_open.is_mode_forbidden(ColorMode.HS) is False


def test_dali_feature_lock_promote_color_mode():
    """Test promoting color modes without dropping compatible others."""
    lock = DaliFeatureLock(lock_features=False)

    # Empty start -> HS
    new_modes, new_mode, add_feat, rm_feat = lock.promote_color_mode(set(), ColorMode.HS)
    assert new_modes == {ColorMode.HS}
    assert new_mode == ColorMode.HS
    assert add_feat == LightEntityFeature.TRANSITION
    assert rm_feat == LightEntityFeature.FLASH

    # HS -> Color Temp (both coexist for DALI DT8)
    new_modes, new_mode, add_feat, rm_feat = lock.promote_color_mode(
        {ColorMode.HS}, ColorMode.COLOR_TEMP
    )
    assert new_modes == {ColorMode.HS, ColorMode.COLOR_TEMP}

    # Brightness -> HS (HS subsumes Brightness)
    new_modes, new_mode, add_feat, rm_feat = lock.promote_color_mode(
        {ColorMode.BRIGHTNESS}, ColorMode.HS
    )
    assert new_modes == {ColorMode.HS}

    # ONOFF -> Brightness (Brightness subsumes ONOFF)
    new_modes, new_mode, add_feat, rm_feat = lock.promote_color_mode(
        {ColorMode.ONOFF}, ColorMode.BRIGHTNESS
    )
    assert new_modes == {ColorMode.BRIGHTNESS}


def test_dali_feature_lock_promote_color_mode_forbidden():
    """Test promotion is blocked if mode is forbidden."""
    lock = DaliFeatureLock(lock_features=True, dimmable=True)
    new_modes, new_mode, add_feat, rm_feat = lock.promote_color_mode(
        {ColorMode.BRIGHTNESS}, ColorMode.HS
    )
    assert new_modes == {ColorMode.BRIGHTNESS}
    assert new_mode is None
    assert add_feat == LightEntityFeature(0)
    assert rm_feat == LightEntityFeature(0)


def test_dali_feature_lock_promote_color_mode_color_temp():
    """Test ColorMode.COLOR_TEMP subsumes brightness and onoff and coexists with HS."""
    lock = DaliFeatureLock(lock_features=False)
    new_modes, new_mode, add_feat, rm_feat = lock.promote_color_mode(
        {ColorMode.BRIGHTNESS, ColorMode.ONOFF}, ColorMode.COLOR_TEMP
    )
    assert new_modes == {ColorMode.COLOR_TEMP}
    assert new_mode == ColorMode.COLOR_TEMP
    assert add_feat == LightEntityFeature.TRANSITION
    assert rm_feat == LightEntityFeature.FLASH

    # Now add HS: coexists with COLOR_TEMP
    new_modes2, new_mode2, _, _ = lock.promote_color_mode(new_modes, ColorMode.HS)
    assert new_modes2 == {ColorMode.COLOR_TEMP, ColorMode.HS}
    assert new_mode2 == ColorMode.HS


def test_dali_feature_lock_log_locked_out(caplog):
    """Test log_locked_out logs debug message."""
    lock = DaliFeatureLock(lock_features=True, dimmable=True)
    with caplog.at_level("DEBUG"):
        lock.log_locked_out("GW1", "12", "*1*12*1##", "12")
    assert "GW1 light 12 is locked to ['brightness']" in caplog.text
    assert "ignoring Dimension 12 frame *1*12*1##" in caplog.text


def test_myhome_light_dali_shims(hass, caplog):
    """Test MyHOMELight backward-compatibility shims delegating to DaliFeatureLock."""
    gateway = MagicMock()
    gateway.log_id = "GW1"
    light = MyHOMELight(
        hass=hass,
        name="Test",
        entity_name="Test",
        icon=None,
        icon_on=None,
        device_id="12",
        who="1",
        where="12",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="Dimmer",
        gateway=gateway,
        lock_features=True,
    )
    # Test _is_mode_forbidden shim
    assert light._is_mode_forbidden(ColorMode.RGB) is True

    # Test _log_locked_out shim
    msg = MagicMock()
    msg.raw = "*1*12*1##"
    with caplog.at_level("DEBUG"):
        light._log_locked_out(msg, "12")
    assert "locked to ['brightness']" in caplog.text

    # Test _promote_color_mode shim
    unlocked_light = MyHOMELight(
        hass=hass,
        name="Test2",
        entity_name="Test2",
        icon=None,
        icon_on=None,
        device_id="13",
        who="1",
        where="13",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="Dimmer",
        gateway=gateway,
        lock_features=False,
    )
    unlocked_light._promote_color_mode(ColorMode.COLOR_TEMP)
    assert ColorMode.COLOR_TEMP in unlocked_light._attr_supported_color_modes
