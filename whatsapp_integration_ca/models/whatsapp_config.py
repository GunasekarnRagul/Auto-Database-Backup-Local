# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import json
import logging

_logger = logging.getLogger(__name__)


class WhatsAppConfig(models.Model):
    _name = "whatsapp.config"
    _description = "WhatsApp Cloud API Configuration"
    _rec_name = "name"

    # Allow only 1 record
    _sql_constraints = [
        ('single_record_check', 'unique(id)', 'Only one WhatsApp Configuration record is allowed.')
    ]

    name = fields.Char(default="WhatsApp Settings")
    
    # Account Info (from API)
    verified_name = fields.Char(string="Verified Name", readonly=True)
    whatsapp_business_account_id = fields.Char(
        string="WABA ID", 
        help="WhatsApp Business Account ID. Find it in Meta Business Suite URL (asset_id parameter).")

    # Credentials
    phone_number_id = fields.Char(string="Phone Number ID")
    access_token = fields.Char(string="Access Token")
    api_version = fields.Selection(
        [
            ('v17.0', 'v17.0'),
            ('v18.0', 'v18.0'),
            ('v19.0', 'v19.0'),
            ('v20.0', 'v20.0'),
            ('v21.0', 'v21.0'),
            ('v22.0', 'v22.0'),
            ('v23.0', 'v23.0'),
            ('v24.0', 'v24.0 (Recommended)'),
        ],
        string="API Version", 
        default="v24.0", 
        required=True,
        help="Select the Facebook Graph API version."
    )

    # Status
    connection_status = fields.Selection(
        [('unknown', 'Unknown'), ('connected', 'Connected'), ('failed', 'Failed')],
        default='unknown',
        readonly=True
    )
    last_tested = fields.Datetime(readonly=True)

    # Extra API Info
    display_phone_number = fields.Char(string="Phone Number", readonly=True)
    quality_rating = fields.Char(string="Quality Rating", readonly=True)
    account_status = fields.Char(string="Account Status", readonly=True)
    messaging_limit = fields.Char(string="Messaging Limit", readonly=True)
    is_official_business = fields.Char(string="Official Business", readonly=True)
    account_mode = fields.Char(string="Account Mode", readonly=True)

    # ------------------------------------------------------------------------------
    # 🔥 1. Auto-refresh on form open
    # ------------------------------------------------------------------------------
    @api.model
    def action_open_settings(self):
        config = self.search([], limit=1)

        if not config:
            config = self.create({'name': 'WhatsApp Settings'})

        return {
            'type': 'ir.actions.act_window',
            'name': 'WhatsApp Settings',
            'res_model': 'whatsapp.config',
            'view_mode': 'form',
            'res_id': config.id,
            'target': 'current',
        }

    # ------------------------------------------------------------------------------
    # 🔥 2. Fetch Live WhatsApp Info
    # ------------------------------------------------------------------------------
    def update_whatsapp_info(self):
        self.ensure_one()

        if not self.api_version:
             self.api_version = 'v18.0'

        base_url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }

        try:
            response = requests.get(
                base_url,
                headers=headers,
                params={
                    'fields': 'verified_name,quality_rating,display_phone_number,'
                              'status,messaging_limit_tier,is_official_business_account,account_mode'
                },
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                self.write({
                    'verified_name': data.get('verified_name', 'N/A'),
                    'display_phone_number': data.get('display_phone_number', 'N/A'),
                    'quality_rating': data.get('quality_rating', 'N/A'),
                    'account_status': data.get('status', 'N/A'),
                    'messaging_limit': data.get('messaging_limit_tier', 'N/A'),
                    'is_official_business': 'Yes' if data.get('is_official_business_account') else 'No',
                    'account_mode': data.get('account_mode', 'N/A'),
                })
        except Exception:
            pass

    # ------------------------------------------------------------------------------
    # 🔥 3. Test Connection
    # ------------------------------------------------------------------------------
    def action_test_connection(self):
        self.ensure_one()

        if not self.phone_number_id or not self.access_token:
            raise UserError("Please enter both Phone Number ID and Access Token.")

        if not self.api_version:
            raise UserError(_("Please specify the API version."))

        # Also fetch WABA ID
        base_url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }

        try:
            response = requests.get(
                base_url,
                headers=headers,
                params={
                    'fields': 'verified_name,quality_rating,display_phone_number,'
                              'status,messaging_limit_tier,is_official_business_account,account_mode'
                },
                timeout=30
            )

            if response.status_code != 200:
                error_data = response.json()
                error = error_data.get('error', {})
                raise UserError(error.get('message', 'Invalid credentials.'))

            data = response.json()

            # Save info
            self.write({
                'connection_status': 'connected',
                'last_tested': fields.Datetime.now(),
                'verified_name': data.get('verified_name', 'N/A'),
                'display_phone_number': data.get('display_phone_number', 'N/A'),
                'quality_rating': data.get('quality_rating', 'N/A'),
                'account_status': data.get('status', 'N/A'),
                'messaging_limit': data.get('messaging_limit_tier', 'N/A'),
                'is_official_business': 'Yes' if data.get('is_official_business_account') else 'No',
                'account_mode': data.get('account_mode', 'N/A'),
            })

            return {'type': 'ir.actions.client', 'tag': 'reload'}

        except requests.exceptions.RequestException as e:
            self.write({'connection_status': 'failed', 'last_tested': fields.Datetime.now()})
            raise UserError(f"Connection Failed: {str(e)}")
        except Exception as e:
            self.write({'connection_status': 'failed', 'last_tested': fields.Datetime.now()})
            raise UserError(f"Connection Failed: {str(e)}")

    # ------------------------------------------------------------------------------
    # 🔥 4. Disconnect
    # ------------------------------------------------------------------------------
    def action_disconnect(self):
        self.write({
            'phone_number_id': False,
            'access_token': False,
            'whatsapp_business_account_id': False,

            'connection_status': 'unknown',
            'last_tested': False,
            'verified_name': False,
            'display_phone_number': False,
            'quality_rating': False,
            'account_status': False,
            'messaging_limit': False,
            'is_official_business': False,
            'account_mode': False,
        })
        return {'type': 'ir.actions.client', 'tag': 'reload'}
        

