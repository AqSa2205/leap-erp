"""Who is making the current request, for models that record it on save.

Set by CurrentUserMiddleware for the length of one request and cleared
afterwards. Outside a request (scripts, imports, scheduled jobs) there is no
user, and current_user() returns None.
"""
from contextvars import ContextVar

_current_user = ContextVar('procurement_current_user', default=None)


def current_user():
    user = _current_user.get()
    if user is not None and getattr(user, 'is_authenticated', False):
        return user
    return None


class CurrentUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _current_user.set(getattr(request, 'user', None))
        try:
            return self.get_response(request)
        finally:
            _current_user.reset(token)
