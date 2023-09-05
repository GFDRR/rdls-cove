from django import template
from django.template.defaultfilters import stringfilter

register = template.Library()


@register.filter
@stringfilter
def property_name(value):
    return value.split("/")[0]


@register.filter
def temporal_start_end(value):
    if isinstance(value, list):
        if len(value) > 1 and "temporal" in value and (value[-1] == "start" or value[-1] == "end"):
            return True
    return False


@register.filter
def links_rel_first(value):
    if isinstance(value, list):
        if len(value) > 1 and "links" in value and value[-2] == "0" and value[-1] == "rel":
            return True
    return False


@register.filter
def links_rel_subsequent(value):
    if isinstance(value, list):
        if len(value) > 1 and "links" in value and value[-2] != "0" and value[-1] == "rel":
            return True
    return False
