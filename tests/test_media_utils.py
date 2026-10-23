from types import SimpleNamespace

import pytest

from ssmm.media_utils import (display_aspect_ratio, format_duration_suffix, has_dar_override,
                              normalize_rotation, quantize_duration, rotation_filters,
                              slide_timeline)


def test_quantize_rounds_up_to_frame_grid():
    assert quantize_duration(1.01, 30) == pytest.approx(31 / 30)
    assert quantize_duration(3.3, 30) == pytest.approx(3.3)  # already on the grid
    assert quantize_duration(0, 30) == 0.0
    assert quantize_duration(2.0, 0) == 0.0


@pytest.mark.parametrize("matrix, tag, expected", [
    (-90, None, "90"),      # portrait phone clip as reported by current ffprobe
    (90, None, "270"),
    (-180, None, "180"),
    (180, None, "180"),
    ("-90.00", None, "90"),
    (None, "90", "90"),     # legacy clockwise 'rotate' tag
    (None, "270", "270"),
    (0, None, None),
    (None, None, None),
])
def test_normalize_rotation(matrix, tag, expected):
    assert normalize_rotation(matrix, tag) == expected


def test_rotation_filters():
    assert rotation_filters("90") == ["transpose=clock"]
    assert rotation_filters("270") == ["transpose=cclock"]
    assert rotation_filters("180") == ["hflip", "vflip"]
    assert rotation_filters(None) == []


def test_display_aspect_ratio_handles_rotation_and_dar():
    assert display_aspect_ratio({'width': 1920, 'height': 1080}) == pytest.approx(16 / 9)
    assert display_aspect_ratio({'width': 1920, 'height': 1080, 'rotate': "90"}) == pytest.approx(9 / 16)
    anamorphic = {'width': 1440, 'height': 1080, 'dar': '16:9'}
    assert display_aspect_ratio(anamorphic) == pytest.approx(16 / 9)
    assert has_dar_override(anamorphic)
    assert display_aspect_ratio({**anamorphic, 'rotate': "270"}) == pytest.approx(9 / 16)
    assert display_aspect_ratio({'width': 0, 'height': 0}) is None


@pytest.mark.parametrize("seconds, expected", [
    (299.6, "5m0s"),   # used to be formatted as "4m60s"
    (59.4, "59s"),
    (61, "1m1s"),
    (0, "0s"),
])
def test_format_duration_suffix(seconds, expected):
    assert format_duration_suffix(seconds) == expected


def test_slide_timeline_ignores_last_interval():
    slides = [SimpleNamespace(duration=2.0, interval_to_next=1),
              SimpleNamespace(duration=1.01, interval_to_next=1),
              SimpleNamespace(duration=3.0, interval_to_next=5)]
    starts, total = slide_timeline(slides, 30)
    assert starts == pytest.approx([0.0, 3.0, 3.0 + 31 / 30 + 1])
    assert total == pytest.approx(3.0 + 31 / 30 + 1 + 3.0)
