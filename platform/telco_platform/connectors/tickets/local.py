"""File-backed ticket store holding cases in their native TMF621 shape (spec S6)."""

from ..base import register_connector
from .base import FileTicketStore


@register_connector("tickets", "local")
class LocalTickets(FileTicketStore):
    system_dir = "cases"
