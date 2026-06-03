from . import models
from . import controllers
from . import wizard

def pre_init_hook(env):
    # Rename old module records in ir_model_data to prevent unique constraint violations on reinstall
    env.cr.execute("""
        UPDATE ir_model_data
        SET module = 'dropbox_odoo_integration'
        WHERE module = 'one_drive_odoo_integration'
    """)
    # Also rename the XML IDs of the model configurations if they were renamed
    env.cr.execute("""
        UPDATE ir_model_data
        SET name = REPLACE(name, 'one_drive_config_', 'dropbox_config_')
        WHERE module = 'dropbox_odoo_integration' AND name LIKE 'one_drive_config_%'
    """)
    # If the records exist in one_drive_model_config but not in ir_model_data, link them to avoid UniqueViolation
    env.cr.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = 'one_drive_model_config'
        )
    """)
    if env.cr.fetchone()[0]:
        env.cr.execute("""
            INSERT INTO ir_model_data (module, name, model, res_id, noupdate)
            SELECT 
                'dropbox_odoo_integration', 
                'dropbox_config_' || REPLACE(res_model, '.', '_'),
                'one_drive.model.config', 
                id, 
                true
            FROM one_drive_model_config
            ON CONFLICT (module, name) DO NOTHING
        """)
