from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Sum, Avg, Count, Q
from django.utils import timezone

from products.models import Product
from orders.models import OrderItem
from .models import Interaction, StoreFollow

TRENDING_TTL = 60 * 15   # 15 min
RELATED_TTL = 60 * 60    # 1 hr
USER_REC_TTL = 60 * 30   # 30 min
FBT_TTL = 60 * 60        # 1 hr

RECENCY_WINDOW = timedelta(days=90)   # ignore interactions older than this
MIN_RATING = 3                        # products rated below this get filtered from "popular"
FOLLOWED_STORE_BOOST = 4              # score bump for products from stores the user follows

def _reorder(ids):
    """Requery preserving the ranked order from a cached id list."""
    products = Product.objects.filter(id__in=ids).select_related('category', 'store')
    order = {pid: i for i, pid in enumerate(ids)}
    return sorted(products, key=lambda p: order.get(p.id, 999))


def _price_band(product, spread=0.5):
    """Returns a (low, high) Decimal range around a product's price, or None."""
    price = product.selling_price
    if not price:
        return None
    spread = Decimal(str(spread))
    return (price * (1 - spread), price * (1 + spread))


def get_popular_products(limit=10, category=None):
    """V1: pure popularity, filtered to decently-rated products. Cold-start fallback for everyone."""
    key = f"popular_{category.id if category else 'all'}_{limit}"
    ids = cache.get(key)
    if ids is None:
        qs = Product.objects.filter(current_stock__gt=0)
        if category:
            qs = qs.filter(category=category)
        qs = (
            qs.annotate(avg_rating=Avg('reviews__stars'))
            .filter(Q(avg_rating__gte=MIN_RATING) | Q(avg_rating__isnull=True))
            .order_by('-sold', '-avg_rating')[:limit]
        )
        ids = list(qs.values_list('id', flat=True))
        cache.set(key, ids, TRENDING_TTL)
    return _reorder(ids)


def get_related_products(product, limit=8):
    """'Customers who interacted with this also liked...' Falls back to same-category, similar-price."""
    key = f"related_{product.id}_{limit}"
    ids = cache.get(key)
    if ids is None:
        recent_cutoff = timezone.now() - RECENCY_WINDOW

        users = (
            Interaction.objects.filter(product=product, timestamp__gte=recent_cutoff)
            .values_list('user_id', flat=True).distinct()
        )
        co_occurring = (
            Interaction.objects.filter(user_id__in=users, timestamp__gte=recent_cutoff)
            .exclude(product=product)
            .values('product_id')
            .annotate(score=Sum('weight'))
            .order_by('-score')[:limit]
        )
        ids = [row['product_id'] for row in co_occurring]

        if len(ids) < limit:
            needed = limit - len(ids)
            fallback_qs = Product.objects.filter(category=product.category, current_stock__gt=0)
            band = _price_band(product)
            if band:
                fallback_qs = fallback_qs.filter(selling_price__range=band)
            fallback = (
                fallback_qs.exclude(id__in=[product.id] + ids)
                .order_by('-sold')[:needed]
                .values_list('id', flat=True)
            )
            ids += list(fallback)

        cache.set(key, ids, RELATED_TTL)
    return _reorder(ids)


def get_recommendations_for_user(user, limit=10):
    """V2: personalized feed for the customer/home page, with recency + followed-store boost."""
    if not user.is_authenticated:
        return get_popular_products(limit=limit)

    key = f"user_recs_{user.id}_{limit}"
    ids = cache.get(key)
    if ids is None:
        recent_cutoff = timezone.now() - RECENCY_WINDOW

        seen_ids = list(
            Interaction.objects.filter(user=user)
            .values_list('product_id', flat=True).distinct()
        )
        if not seen_ids:
            return get_popular_products(limit=limit)

        similar_users = (
            Interaction.objects.filter(product_id__in=seen_ids, timestamp__gte=recent_cutoff)
            .exclude(user=user)
            .values_list('user_id', flat=True).distinct()
        )
        scored_rows = (
            Interaction.objects.filter(user_id__in=similar_users, timestamp__gte=recent_cutoff)
            .exclude(product_id__in=seen_ids)
            .values('product_id')
            .annotate(score=Sum('weight'))
        )
        scores = {row['product_id']: row['score'] for row in scored_rows}

        # boost products from stores this user follows
        followed_store_ids = list(
            StoreFollow.objects.filter(user=user).values_list('store_id', flat=True)
        )
        if followed_store_ids:
            followed_candidates = (
                Product.objects.filter(store_id__in=followed_store_ids, current_stock__gt=0)
                .exclude(id__in=seen_ids)
                .values_list('id', flat=True)
            )
            for pid in followed_candidates:
                scores[pid] = scores.get(pid, 0) + FOLLOWED_STORE_BOOST

        ids = [pid for pid, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)][:limit]

        if len(ids) < limit:
            top_cats = (
                Interaction.objects.filter(user=user)
                .values('product__category_id')
                .annotate(score=Sum('weight'))
                .order_by('-score')[:3]
            )
            cat_ids = [c['product__category_id'] for c in top_cats]
            needed = limit - len(ids)
            fallback = (
                Product.objects.filter(category_id__in=cat_ids, current_stock__gt=0)
                .exclude(id__in=seen_ids + ids)
                .order_by('-sold')[:needed]
                .values_list('id', flat=True)
            )
            ids += list(fallback)

        if len(ids) < limit:
            for p in get_popular_products(limit=(limit - len(ids)) * 2):
                if p.id not in seen_ids and p.id not in ids:
                    ids.append(p.id)
                if len(ids) >= limit:
                    break

        cache.set(key, ids, USER_REC_TTL)
    return _reorder(ids)


def get_frequently_bought_together(product, limit=5):
    """Real market-basket signal from actual orders. Best used on the cart/checkout page."""
    key = f"fbt_{product.id}_{limit}"
    ids = cache.get(key)
    if ids is None:
        order_ids = OrderItem.objects.filter(product=product).values_list('order_id', flat=True)
        co_bought = (
            OrderItem.objects.filter(order_id__in=order_ids)
            .exclude(product=product)
            .values('product_id')
            .annotate(times_bought_together=Count('id'))
            .order_by('-times_bought_together')[:limit]
        )
        ids = [row['product_id'] for row in co_bought]
        cache.set(key, ids, FBT_TTL)
    return _reorder(ids)