# -*- coding: utf-8 -*-
"""
One-time migration to fix legacy %20-encoded paths stored in nextcloud_file_id.

Old code (before the encoding fix) sometimes stored URL-encoded paths like:
    /attach/CRM/lead_id_Administrators%20opportunity/file.png

New code stores decoded paths:
    /attach/CRM/lead_id_Administrators opportunity/file.png

This model provides a method to decode all existing records in one shot.
It is safe to run multiple times (unquote of a clean string is a no-op).
"""
import logging
import urllib.parse
from odoo import models, api

_logger = logging.getLogger(__name__)


class NextcloudMigration(models.TransientModel):
    _name = "nextcloud.migration"
    _description = "AWS S3 Path Migration Helper"

    @api.model
    def fix_encoded_file_ids(self):
        """
        Decode any %xx-encoded nextcloud_file_id values in:
          - nextcloud.file
          - ir.attachment

        Run once after upgrading the module, or call from the Odoo shell:
            env['nextcloud.migration'].fix_encoded_file_ids()

        Returns a dict with counts of records updated.
        """
        fixed_files = 0
        fixed_attachments = 0

        # ── nextcloud.file ──────────────────────────────────────────────────────
        all_files = self.env['nextcloud.file'].sudo().with_context(
            active_test=False
        ).search([('nextcloud_file_id', '!=', False)])

        for rec in all_files:
            raw = rec.nextcloud_file_id or ''
            if '%' in raw:
                decoded = urllib.parse.unquote(raw)
                if decoded != raw:
                    try:
                        rec.sudo().with_context(skip_nextcloud_sync=True).write(
                            {'nextcloud_file_id': decoded}
                        )
                        fixed_files += 1
                        _logger.info(
                            "nextcloud.file [%s] fixed: %r → %r", rec.id, raw, decoded
                        )
                    except Exception as e:
                        _logger.warning(
                            "nextcloud.file [%s] could not be fixed: %s", rec.id, e
                        )

        # ── ir.attachment ───────────────────────────────────────────────────────
        all_atts = self.env['ir.attachment'].sudo().search(
            [('nextcloud_file_id', '!=', False)]
        )
        for att in all_atts:
            raw = att.nextcloud_file_id or ''
            if '%' in raw:
                decoded = urllib.parse.unquote(raw)
                if decoded != raw:
                    try:
                        att.sudo().with_context(skip_nextcloud_sync=True).write(
                            {'nextcloud_file_id': decoded}
                        )
                        fixed_attachments += 1
                        _logger.info(
                            "ir.attachment [%s] fixed: %r → %r", att.id, raw, decoded
                        )
                    except Exception as e:
                        _logger.warning(
                            "ir.attachment [%s] could not be fixed: %s", att.id, e
                        )

        result = {
            'nextcloud_file_fixed': fixed_files,
            'ir_attachment_fixed': fixed_attachments,
            'total': fixed_files + fixed_attachments,
        }
        _logger.info("AWS S3 path migration complete: %s", result)
        return result
