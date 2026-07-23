from services.performance_intelligence import Segment
from version import APP_VERSION

def test_release_version():
    assert APP_VERSION == "9.5.0"

def test_segment_maturity_and_reliability():
    assert Segment("X", 2, 1, 2.0).maturity == "INSUFFICIENT"
    assert Segment("X", 10, 6, 5.0).maturity == "EARLY"
    assert Segment("X", 30, 18, 9.0).maturity == "MODERATE"
    assert Segment("X", 10, 6, 6.0).reliability == 0.2
