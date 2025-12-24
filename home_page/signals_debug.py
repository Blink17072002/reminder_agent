from allauth.socialaccount.signals import pre_social_login, social_account_added, social_account_updated, social_account_removed
from django.dispatch import receiver
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

@receiver(pre_social_login)
def debug_pre_social_login(sender, request, sociallogin, **kwargs):
    logger.info(f"DEBUG: pre_social_login. User: {sociallogin.user}, Account: {sociallogin.account.uid}, Process: {source_process_name(request)}")
    if sociallogin.state.get('process') == 'connect':
        logger.info("DEBUG: Process is CONNECT")
    else:
        logger.info(f"DEBUG: Process is {sociallogin.state.get('process')}")

@receiver(social_account_added)
def debug_social_account_added(sender, request, sociallogin, **kwargs):
    logger.info(f"DEBUG: social_account_added. User: {sociallogin.user}, UID: {sociallogin.account.uid}")

def source_process_name(request):
    return request.GET.get('process') or 'unknown'
