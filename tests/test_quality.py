"""Флаги качества данных. Чистые функции, HTTP не нужен."""

import pytest

from server import quality


@pytest.mark.parametrize("humidity,expected", [
    (None, None),
    (0.0, None),
    (45.0, None),
    (74.9, None),
    (75.0, "suspect"),      # порог включительно
    (84.9, "suspect"),
    (85.0, "invalid"),      # порог включительно
    (100.0, "invalid"),
])
def test_rh_flag_thresholds(humidity, expected):
    assert quality.rh_flag(humidity) == expected


def test_rh_flag_thresholds_are_below_the_nominal_ones():
    """Пороги сдвинуты вниз намеренно: BME280 греется от ESP8266 в одном
    корпусе и занижает влажность. Возврат к номинальным 85/90 обесценил бы
    флаг ровно в том диапазоне, ради которого он заведён."""
    assert quality.RH_SUSPECT < 85.0
    assert quality.RH_INVALID < 90.0
    assert quality.RH_SUSPECT < quality.RH_INVALID
