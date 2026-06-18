/** @odoo-module **/
/**
 * Patches the ir.attachment form view so that clicking the
 * "Upload to Nextcloud" button auto-saves any pending changes
 * before running the server action — eliminating the
 * "Please click on the save button first" warning.
 */

import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";

patch(FormController.prototype, {
    /**
     * Before dispatching any button click, check if the button is our
     * upload button. If so, save the record first, then execute the action.
     */
    async beforeExecuteActionButton(clickParams) {
        if (
            clickParams.name === "action_sync_to_drive" &&
            this.model.root.isDirty
        ) {
            const saved = await this.model.root.save();
            if (!saved) {
                return false; // Abort if save failed
            }
        }
        return super.beforeExecuteActionButton(clickParams);
    },
});
