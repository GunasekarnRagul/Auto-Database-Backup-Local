/** @odoo-module **/

import { Component } from "@odoo/owl";

export class ChatMessage extends Component {
    static template = "chatgpt_ai_assistance_ca.ChatMessage";
    static props = {
        message: { type: Object },
    };

    sanitize(html) {
        if (!html) return '';
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');
        doc.querySelectorAll('script, iframe, object, embed').forEach(el => el.remove());
        doc.querySelectorAll('*').forEach(el => {
            [...el.attributes].forEach(attr => {
                if (attr.name.startsWith('on')) el.removeAttribute(attr.name);
            });
        });
        return doc.body.innerHTML;
    }

    get safeContent() {
        return this.sanitize(this.props.message.content || '');
    }

    parseMarkdown(text) {
        if (!text) return '';
        return text
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/g, '<em>$1</em>')
            .replace(/^### (.+)$/gm, '<h4>$1</h4>')
            .replace(/^## (.+)$/gm, '<h3>$1</h3>')
            .replace(/^# (.+)$/gm, '<h2>$1</h2>')
            .replace(/^- (.+)$/gm, '<li>$1</li>')
            .replace(/(<li>[\s\S]*?<\/li>)/g, '<ul>$1</ul>')
            .replace(/<\/ul>\s*<ul>/g, '')
            .replace(/\n/g, '<br>');
    }

    get renderedContent() {
        const msg = this.props.message;
        if (msg.role === 'user') {
            return msg.content || '';
        }
        if (msg.response_format === 'error') {
            return msg.content || '';
        }
        return this.sanitize(this.parseMarkdown(msg.content || ''));
    }

    get hasTableData() {
        const msg = this.props.message;
        return msg.response_format === 'table'
            && msg.records
            && msg.records.length > 0;
    }

    get hasListData() {
        const msg = this.props.message;
        return msg.response_format === 'list'
            && msg.records
            && msg.records.length > 0;
    }

    get hasCardData() {
        const msg = this.props.message;
        return msg.response_format === 'card'
            && msg.records
            && msg.records.length > 0;
    }

    get isError() {
        return this.props.message.response_format === 'error';
    }

    get tableHeaders() {
        const msg = this.props.message;
        if (msg.fields && msg.fields.length) return msg.fields;
        if (msg.records && msg.records.length) {
            return Object.keys(msg.records[0]).filter(k => k !== 'id' && k !== '__model');
        }
        return [];
    }

    get tableRows() {
        const msg = this.props.message;
        return (msg.records || []).slice(0, 25);
    }

    get cardEntries() {
        const msg = this.props.message;
        if (!msg.records || !msg.records.length) return [];
        return Object.entries(msg.records[0]).filter(
            ([k]) => k !== 'id' && k !== '__model'
        );
    }

    getListItemText(row) {
        return Object.entries(row)
            .filter(([k]) => k !== 'id' && k !== '__model')
            .slice(0, 3)
            .map(([, v]) => v)
            .join(' — ');
    }

    getCellValue(row, field) {
        const val = row[field];
        if (val === null || val === undefined) return '';
        if (val === true) return '✓';
        if (val === false) return '✗';
        return String(val);
    }

    exportCSV() {
        const records = this.props.message.records || [];
        if (!records.length) return;
        const headers = this.tableHeaders;
        const rows = records.map(r =>
            headers.map(h => `"${String(r[h] || '').replace(/"/g, '""')}"`).join(',')
        );
        const csv = [headers.join(','), ...rows].join('\n');
        const blob = new Blob([csv], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'export.csv';
        a.click();
        URL.revokeObjectURL(url);
    }

    openRecord(row) {
        if (!row || !row.id) return;
        const modelName = row.__model || '';
        if (!modelName) return;
        const urlSegment = modelName.replace(/\./g, '-');
        window.open(`/odoo/${urlSegment}/${row.id}`, '_blank');
    }

    copyContent() {
        navigator.clipboard.writeText(this.props.message.content || '');
    }

    async saveFavorite() {
        const content = this.props.message.content || '';
        const label = window.prompt('Enter a label for this prompt:', content.slice(0, 30));
        if (!label) return;
        await fetch('/chatgpt_ai/save_favorite', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                jsonrpc: '2.0', method: 'call', id: 1,
                params: { prompt_text: content, label }
            }),
        });
    }
}
