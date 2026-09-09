from django.core.cache import cache
from django.db.models import Case, IntegerField, Q, When

from merchant_interface.models import Store
from products.models import Product

SEARCH_TTL = 60 * 5  # 5 min - short-lived since stock/relevance shift often
SEARCH_POOL_SIZE = 60
SEARCH_STORE_RATIO = 1 / 5  # roughly one store per five products, when stores match


def _reorder_mixed(page_slice):
    """Requery products and stores for one page, preserving the ranked (type, id) order."""
    product_ids = [oid for otype, oid in page_slice if otype == "product"]
    store_ids = [oid for otype, oid in page_slice if otype == "store"]

    products_by_id = {
        p.id: p
        for p in Product.objects.filter(id__in=product_ids).select_related(
            "category", "store"
        )
    }
    stores_by_id = {s.id: s for s in Store.objects.filter(id__in=store_ids)}

    results = []
    for otype, oid in page_slice:
        if otype == "product" and oid in products_by_id:
            results.append({"type": "product", "object": products_by_id[oid]})
        elif otype == "store" and oid in stores_by_id:
            results.append({"type": "store", "object": stores_by_id[oid]})
    return results


def _interleave_by_ratio(primary_items, secondary_items, ratio):
    """
    Merges two ranked lists into one, inserting a secondary item after every
    `1/ratio` primary items while preserving each list's own relevance
    order. Never forces secondary items in if there aren't any.
    """
    result = []
    p_idx = s_idx = 0
    since_last_secondary = 0
    step = round(1 / ratio) if ratio else None

    while p_idx < len(primary_items) or s_idx < len(secondary_items):
        take_secondary = (
            step is not None
            and since_last_secondary >= step - 1
            and s_idx < len(secondary_items)
        )
        if take_secondary:
            result.append(secondary_items[s_idx])
            s_idx += 1
            since_last_secondary = 0
        elif p_idx < len(primary_items):
            result.append(primary_items[p_idx])
            p_idx += 1
            since_last_secondary += 1
        elif s_idx < len(secondary_items):
            result.append(secondary_items[s_idx])
            s_idx += 1
        else:
            break

    return result


def search_products(query, limit=SEARCH_POOL_SIZE):
    """Product matches ranked by name-starts-with first, then by sales."""
    return (
        Product.active.filter(Q(name__icontains=query) | Q(description__icontains=query))
        .annotate(
            starts_with_query=Case(
                When(name__istartswith=query, then=0),
                default=1,
                output_field=IntegerField(),
            )
        )
        .order_by("starts_with_query", "-sold")[:limit]
    )


def search_stores(query, limit=SEARCH_POOL_SIZE):
    """Store matches ranked by name-starts-with first, then alphabetically."""
    return (
        Store.objects.filter(enabled=True)
        .filter(Q(name__icontains=query) | Q(description__icontains=query))
        .annotate(
            starts_with_query=Case(
                When(name__istartswith=query, then=0),
                default=1,
                output_field=IntegerField(),
            )
        )
        .order_by("starts_with_query", "name")[:limit]
    )


def search_catalog(query, page=1, page_size=12):
    """
    Combined product + store search for one page of results. Products and
    stores are ranked independently, then merged so a store surfaces roughly
    every SEARCH_STORE_RATIO products - skipped entirely if no stores match.
    """
    query = (query or "").strip()
    if not query:
        return {"results": [], "has_more": False}

    cache_key = f"search_{query.lower()}"
    result_ids = cache.get(cache_key)

    if result_ids is None:
        product_items = [("product", p.id) for p in search_products(query)]
        store_items = [("store", s.id) for s in search_stores(query)]
        result_ids = _interleave_by_ratio(product_items, store_items, SEARCH_STORE_RATIO)
        cache.set(cache_key, result_ids, SEARCH_TTL)

    start = (page - 1) * page_size
    end = start + page_size
    page_slice = result_ids[start:end]

    return {
        "results": _reorder_mixed(page_slice),
        "has_more": end < len(result_ids),
    }