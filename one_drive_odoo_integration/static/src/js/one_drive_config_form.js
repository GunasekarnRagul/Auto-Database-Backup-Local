/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";

/**
 * Patch FormController to show a full-screen loader when the
 * "Root folder sync" (action_sync_all) button is clicked.
 */
patch(FormController.prototype, {
    /**
     * Odoo 17 uses `onClickButton` for arch buttons.
     * @override
     */
    async onClickButton(clickParams) {
        const actionName = clickParams && clickParams.name;
        console.log("GD Sync Debug: Button Clicked - ", actionName);

        if (actionName === 'action_sync_all') {
            const loader = this._showFullScreenLoader("Synchronizing with OneDrive...");
            try {
                // In Odoo 17 patch, we use this._super to call the original method.
                return await this._super(...arguments);
            } finally {
                this._hideFullScreenLoader(loader);
            }
        }
        return this._super(...arguments);
    },

    _showFullScreenLoader(message) {
        const loader = document.createElement('div');
        loader.className = 'gd_fullscreen_loader';
        loader.style.zIndex = '999999'; // Ensure it is above everything
        loader.innerHTML = `
            <div class="gd_fullscreen_loader_content">
                <div class="gd_fullscreen_spinner"></div>
                <span>${message}</span>
            </div>
        `;
        document.body.appendChild(loader);
        return loader;
    },

    _hideFullScreenLoader(loader) {
        if (loader && loader.parentNode) {
            loader.parentNode.removeChild(loader);
        }
    }
});
