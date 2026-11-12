from app.api import orders


def test_show():
    assert orders.bp.name == "orders"
