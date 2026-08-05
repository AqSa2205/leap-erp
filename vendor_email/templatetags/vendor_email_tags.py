from django import template

from vendor_email.models import VendorEmailMessage

register = template.Library()


@register.inclusion_tag('vendor_email/_review_banner.html')
def vendor_email_review_banner():
    unmatched = VendorEmailMessage.objects.filter(status='unmatched')
    return {'unmatched': unmatched, 'count': unmatched.count()}