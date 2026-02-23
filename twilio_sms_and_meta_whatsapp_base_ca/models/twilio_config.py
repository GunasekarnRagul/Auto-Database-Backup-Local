# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import json
import logging

_logger = logging.getLogger(__name__)

PROVIDER_LIST = [
    ('twilio', 'Twilio'),
]


class TwilioConfig(models.Model):
    _name = "twilio.config"
    _description = "SMS Gateway Configuration"
    _rec_name = "name"

    # ---------------------------
    # COMMON FIELDS
    # ---------------------------
    name = fields.Char(string="Account Label", required=True, default="SMS Account")
    provider = fields.Selection(PROVIDER_LIST, string="SMS Provider", default='twilio', required=True)

    connection_status = fields.Selection(
        [('unknown', 'Unknown'), ('connected', 'Connected'), ('failed', 'Failed')],
        default='unknown', readonly=True
    )
    last_tested = fields.Datetime(readonly=True)
    account_name = fields.Char(string="Account Name", readonly=True)
    account_type = fields.Char(readonly=True)
    account_balance = fields.Char(readonly=True)
    total_messages_sent = fields.Char(readonly=True)
    current_bill_amount = fields.Char(readonly=True)

    # ---------------------------
    # TWILIO FIELDS
    # ---------------------------
    account_sid = fields.Char(string="Account SID")
    auth_token = fields.Char(string="Auth Token")
    twilio_number = fields.Char(string="Twilio Number (SMS)")

    # ---------------------------
    # ACTIONS
    # ---------------------------
    @api.model
    def action_open_settings(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'SMS Settings',
            'res_model': 'twilio.config',
            'view_mode': 'list,form',
            'target': 'current',
        }

    def action_test_connection(self):
        self.ensure_one()
        method = getattr(self, f'_test_{self.provider}_connection', None)
        if method:
            return method()
        raise UserError(_("Connection test not implemented for %s") % self.provider)

    def update_twilio_usage(self):
        """Refresh stats"""
        self.ensure_one()
        method = getattr(self, f'_refresh_{self.provider}_stats', None)
        if method:
            return method()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_disconnect(self):
        self.write({
            'connection_status': 'unknown', 'last_tested': False,
            'account_type': False, 'account_balance': False,
            'total_messages_sent': False, 'current_bill_amount': False,
            'account_name': False,
        })
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def send_sms(self, to_number, body):
        """Universal SMS send dispatcher. Returns (success: bool, info: str)."""
        self.ensure_one()
        method = getattr(self, f'_send_{self.provider}', None)
        if not method:
            return False, f"Send not implemented for {self.provider}"
        return method(to_number, body)

    # =============================================
    # 1. TWILIO
    # =============================================
    def _test_twilio_connection(self):
        self.ensure_one()
        if not self.account_sid or not self.auth_token:
            raise UserError(_("Please enter Twilio Account SID and Auth Token."))
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}.json"
        try:
            res = requests.get(url, auth=(self.account_sid, self.auth_token), timeout=10)
            data = res.json()
            if res.status_code != 200:
                self.write({'connection_status': 'failed'})
                raise UserError(data.get("message", "Invalid credentials."))
            self.write({
                'connection_status': 'connected', 'last_tested': fields.Datetime.now(),
                'account_type': data.get("type", "N/A"),
                'account_name': data.get("friendly_name", "N/A"),
            })
            self._refresh_twilio_stats()
            return {'type': 'ir.actions.client', 'tag': 'reload'}
        except requests.exceptions.RequestException as e:
            self.write({'connection_status': 'failed'})
            raise UserError(_("Connection Failed: %s") % str(e))

    def _refresh_twilio_stats(self):
        self.ensure_one()
        if not self.account_sid or not self.auth_token:
            return
        auth = (self.account_sid, self.auth_token)
        balance, currency = "0", ""
        try:
            r = requests.get(f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Balance.json",
                             auth=auth, timeout=10)
            if r.status_code == 200:
                d = r.json()
                balance, currency = d.get("balance", "0"), d.get("currency", "")
        except Exception:
            pass
        total_count, total_price = 0, 0.0
        sms_cats = {'sms', 'sms-outbound', 'sms-inbound', 'messages', 'sms-outbound-longcode'}
        try:
            r = requests.get(f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Usage/Records/Today.json",
                             auth=auth, timeout=10)
            if r.status_code == 200:
                for rec in r.json().get("usage_records", []):
                    if rec.get("category") in sms_cats:
                        try: total_count += int(rec.get("count") or 0)
                        except: pass
                        try: total_price += float(rec.get("price") or 0)
                        except: pass
        except Exception:
            pass
        self.write({
            'account_balance': f"{balance} {currency}",
            'total_messages_sent': str(total_count),
            'current_bill_amount': f"${total_price:.4f}" if total_price else "0",
        })
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def _send_twilio(self, to_number, body):
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        r = requests.post(url, data={'From': self.twilio_number, 'To': to_number, 'Body': body},
                          auth=(self.account_sid, self.auth_token), timeout=30)
        if r.status_code in (200, 201):
            d = r.json()
            return True, f"SID: {d.get('sid', 'N/A')} | Status: {d.get('status', 'N/A')}"
        try: return False, r.json().get('message', r.text)
        except: return False, r.text
