"""
Tests for restocking API endpoints (recommendations and order submission).
"""
import pytest
from datetime import datetime, timedelta

import main


@pytest.fixture(autouse=True)
def clear_restocking_orders():
    """Restocking orders live in shared module state; reset before each test."""
    main.restocking_orders.clear()
    yield
    main.restocking_orders.clear()


class TestRestockingRecommendations:
    """Test suite for GET /api/restocking/recommendations."""

    def test_get_recommendations(self, client):
        """Test getting restocking recommendations."""
        response = client.get("/api/restocking/recommendations")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 8

        first = data[0]
        for field in ["sku", "name", "trend", "current_demand", "forecasted_demand",
                      "gap", "gap_pct", "unit_cost", "lead_time_days", "estimated_cost"]:
            assert field in first

    def test_negative_gap_items_excluded(self, client):
        """Items with decreasing demand (negative gap) should not be recommended."""
        response = client.get("/api/restocking/recommendations")
        skus = [r["sku"] for r in response.json()]
        assert "MTR-304" not in skus

    def test_ranking_order(self, client):
        """Increasing-trend items rank first, ordered by gap percent descending."""
        response = client.get("/api/restocking/recommendations")
        data = response.json()

        assert data[0]["sku"] == "WDG-001"
        assert data[1]["sku"] == "GSK-203"
        assert data[2]["sku"] == "FLT-405"

        # All increasing-trend items must come before all stable ones
        trends = [r["trend"] for r in data]
        first_stable = trends.index("stable")
        assert all(t == "increasing" for t in trends[:first_stable])
        assert all(t != "increasing" for t in trends[first_stable:])

        # Within each trend group, gap_pct is non-increasing
        for group in ("increasing", "stable"):
            pcts = [r["gap_pct"] for r in data if r["trend"] == group]
            assert pcts == sorted(pcts, reverse=True)

    def test_gap_and_cost_math(self, client):
        """Gap and estimated cost are computed from forecast and inventory data."""
        response = client.get("/api/restocking/recommendations")
        by_sku = {r["sku"]: r for r in response.json()}

        wdg = by_sku["WDG-001"]
        assert wdg["gap"] == 150
        assert abs(wdg["gap_pct"] - 50.0) < 0.01
        assert abs(wdg["estimated_cost"] - 150 * wdg["unit_cost"]) < 0.01
        assert abs(wdg["estimated_cost"] - 8250.0) < 0.01

    def test_lead_time_from_inventory(self, client):
        """Lead times come from the joined inventory record."""
        response = client.get("/api/restocking/recommendations")
        by_sku = {r["sku"]: r for r in response.json()}
        assert by_sku["PSU-501"]["lead_time_days"] == 12
        assert by_sku["WDG-001"]["lead_time_days"] == 7


class TestCreateRestockingOrder:
    """Test suite for POST /api/restocking/orders and GET /api/restocking/orders."""

    def _valid_payload(self):
        return {
            "budget": 10000,
            "items": [
                {"sku": "WDG-001", "quantity": 150},
                {"sku": "GSK-203", "quantity": 100},
            ],
        }

    def test_create_order_success(self, client):
        """Test submitting a valid restocking order."""
        response = client.post("/api/restocking/orders", json=self._valid_payload())
        assert response.status_code == 201

        order = response.json()
        assert order["order_number"].startswith("RST-")
        assert order["status"] == "Submitted"
        assert order["budget"] == 10000

        # total = 150 * 55.00 + 100 * 42.00
        assert abs(order["total_cost"] - (8250.0 + 4200.0)) < 0.01

        # Order lead time is the max across items (WDG-001: 7, GSK-203: 10)
        assert order["lead_time_days"] == 10

        created = datetime.fromisoformat(order["created_date"])
        expected = datetime.fromisoformat(order["expected_delivery"])
        assert abs((expected - created) - timedelta(days=10)) < timedelta(seconds=1)

    def test_order_items_structure(self, client):
        """Order items echo inventory pricing and lead times."""
        response = client.post("/api/restocking/orders", json=self._valid_payload())
        items = response.json()["items"]
        assert len(items) == 2

        by_sku = {i["sku"]: i for i in items}
        wdg = by_sku["WDG-001"]
        assert wdg["name"] == "Industrial Widget Type A"
        assert wdg["quantity"] == 150
        assert abs(wdg["line_total"] - wdg["quantity"] * wdg["unit_cost"]) < 0.01
        assert wdg["lead_time_days"] == 7

    def test_created_order_appears_in_list(self, client):
        """Test that a submitted order is returned by the GET endpoint."""
        assert client.get("/api/restocking/orders").json() == []

        created = client.post("/api/restocking/orders", json=self._valid_payload()).json()

        orders = client.get("/api/restocking/orders").json()
        assert len(orders) == 1
        assert orders[0]["order_number"] == created["order_number"]

    def test_order_numbers_increment(self, client):
        """Test that consecutive orders get distinct incrementing numbers."""
        first = client.post("/api/restocking/orders", json=self._valid_payload()).json()
        second = client.post("/api/restocking/orders", json=self._valid_payload()).json()
        assert first["order_number"] != second["order_number"]
        assert first["order_number"].endswith("0001")
        assert second["order_number"].endswith("0002")

    def test_empty_items_rejected(self, client):
        """Test that an order with no items returns 400."""
        response = client.post("/api/restocking/orders", json={"budget": 5000, "items": []})
        assert response.status_code == 400
        assert "detail" in response.json()

    def test_unknown_sku_rejected(self, client):
        """Test that an unknown SKU returns 400."""
        response = client.post("/api/restocking/orders", json={
            "budget": 5000,
            "items": [{"sku": "NOPE-999", "quantity": 10}],
        })
        assert response.status_code == 400
        assert "NOPE-999" in response.json()["detail"]

    def test_nonpositive_quantity_rejected(self, client):
        """Test that a zero quantity returns 400."""
        response = client.post("/api/restocking/orders", json={
            "budget": 5000,
            "items": [{"sku": "WDG-001", "quantity": 0}],
        })
        assert response.status_code == 400


class TestInventoryLeadTime:
    """Inventory model accepts the new optional lead_time_days field."""

    def test_inventory_includes_new_skus(self, client):
        """The 8 forecast SKUs added to inventory are served."""
        response = client.get("/api/inventory")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 40

        skus = {item["sku"] for item in data}
        for sku in ["WDG-001", "BRG-102", "GSK-203", "MTR-304",
                    "FLT-405", "VLV-506", "SNR-420", "CTL-330"]:
            assert sku in skus

    def test_lead_time_optional(self, client):
        """Records without lead_time_days still validate (field is optional)."""
        response = client.get("/api/inventory")
        data = response.json()
        by_sku = {item["sku"]: item for item in data}
        # New record has it; a legacy record returns None
        assert by_sku["WDG-001"]["lead_time_days"] == 7
        assert by_sku["PCB-001"]["lead_time_days"] is None
