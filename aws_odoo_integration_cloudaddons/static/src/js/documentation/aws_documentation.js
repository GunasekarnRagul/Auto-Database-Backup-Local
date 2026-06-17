/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class NextcloudDocumentation extends Component {
    setup() {
        this.action = useService("action");
        this.state = useState({
            activeSection: 'overview',
            sidebarCollapsed: localStorage.getItem('gdd_sidebar_collapsed') === 'true',
        });
    }

    onSidebarClick(section) {
        this.state.activeSection = section;
    }

    toggleSidebar() {
        this.state.sidebarCollapsed = !this.state.sidebarCollapsed;
        localStorage.setItem('gdd_sidebar_collapsed', this.state.sidebarCollapsed);
    }
}

NextcloudDocumentation.template = "nextcloud_odoo_integration.Documentation";

registry.category("actions").add("nextcloud_odoo_integration.documentation", NextcloudDocumentation);
