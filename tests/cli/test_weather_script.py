import io
import json
import runpy
import sys
import urllib.parse
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path('/home/admin/.nanobot/workspace/scripts/weather.py')


class _FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return io.StringIO(json.dumps(self.payload))

    def __exit__(self, exc_type, exc, tb):
        return False


def test_weather_script_uses_fixed_nanan_coordinates_without_geocoding():
    calls = []

    def fake_urlopen(url, timeout=10):
        calls.append(url)
        if 'api.open-meteo.com/v1/forecast' in url:
            parsed = urllib.parse.urlparse(url)
            qs = urllib.parse.parse_qs(parsed.query)
            assert qs['latitude'] == ['29.5']
            assert qs['longitude'] == ['106.5']
            return _FakeResp({
                'current_weather': {'temperature': 26, 'windspeed': 3, 'weathercode': 1},
                'daily': {
                    'time': ['2026-04-14', '2026-04-15', '2026-04-16'],
                    'temperature_2m_max': [30, 29, 28],
                    'temperature_2m_min': [20, 19, 18],
                    'weathercode': [1, 2, 3],
                },
            })
        raise AssertionError(f'unexpected url: {url}')

    stdout = io.StringIO()
    old_argv = sys.argv[:]
    try:
        sys.argv = [str(SCRIPT), '重庆南岸区', 'forecast']
        with patch('urllib.request.urlopen', side_effect=fake_urlopen), patch('sys.stdout', stdout):
            runpy.run_path(str(SCRIPT), run_name='__main__')
    finally:
        sys.argv = old_argv

    assert all('geocoding-api.open-meteo.com' not in url for url in calls)
    assert '📍 重庆南岸区' in stdout.getvalue()
