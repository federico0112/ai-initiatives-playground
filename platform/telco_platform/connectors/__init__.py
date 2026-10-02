"""Connectors to external systems. Each kind has one interface and several backends.

Importing this package registers the built-in backends.
"""

from . import carrier_portal, crm, inbox, outbox  # noqa: F401
from .base import CONNECTORS, get_connector, register_connector  # noqa: F401
from .tickets import jira, local, salesforce  # noqa: F401
