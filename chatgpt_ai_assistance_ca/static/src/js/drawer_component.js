/** @odoo-module **/

import { Component, useState, onMounted } from "@odoo/owl";

export class DrawerComponent extends Component {
    static template = "chatgpt_ai_assistance_ca.DrawerComponent";
    static props = {
        activeConversationId: { type: [Number, { value: null }], optional: true },
        onSelectConversation: { type: Function },
        onNewConversation: { type: Function },
    };

    setup() {
        this.state = useState({
            is_open: localStorage.getItem('chatgpt_ai_drawer_open') !== 'false',
            conversations: [],
            favorites: [],
            editing_id: null,
            edit_name: '',
        });

        onMounted(async () => {
            await this.loadConversations();
            await this._loadFavorites();
        });
    }

    async loadConversations() {
        try {
            const resp = await fetch('/chatgpt_ai/get_conversations', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    jsonrpc: '2.0', method: 'call', id: 1, params: {}
                }),
            });
            const data = await resp.json();
            this.state.conversations = data.result || [];
        } catch (e) {
            console.error('Failed to load conversations:', e);
        }
    }

    async _loadFavorites() {
        try {
            const resp = await fetch('/chatgpt_ai/get_favorites', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    jsonrpc: '2.0', method: 'call', id: 1, params: {}
                }),
            });
            const data = await resp.json();
            this.state.favorites = data.result || [];
        } catch (e) {
            console.error('Failed to load favorites:', e);
        }
    }

    toggleDrawer() {
        this.state.is_open = !this.state.is_open;
        localStorage.setItem('chatgpt_ai_drawer_open', String(this.state.is_open));
    }

    getGroupedConversations() {
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        const yesterday = new Date(today);
        yesterday.setDate(today.getDate() - 1);
        const weekStart = new Date(today);
        weekStart.setDate(today.getDate() - today.getDay());

        const groups = {
            'Today': [],
            'Yesterday': [],
            'This Week': [],
            'Earlier': [],
        };

        for (const c of this.state.conversations) {
            const d = new Date(c.create_date);
            d.setHours(0, 0, 0, 0);
            if (d >= today) groups['Today'].push(c);
            else if (d >= yesterday) groups['Yesterday'].push(c);
            else if (d >= weekStart) groups['This Week'].push(c);
            else groups['Earlier'].push(c);
        }

        return Object.entries(groups).filter(([, items]) => items.length > 0);
    }

    formatRelativeTime(isoDate) {
        if (!isoDate) return '';
        const d = new Date(isoDate);
        const diff = Date.now() - d.getTime();
        const hours = Math.floor(diff / 3600000);
        if (hours < 1) return 'Just now';
        if (hours < 24) return `${hours}h ago`;
        const days = Math.floor(hours / 24);
        if (days === 1) return 'Yesterday';
        return d.toLocaleDateString('en', { month: 'short', day: 'numeric' });
    }

    truncateName(name) {
        if (!name) return '';
        return name.length > 36 ? name.slice(0, 36) + '…' : name;
    }

    selectConversation(convId) {
        this.props.onSelectConversation(convId);
    }

    newChat() {
        this.props.onNewConversation();
    }

    startRename(conv, ev) {
        ev.stopPropagation();
        this.state.editing_id = conv.id;
        this.state.edit_name = conv.name;
    }

    handleRenameInput(ev) {
        this.state.edit_name = ev.target.value;
    }

    async confirmRename(conv) {
        if (!this.state.edit_name.trim()) {
            this.state.editing_id = null;
            return;
        }
        try {
            await fetch('/web/dataset/call_kw', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    jsonrpc: '2.0', method: 'call', id: 1,
                    params: {
                        model: 'ai.conversation',
                        method: 'write',
                        args: [[conv.id], { name: this.state.edit_name }],
                        kwargs: {},
                    },
                }),
            });
            conv.name = this.state.edit_name;
        } catch (e) {
            console.error('Rename failed:', e);
        }
        this.state.editing_id = null;
    }

    handleRenameKeydown(conv, ev) {
        if (ev.key === 'Enter') {
            this.confirmRename(conv);
        }
    }

    async deleteConversation(id, ev) {
        ev.stopPropagation();
        if (!window.confirm('Delete this conversation?')) return;
        try {
            await fetch('/web/dataset/call_kw', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    jsonrpc: '2.0', method: 'call', id: 1,
                    params: {
                        model: 'ai.conversation',
                        method: 'unlink',
                        args: [[id]],
                        kwargs: {},
                    },
                }),
            });
            this.state.conversations = this.state.conversations.filter(c => c.id !== id);
        } catch (e) {
            console.error('Delete failed:', e);
        }
    }

    async toggleFavorite(conv, ev) {
        ev.stopPropagation();
        try {
            await fetch('/web/dataset/call_kw', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    jsonrpc: '2.0', method: 'call', id: 1,
                    params: {
                        model: 'ai.conversation',
                        method: 'write',
                        args: [[conv.id], { is_favorite: !conv.is_favorite }],
                        kwargs: {},
                    },
                }),
            });
            conv.is_favorite = !conv.is_favorite;
        } catch (e) {
            console.error('Toggle favorite failed:', e);
        }
    }

    selectFavoritePrompt(promptText) {
        // Dispatch a custom event that parent can listen to
        const event = new CustomEvent('chatgpt-prefill-input', {
            detail: { text: promptText },
            bubbles: true,
        });
        document.dispatchEvent(event);
    }
}
