from . import models
from . import controllers
from . import wizard


def pre_init_hook(env):
    # Rename old module records in ir_model_data to prevent unique constraint violations on reinstall
    env.cr.execute("""
        UPDATE ir_model_data
        SET module = 'nextcloud_odoo_integration'
        WHERE module = 'nextcloud_odoo_integration'
    """)
    # Also rename the XML IDs of the model configurations if they were renamed
    env.cr.execute("""
        UPDATE ir_model_data
        SET name = REPLACE(name, 'nextcloud_config_', 'nextcloud_config_')
        WHERE module = 'nextcloud_odoo_integration' AND name LIKE 'nextcloud_config_%'
    """)
    # If the records exist in nextcloud_model_config but not in ir_model_data, link them to avoid UniqueViolation
    env.cr.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = 'nextcloud_model_config'
        )
    """)
    if env.cr.fetchone()[0]:
        env.cr.execute("""
            INSERT INTO ir_model_data (module, name, model, res_id, noupdate)
            SELECT 
                'nextcloud_odoo_integration', 
                'nextcloud_config_' || REPLACE(res_model, '.', '_'),
                'nextcloud.model.config', 
                id, 
                true
            FROM nextcloud_model_config
            ON CONFLICT (module, name) DO NOTHING
        """)


def post_init_hook(env):
    """
    Automatically decode any %20-encoded paths stored in nextcloud_file_id
    that were written by older versions of this module.

    This runs silently after every install/upgrade.  If there are no bad
    records the function is a fast no-op (no DB writes occur).
    """
    try:
        result = env['nextcloud.migration'].fix_encoded_file_ids()
        if result.get('total', 0) > 0:
            import logging
            logging.getLogger(__name__).info(
                "Nextcloud post-upgrade path migration: fixed %d record(s) "
                "(%d nextcloud.file, %d ir.attachment)",
                result['total'],
                result['nextcloud_file_fixed'],
                result['ir_attachment_fixed'],
            )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "Nextcloud post-upgrade path migration failed (non-fatal): %s", e
        )
