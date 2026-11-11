"""Every number that decides who gets paid what lives in this one file.

Kept free of Django imports so any app (delivery, orders, ...) can import it
without creating an import cycle.
"""

from decimal import ROUND_HALF_UP, Decimal

# The website's cut of every product sold (1%), taken from each store's slice
# of an order when the shopper's cash has been collected.
PLATFORM_FEE_RATE = Decimal("0.01")

# Flat pay per delivery trip. A store -> warehouse leg is one trip and a
# warehouse -> shopper leg is another, so a one-store order has two trips.
COURIER_TRIP_FEE = Decimal("20.00")

# Pay per package a warehouse worker packs (one package per order).
WAREHOUSE_PACK_FEE = Decimal("10.00")

CENT = Decimal("0.01")


def money(value):
    """Round to cents the way shoppers expect (half-up, not banker's)."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def platform_fee(gross):
    """The website's 1% of a store's sales."""
    return money(Decimal(gross) * PLATFORM_FEE_RATE)


def shipping_fee(store_count):
    """Delivery & handling fee the shopper pays on an order that spans
    `store_count` stores.

    It is exactly what the platform pays out in logistics for that order: one
    pickup trip per store, one drop-off trip, and one packing fee. Charging
    this is what keeps the platform account from going negative, since the 1%
    commission alone can never cover $20 trips and $10 packing. Change
    COURIER_TRIP_FEE / WAREHOUSE_PACK_FEE and the fee follows automatically.
    """
    if not store_count or store_count < 1:
        return money(0)
    return money(COURIER_TRIP_FEE * (store_count + 1) + WAREHOUSE_PACK_FEE)
