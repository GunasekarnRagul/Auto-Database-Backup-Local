# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # ─── AWS S3 Integration Fields ──────────────────────────────────────────
    aws_access_key_id = fields.Char(
        string="AWS Access Key ID",
        help="Enter your AWS Access Key ID.",
        config_parameter='aws_odoo_integration_cloudaddons.aws_access_key_id',
    )
    aws_secret_access_key = fields.Char(
        string="AWS Secret Access Key",
        help="Enter your AWS Secret Access Key.",
        config_parameter='aws_odoo_integration_cloudaddons.aws_secret_access_key',
    )
    aws_s3_bucket_name = fields.Char(
        string="S3 Bucket Name",
        help="AWS S3 Bucket where backups will be stored.",
        config_parameter='aws_odoo_integration_cloudaddons.aws_s3_bucket_name',
    )
    aws_region = fields.Char(
        string="AWS Region",
        help="AWS Region where your bucket is located.",
        config_parameter='aws_odoo_integration_cloudaddons.aws_region',
        default='us-east-1',
    )
    aws_is_connection_tested = fields.Boolean(
        string='S3 Connection Tested',
        default=False
    )

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        ICP = self.env['ir.config_parameter'].sudo()
        res.update(
            aws_is_connection_tested=ICP.get_param('aws_odoo_integration_cloudaddons.aws_is_connection_tested') == 'True',
        )
        return res

    # ─── Test Connection ─────────────────────────────────────────────────────
    def action_test_aws_s3_connection(self):
        """Test the AWS S3 connection using the provided credentials."""
        self.ensure_one()

        ICP = self.env['ir.config_parameter'].sudo()
        access_key = ICP.get_param('aws_odoo_integration_cloudaddons.aws_access_key_id')
        secret_key = ICP.get_param('aws_odoo_integration_cloudaddons.aws_secret_access_key')
        bucket_name = ICP.get_param('aws_odoo_integration_cloudaddons.aws_s3_bucket_name')
        region = ICP.get_param('aws_odoo_integration_cloudaddons.aws_region') or 'us-east-1'

        if not (access_key and secret_key and bucket_name):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Incomplete Data'),
                    'message': _('Please Save your credentials first, then click Test Connection.'),
                    'type': 'warning',
                    'sticky': False,
                }
            }

        try:
            import boto3
            from botocore.exceptions import ClientError, NoCredentialsError, EndpointResolutionError
        except ImportError:
            raise UserError(_(
                "The 'boto3' Python library is not installed.\n"
                "Please install it by running: pip install boto3"
            ))

        try:
            s3_client = boto3.client(
                's3',
                aws_access_key_id=access_key.strip(),
                aws_secret_access_key=secret_key.strip(),
                region_name=region.strip(),
            )
            # Try to get bucket location — lightweight call that verifies creds + bucket access
            s3_client.head_bucket(Bucket=bucket_name.strip())

        except ImportError as e:
            raise UserError(_("Missing dependency: %s") % str(e))
        except Exception as e:
            err_str = str(e)
            # botocore may not be installed in all environments; handle gracefully
            if 'NoCredentialsError' in type(e).__name__ or 'NoCredentialsError' in err_str:
                raise UserError(_(
                    "AWS credentials are invalid or missing.\n"
                    "Please check your Access Key ID and Secret Access Key."
                ))
            if 'ClientError' in type(e).__name__:
                if '403' in err_str or 'Forbidden' in err_str:
                    raise UserError(_(
                        "Access Denied (HTTP 403).\n"
                        "The provided credentials do not have permission to access the bucket '%s'.\n"
                        "Please check your IAM permissions."
                    ) % bucket_name.strip())
                if '404' in err_str or 'NoSuchBucket' in err_str:
                    raise UserError(_(
                        "Bucket Not Found (HTTP 404).\n"
                        "The bucket '%s' does not exist in the region '%s'.\n"
                        "Please verify the bucket name and region."
                    ) % (bucket_name.strip(), region.strip()))
                if '301' in err_str or 'PermanentRedirect' in err_str:
                    raise UserError(_(
                        "Region Mismatch.\n"
                        "The bucket '%s' exists but is NOT in the '%s' region.\n"
                        "Please update the AWS Region field to match the bucket's actual region."
                    ) % (bucket_name.strip(), region.strip()))
            if 'EndpointResolutionError' in type(e).__name__ or 'Could not connect' in err_str:
                raise UserError(_(
                    "Cannot connect to AWS.\n"
                    "Please check your internet connection and the AWS Region value."
                ))
            raise UserError(_(
                "Connection to AWS S3 failed:\n%s\n\n"
                "Please verify your credentials, bucket name, and region."
            ) % err_str)

        # ── Success ──────────────────────────────────────────────────────────
        # Save the status to ICP
        ICP.set_param('aws_odoo_integration_cloudaddons.aws_is_connection_tested', 'True')
        
        # Reopen settings to reflect the new visibility
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'res.config.settings',
            'view_mode': 'form',
            'target': 'inline',
            'context': {'module': 'aws_odoo_integration_cloudaddons'},
        }

    def action_reset_aws_s3_connection(self):
        self.ensure_one()
        self.env['ir.config_parameter'].sudo().set_param('aws_odoo_integration_cloudaddons.aws_is_connection_tested', 'False')
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'res.config.settings',
            'view_mode': 'form',
            'target': 'inline',
            'context': {'module': 'aws_odoo_integration_cloudaddons'},
        }

    def action_disconnect_aws_s3(self):
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('aws_odoo_integration_cloudaddons.aws_access_key_id', '')
        ICP.set_param('aws_odoo_integration_cloudaddons.aws_secret_access_key', '')
        ICP.set_param('aws_odoo_integration_cloudaddons.aws_s3_bucket_name', '')
        ICP.set_param('aws_odoo_integration_cloudaddons.aws_is_connection_tested', 'False')
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'res.config.settings',
            'view_mode': 'form',
            'target': 'inline',
            'context': {'module': 'aws_odoo_integration_cloudaddons'},
        }
