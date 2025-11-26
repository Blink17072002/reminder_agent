import json
from django import template
from django.utils.safestring import mark_safe

register = template.Library()

@register.filter(name='tojson')
def tojson(value):
    """Convert a Python object to JSON string."""
    return mark_safe(json.dumps(value))
