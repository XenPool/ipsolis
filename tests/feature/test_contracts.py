"""Software contracts — Model-A per-seat cost math via the admin API."""
from conftest import NS


def _find(api, cid):
    _, lst = api.get("/admin/contracts")
    return next((c for c in lst if c["id"] == cid), None)


def test_seat_price_annual(api):
    st, c = api.post("/admin/contracts", json={
        "vendor": f"{NS}-adobe", "product": "Creative Cloud",
        "contract_value": 72000, "currency": "EUR",
        "billing_interval": "annual", "licensed_seats": 100,
    })
    assert st == 201, c
    cid = c["id"]
    # 72000/yr → 6000/mo → /100 seats = 60.00/seat
    assert c["monthly_value"] == 6000.0
    assert c["seat_price_monthly"] == 60.0
    row = _find(api, cid)
    assert row["seat_price_monthly"] == 60.0
    api.delete(f"/admin/contracts/{cid}")


def test_seat_price_quarterly_and_unlimited(api):
    # quarterly: 3000/quarter → 1000/mo → /10 seats = 100.00
    st, c = api.post("/admin/contracts", json={
        "vendor": f"{NS}-q", "product": "Tool", "contract_value": 3000,
        "billing_interval": "quarterly", "licensed_seats": 10,
    })
    assert st == 201, c
    assert c["seat_price_monthly"] == 100.0
    api.delete(f"/admin/contracts/{c['id']}")

    # unlimited seats → no per-seat price
    st, c2 = api.post("/admin/contracts", json={
        "vendor": f"{NS}-site", "product": "SiteLic", "contract_value": 12000,
        "billing_interval": "annual", "licensed_seats": None,
    })
    assert st == 201, c2
    assert c2["seat_price_monthly"] is None
    assert c2["utilization"] is None
    api.delete(f"/admin/contracts/{c2['id']}")


def test_bad_billing_interval_rejected(api):
    st, _ = api.post("/admin/contracts", json={
        "vendor": f"{NS}-bad", "product": "X", "contract_value": 10,
        "billing_interval": "weekly",
    })
    assert st == 422
