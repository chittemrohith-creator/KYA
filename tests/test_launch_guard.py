import pytest
from app import create_app

def test_production_launch_is_blocked(monkeypatch):
    monkeypatch.setenv('CIVICSYNC_ENV', 'production')
    with pytest.raises(RuntimeError, match='Public launch blocked'):
        create_app({'TESTING': True})
