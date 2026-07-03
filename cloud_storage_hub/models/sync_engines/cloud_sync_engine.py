# -*- coding: utf-8 -*-
"""
Cloud Sync Engine Dispatcher
Routes sync operations to the correct provider-specific engine.
"""
import logging
from odoo import models, api

_logger = logging.getLogger(__name__)

ENGINE_MODEL_MAP = {
    'aws':       'cloud.sync.aws',
    'gdrive':    'cloud.sync.gdrive',
    'onedrive':  'cloud.sync.onedrive',
    'nextcloud': 'cloud.sync.nextcloud',
    'dropbox':   'cloud.sync.dropbox',
}


class CloudSyncEngine(models.AbstractModel):
    """Abstract dispatcher — call _get_engine(provider_type) to get the provider engine."""
    _name = 'cloud.sync.engine'
    _description = 'Cloud Sync Engine Dispatcher'

    @api.model
    def _get_engine(self, provider_type):
        model_name = ENGINE_MODEL_MAP.get(provider_type)
        if not model_name:
            raise ValueError(f'No sync engine registered for provider_type: {provider_type}')
        return self.env[model_name].sudo()
