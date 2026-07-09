# -*- coding: utf-8 -*-
import os
import logging

from odoo import http
from odoo.http import request, content_disposition

_logger = logging.getLogger(__name__)


class AutoBackupController(http.Controller):

    @http.route(
        '/auto_backup/download/<int:history_id>',
        type='http',
        auth='user',
        methods=['GET'],
    )
    def download_backup(self, history_id, **kwargs):
        """Stream a backup file from disk to the browser."""
        history = request.env['auto.backup.history'].browse(history_id)
        if not history.exists():
            return request.not_found()

        # Access check — must be an internal user (admin)
        if not request.env.user.has_group('base.group_system'):
            return request.not_found()

        full_path = history.full_path
        if not full_path or not os.path.isfile(full_path):
            return request.not_found()

        filename = history.filename or os.path.basename(full_path)

        def file_generator(path, chunk_size=8192):
            with open(path, 'rb') as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk

        headers = [
            ('Content-Type', 'application/octet-stream'),
            ('Content-Disposition', content_disposition(filename)),
            ('Content-Length', str(os.path.getsize(full_path))),
        ]

        return request.make_response(
            file_generator(full_path),
            headers=headers,
        )
