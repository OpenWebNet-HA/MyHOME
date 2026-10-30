"""R-1: a tape reading accepted above the travel (up to its physical limit) never puts the cover past its end stop."""
from tests.test_cover_calibration_check_plan import measured
from tests.test_cover_calibration_geometry import geometry as geometry_fixture
from tests.test_cover_profiles import plant as plant_fixture
from tests.test_panel_cover_calibration import act
from tests.test_panel_cover_calibration import calibration as calibration_fixture

geometry = geometry_fixture
plant = plant_fixture
calibration = calibration_fixture


async def test_the_last_reading_at_the_physical_limit_saves_and_anchors_at_the_top(geometry):
    cal = geometry
    # The half-closing reading at the very top of the accepted range: above the travel, as L-9 accepts.
    await measured(cal, lift_s=4.4, gap=3, up=22.3, down=23.8, travel=198, half_open=0.5, half_close=1.0)
    assert cal.session.readings["half_close"] > 198
    result = await act(cal, "save", name="Above the travel")
    assert result["phase"] == "saved"
    assert cal.cover.current_cover_position == 100


def test_the_edge_of_a_reading_stays_within_the_travel():
    from custom_components.myhome.cover_calibration_geometry import edge_percent
    assert [edge_percent(cm, 198) for cm in (0, 99, 198, 201)] == [0, 50, 100, 100]
