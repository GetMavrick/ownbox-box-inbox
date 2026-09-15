"""Airtable REST client — a thin, generic CRUD wrapper over the Airtable Web API.

Reusable infrastructure (lives in core/, no reel/gtm specifics): the field
mapping and sync policy belong to the department that uses it
(marketing/content_machine/reel/airtable_sync.py). BYOK via AIRTABLE_* env; inert until
configured. The platform is poll-only, so Airtable→AIOS is a periodic sweep, not
a webhook.
"""
from core.airtable.client import (
    AirtableError, is_configured, list_records, create_record, update_record,
    get_record, delete_record, append_attachments,
    get_base_schema, create_table, create_field,   # Metadata API (schema-only, owner-triggered)
)

__all__ = ["AirtableError", "is_configured", "list_records", "create_record",
           "update_record", "get_record", "delete_record", "append_attachments",
           "get_base_schema", "create_table", "create_field"]
