from django.core.cache import cache
from django.db.models import Sum, Avg
from products.models import Product
from .models import Interaction

TRENDING_TTL = 60 * 15   # 15 min
RELATED_TTL = 60 * 60    # 1 hr
USER_REC_TTL = 60 * 30   # 30 min


def _reorder(ids):
    """Requery preserving the ranked order from a cached id list."""
    products = Product.objects.filter(id__in=ids).select_related('category', 'store')
    order = {pid: i for i, pid in enumerate(ids)}
    return sorted(products, key=lambda p: order.get(p.id, 999))


def get_popular_products(limit=10, category=None):
    """V1: pure popularity. Cold-start fallback for everyone."""
    key = f"popular_{category.id if category else 'all'}_{limit}"
    ids = cache.get(key)
    if ids is None:
        qs = Product.objects.filter(current_stock__gt=0)
        if category:
            qs = qs.filter(category=category)
        qs = qs.annotate(avg_rating=Avg('reviews__stars')).order_by('-sold', '-avg_rating')[:limit]
        ids = list(qs.values_list('id', flat=True))
        cache.set(key, ids, TRENDING_TTL)
    return _reorder(ids)


def get_related_products(product, limit=8):
    """'Customers who interacted with this also liked...' Falls back to same-category."""
    key = f"related_{product.id}_{limit}"
    ids = cache.get(key)
    if ids is None:
        users = Interaction.objects.filter(product=product).values_list('user_id', flat=True).distinct()
        co_occurring = (
            Interaction.objects.filter(user_id__in=users)
            .exclude(product=product)
            .values('product_id')
            .annotate(score=Sum('weight'))
            .order_by('-score')[:limit]
        )
        ids = [row['product_id'] for row in co_occurring]

        if len(ids) < limit:  # top up with same-category, best-selling
            needed = limit - len(ids)
            fallback = (
                Product.objects.filter(category=product.category, current_stock__gt=0)
                .exclude(id__in=[product.id] + ids)
                .order_by('-sold')[:needed]
                .values_list('id', flat=True)
            )
            ids += list(fallback)

        cache.set(key, ids, RELATED_TTL)
    return _reorder(ids)


def get_recommendations_for_user(user, limit=10):
    """V2: personalized feed for the customer/home page."""
    if not user.is_authenticated:
        return get_popular_products(limit=limit)

    key = f"user_recs_{user.id}_{limit}"
    ids = cache.get(key)
    if ids is None:
        seen_ids = list(Interaction.objects.filter(user=user).values_list('product_id', flat=True).distinct())
        if not seen_ids:
            return get_popular_products(limit=limit)

        # users who overlap with this user's interactions
        similar_users = (
            Interaction.objects.filter(product_id__in=seen_ids)
            .exclude(user=user)
            .values_list('user_id', flat=True).distinct()
        )
        scored = (
            Interaction.objects.filter(user_id__in=similar_users)
            .exclude(product_id__in=seen_ids)
            .values('product_id')
            .annotate(score=Sum('weight'))
            .order_by('-score')[:limit]
        )
        ids = [row['product_id'] for row in scored]

        if len(ids) < limit:  # top up with this user's favorite categories
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

        if len(ids) < limit:  # final top-up: global popularity
            for p in get_popular_products(limit=(limit - len(ids)) * 2):
                if p.id not in seen_ids and p.id not in ids:
                    ids.append(p.id)
                if len(ids) >= limit:
                    break

        cache.set(key, ids, USER_REC_TTL)
    return _reorder(ids)