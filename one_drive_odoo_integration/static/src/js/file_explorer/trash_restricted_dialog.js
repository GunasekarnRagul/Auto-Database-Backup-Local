/** @odoo-module **/

import { Component } from "@odoo/owl";

/**
 * Custom dialog component to show a beautifully styled restricted action warning 
 * when the user tries to restore/delete items from within a trashed folder.
 */
export class TrashRestrictedDialog extends Component {
    static template = "one_drive_odoo_integration.TrashRestrictedDialog";
    static props = {
        close: Function
    };
}
