"""Смещение публичных координат: детерминированность и величина сдвига."""

import math

from server.geo import MAX_OFFSET_M, MIN_OFFSET_M, public_coords

SECRET = "test-geo-secret"
# Ереван, примерно центр
LAT, LON = 40.1792, 44.4991


def distance_m(lat1, lon1, lat2, lon2):
    """Гаверсинус — намеренно не той формулой, которой считает сама функция."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def test_offset_is_deterministic():
    assert public_coords(1, LAT, LON, SECRET) == public_coords(1, LAT, LON, SECRET)


def test_offset_stays_within_the_declared_range():
    # Округление публичной координаты до 4 знаков добавляет к сдвигу до ~8 м —
    # отсюда допуск вокруг заявленных 100–300.
    for device_id in range(1, 200):
        lat, lon = public_coords(device_id, LAT, LON, SECRET)
        d = distance_m(LAT, LON, lat, lon)
        assert MIN_OFFSET_M - 10 <= d <= MAX_OFFSET_M + 10, (device_id, d)


def test_different_devices_move_differently():
    points = {public_coords(i, LAT, LON, SECRET) for i in range(1, 50)}
    assert len(points) == 49


def test_offset_depends_on_the_secret():
    assert public_coords(1, LAT, LON, SECRET) != public_coords(1, LAT, LON, "other")


def test_public_point_is_rounded():
    lat, lon = public_coords(1, LAT, LON, SECRET)
    assert lat == round(lat, 4) and lon == round(lon, 4)


def test_directions_are_spread_over_the_circle():
    """Сдвиг не должен утыкаться в одну сторону — иначе истинная точка
    восстанавливается по облаку соседей."""
    quadrants = set()
    for device_id in range(1, 60):
        lat, lon = public_coords(device_id, LAT, LON, SECRET)
        quadrants.add((lat > LAT, lon > LON))
    assert len(quadrants) == 4


def test_survives_the_poles():
    lat, lon = public_coords(1, 89.999, 30.0, SECRET)
    assert math.isfinite(lat) and math.isfinite(lon)
