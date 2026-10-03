from django import template

register = template.Library()


@register.filter
def mul(value, arg):
    """Multiply two values in a template: {{ price|mul:quantity }}"""
    try:
        return float(value) * float(arg)
    except (TypeError, ValueError):
        return 0