"""Configuration module for `django_sqlite` app."""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class SQLiteConfig(AppConfig):
    """Configuration class for `django_sqlite` app.

    To use this configuration in your Django project include
    `django_sqlite.apps` in the :setting:`INSTALLED_APPS` setting.
    """

    name = "django_sqlite"
    verbose_name = _("SQLite utils")

