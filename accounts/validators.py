import re

from django.core.exceptions import ValidationError


class ComplexityValidator:
    """Enforces character-class composition on top of Django's built-in
    validators (length/common-password/similarity/numeric-only)."""

    SPECIAL_CHARS = r"""!@#$%^&*()_+\-=\[\]{};:'"\\|,.<>/?~`"""

    def validate(self, password, user=None):
        errors = []
        if not re.search(r'[A-Z]', password):
            errors.append('Password must contain at least one uppercase letter.')
        if not re.search(r'[a-z]', password):
            errors.append('Password must contain at least one lowercase letter.')
        if not re.search(r'\d', password):
            errors.append('Password must contain at least one number.')
        if not re.search(f'[{re.escape(self.SPECIAL_CHARS)}]', password):
            errors.append('Password must contain at least one special character (e.g. !@#$%^&*).')
        if errors:
            raise ValidationError(errors, code='password_complexity')

    def get_help_text(self):
        return (
            'Your password must contain at least one uppercase letter, one lowercase '
            'letter, one number, and one special character (e.g. !@#$%^&*).'
        )
