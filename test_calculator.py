"""Regression tests for the /calculate route's input handling."""

import pytest

from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


def test_empty_item_cost_returns_200(client):
    """Empty itemCost must render a friendly error, not crash with a 500."""
    r = client.post(
        "/calculate",
        data={"itemCost": "", "wageAmount": "20", "wageType": "hourly"},
    )
    assert r.status_code == 200


def test_non_numeric_item_cost_returns_200(client):
    """A non-numeric cost must render a friendly error, not crash with a 500."""
    r = client.post(
        "/calculate",
        data={"itemCost": "abc", "wageAmount": "20", "wageType": "hourly"},
    )
    assert r.status_code == 200


def test_valid_input_still_calculates(client):
    """A valid request still produces a result page."""
    r = client.post(
        "/calculate",
        data={"itemCost": "100", "wageAmount": "20", "wageType": "hourly"},
    )
    assert r.status_code == 200
    assert b"5.0" in r.data  # 100 / 20 = 5 hours
