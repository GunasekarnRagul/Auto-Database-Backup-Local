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
