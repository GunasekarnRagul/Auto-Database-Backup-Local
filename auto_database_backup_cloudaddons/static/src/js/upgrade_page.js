/** @odoo-module **/
import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";

const MODULES = [
    {
        id: "gdrive",
        name: "Cloud Storage | Google Drive Database Backups | Smart Retention | Manual & Auto Backups",
        icon: "fa-google",
        iconColor: "#4285F4",
        bg: "linear-gradient(135deg,#4285F4 0%,#34A853 100%)",
        badge: "Google Drive",
        description:
            "Automate encrypted database snapshots directly to Google Drive using OAuth 2.0. Includes smart retention, selective filestore inclusion, and 1-click manual triggers.",
        features: ["OAuth 2.0 Security", "Smart Retention", "Direct Drive Sync", "1-Click Trigger"],
        link: "https://apps.odoo.com/apps/modules/19.0/auto_backup_db_gdrive",
        versions: "v17.0 – 19.0",
    },
    {
        id: "aws",
        name: "Cloud Storage | AWS S3 Database Backups | Smart Retention | Manual & Auto Backups",
        icon: "fa-cloud",
        iconColor: "#FF9900",
        bg: "linear-gradient(135deg,#FF9900 0%,#c45100 100%)",
        badge: "AWS S3",
        description:
            "Protect your business data with Amazon S3. Automate database snapshots with IAM-secure credentials, smart retention policies, and 1-click manual triggers.",
        features: ["IAM Credentials", "Smart Retention", "Bucket Management", "1-Click Trigger"],
        link: "https://apps.odoo.com/apps/modules/19.0/auto_backup_db_aws_s3",
        versions: "v17.0 – 19.0",
    },
    {
        id: "dropbox",
        name: "Cloud Storage | Dropbox Database Backups | Smart Retention | Manual & Auto Backups",
        icon: "fa-dropbox",
        iconColor: "#0061FF",
        bg: "linear-gradient(135deg,#0061FF 0%,#00b4d8 100%)",
        badge: "Dropbox",
        description:
            "Automate database snapshots directly to Dropbox. Complete with smart storage retention and 1-click manual triggers. Works with any Dropbox plan.",
        features: ["OAuth 2.0 Auth", "Smart Retention", "Folder Management", "1-Click Trigger"],
        link: "https://apps.odoo.com/apps/modules/19.0/auto_backup_db_dropbox",
        versions: "v17.0 – 19.0",
    },
    {
        id: "onedrive",
        name: "Cloud Storage | OneDrive Database Backups | Smart Retention | Manual & Auto Backups",
        icon: "fa-windows",
        iconColor: "#0078D4",
        bg: "linear-gradient(135deg,#0078D4 0%,#50b8e7 100%)",
        badge: "OneDrive",
        description:
            "Secure Odoo backups to Microsoft OneDrive using Microsoft OAuth. Smart retention, compressed archives, and seamless 1-click manual backup.",
        features: ["Microsoft OAuth", "Smart Retention", "OneDrive Folders", "1-Click Trigger"],
        link: "https://apps.odoo.com/apps/modules/19.0/auto_backup_db_onedrive",
        versions: "v17.0 – 19.0",
    },
    {
        id: "nextcloud",
        name: "Cloud Storage | Nextcloud Database Backups | Smart Retention | Manual & Auto Backups",
        icon: "fa-server",
        iconColor: "#0082C9",
        bg: "linear-gradient(135deg,#0082C9 0%,#00D2D3 100%)",
        badge: "Nextcloud",
        description:
            "Self-hosted cloud storage for maximum data sovereignty. Back up your Odoo database directly to your own Nextcloud server with WebDAV integration.",
        features: ["WebDAV Auth", "Self-hosted Storage", "Smart Retention", "1-Click Trigger"],
        link: "https://apps.odoo.com/apps/modules/19.0/auto_backup_db_nextcloud",
        versions: "v17.0 – 19.0",
    },
    {
        id: "multicloud",
        name: "Cloud Storage | Multi-Cloud Database Backups | Smart Retention | Manual & Auto Backups",
        icon: "fa-th-large",
        iconColor: "#7c3aed",
        bg: "linear-gradient(135deg,#7c3aed 0%,#db2777 100%)",
        badge: "All 5 Providers",
        description:
            "The most comprehensive backup module for Odoo. Automate encrypted snapshots to AWS S3, Google Drive, Dropbox, OneDrive and Nextcloud with independent scheduling per provider.",
        features: ["5 Providers in One", "Per-provider Scheduling", "Dual Formats", "Selective DB Scope"],
        link: "https://apps.odoo.com/apps/modules/19.0/auto_backup_db_cloud",
        versions: "v17.0 – 19.0",
        featured: true,
    },
];

const COMPARISON = [
    { feature: "Server Crash Survival", local: "❌ Lost if server fails", cloud: "✅ Guaranteed Survival (Off-site)" },
    { feature: "Ransomware Protection", local: "❌ Vulnerable (Local disks encrypt)", cloud: "✅ Protected (Cloud versioning / immutable)" },
    { feature: "Disaster Recovery Time", local: "⏳ Days (Rebuilding server)", cloud: "⚡ Minutes (1-Click Fetch & Restore)" },
    { feature: "Storage Capacity limits", local: "⚠️ Limited by hard drive size", cloud: "✅ Infinite Scalability" },
    { feature: "Off-site Redundancy", local: "❌ None (Single location)", cloud: "✅ Multi-Region Availability" },
    { feature: "Hardware Failure Risk", local: "⚠️ High (Disk Corruptions)", cloud: "✅ Zero (99.9999% Cloud SLA)" },
    { feature: "File Encryption (At Rest)", local: "❌ Unencrypted ZIPs", cloud: "✅ AES-256 Bank-Grade Encryption" },
    { feature: "Automated Lifecycle Rules", local: "❌ Manual folder cleanup", cloud: "✅ Smart Retention (Auto-delete old)" },
    { feature: "Data Compliance (GDPR)", local: "⚠️ Manual compliance required", cloud: "✅ Enterprise Compliant Data Centers" },
    { feature: "Server Performance Impact", local: "⚠️ High IOPS (Disk heavy)", cloud: "⚡ Zero Lag (Background streaming)" },
    { feature: "Multi-Database Handling", local: "⚠️ Eats up local disk fast", cloud: "✅ Unlimited DBs organized cleanly" },
    { feature: "Accessibility", local: "❌ Only via SSH / Server Access", cloud: "✅ Access from any web browser" },
    { feature: "Maintenance & Upkeep", local: "⚠️ Manual drive replacements", cloud: "✅ Fully Managed by Cloud Provider" },
    { feature: "Setup Process", local: "⚠️ Requires Linux permissions", cloud: "⚡ 1-Click OAuth Authentication" },
    { feature: "Price", local: "🆓 Free (This Module)", cloud: "💎 Premium (One-time purchase)" },
];

export class AutoBackupUpgradePage extends Component {
    static template = "auto_database_backup_cloudaddons.UpgradePage";
    setup() {
        this.modules = MODULES;
        this.comparison = COMPARISON;
    }
}

registry.category("actions").add("auto_database_backup_cloudaddons.upgrade_page", AutoBackupUpgradePage);
