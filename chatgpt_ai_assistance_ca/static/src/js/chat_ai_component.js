/** @odoo-module **/

import { Component, useState, onMounted, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { ChatMessage } from "@chatgpt_ai_assistance_ca/js/chat_message";
import { DrawerComponent } from "@chatgpt_ai_assistance_ca/js/drawer_component";

export class ChatAIComponent extends Component {
    static template = "chatgpt_ai_assistance_ca.ChatAIComponent";
    static components = { DrawerComponent, ChatMessage };

    setup() {
        this.state = useState({
            messages: [],
            conversation_id: null,
            loading: false,
            input_text: '',
            active_agent: null,
            agents: [],
            show_agent_dropdown: false,
            drawer_open: true,
        });
        this.messageThreadRef = useRef('messageThread');
        this.inputRef = useRef('chatInput');

        onMounted(() => {
            this._loadAgents();
            // Listen for favorite prompt prefill events
            document.addEventListener('chatgpt-prefill-input', (ev) => {
                this.state.input_text = ev.detail.text;
                if (this.inputRef.el) {
                    this.inputRef.el.value = ev.detail.text;
                    this.inputRef.el.focus();
                }
            });
        });
    }

    async _loadAgents() {
        try {
            const result = await this._rpc('/web/dataset/call_kw', {
                model: 'ai.agent',
                method: 'search_read',
                args: [[['active', '=', true]]],
                kwargs: { fields: ['id', 'name', 'is_default'], limit: 50 },
            });
            this.state.agents = result || [];
            this.state.active_agent = this.state.agents.find(a => a.is_default)
                || this.state.agents[0]
                || null;
        } catch (e) {
            console.error('Failed to load agents:', e);
        }
    }

    async sendMessage() {
        const text = this.state.input_text.trim();
        if (!text || this.state.loading) return;

        // Optimistic UI: add user message immediately
        this.state.messages.push({
            role: 'user',
            content: text,
            id: Date.now(),
        });
        this.state.input_text = '';
        this.state.loading = true;
        // Reset textarea height
        if (this.inputRef.el) {
            this.inputRef.el.value = '';
            this.inputRef.el.style.height = 'auto';
        }
        this._scrollToBottom();

        try {
            const resp = await fetch('/chatgpt_ai/send_message', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    jsonrpc: '2.0',
                    method: 'call',
                    id: 1,
                    params: {
                        conversation_id: this.state.conversation_id,
                        message: text,
                    },
                }),
            });
            const data = await resp.json();
            const result = data.result;

            if (result.error) {
                this.state.messages.push({
                    role: 'assistant',
                    content: result.error,
                    response_format: 'error',
                    records: [],
                    fields: [],
                    id: Date.now(),
                });
            } else {
                this.state.conversation_id = result.conversation_id;
                this.state.messages.push({
                    role: 'assistant',
                    content: result.narration,
                    response_format: result.response_format,
                    records: result.records || [],
                    fields: result.fields || [],
                    id: result.message_id,
                });
            }
        } catch (e) {
            this.state.messages.push({
                role: 'assistant',
                content: `Network error: ${e.message}`,
                response_format: 'error',
                records: [],
                fields: [],
                id: Date.now(),
            });
        } finally {
            this.state.loading = false;
            this._scrollToBottom();
        }
    }

    async loadConversation(conversation_id) {
        if (!conversation_id) return;
        this.state.loading = true;
        try {
            const resp = await fetch('/chatgpt_ai/get_messages', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    jsonrpc: '2.0',
                    method: 'call',
                    id: 1,
                    params: { conversation_id },
                }),
            });
            const data = await resp.json();
            const msgs = data.result;
            if (Array.isArray(msgs)) {
                this.state.messages = msgs.map(m => ({
                    ...m,
                    records: m.raw_data ? JSON.parse(m.raw_data) : [],
                    fields: [],
                }));
            }
            this.state.conversation_id = conversation_id;
            this._scrollToBottom();
        } catch (e) {
            console.error('Failed to load conversation:', e);
        } finally {
            this.state.loading = false;
        }
    }

    newConversation() {
        this.state.messages = [];
        this.state.conversation_id = null;
        if (this.inputRef.el) {
            this.inputRef.el.focus();
        }
    }

    handleKeyDown(ev) {
        if (ev.key === 'Enter' && !ev.shiftKey) {
            ev.preventDefault();
            this.sendMessage();
        }
    }

    handleInput(ev) {
        this.state.input_text = ev.target.value;
        // Auto-grow textarea
        ev.target.style.height = 'auto';
        ev.target.style.height = Math.min(ev.target.scrollHeight, 200) + 'px';
    }

    selectSuggestion(text) {
        this.state.input_text = text;
        if (this.inputRef.el) {
            this.inputRef.el.value = text;
            this.inputRef.el.focus();
        }
    }

    toggleAgentDropdown() {
        this.state.show_agent_dropdown = !this.state.show_agent_dropdown;
    }

    selectAgent(agent) {
        this.state.active_agent = agent;
        this.state.show_agent_dropdown = false;
    }

    _scrollToBottom() {
        setTimeout(() => {
            const el = this.messageThreadRef.el;
            if (el) el.scrollTop = el.scrollHeight;
        }, 50);
    }

    async _rpc(url, params) {
        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                jsonrpc: '2.0',
                method: 'call',
                id: 1,
                params,
            }),
        });
        const data = await resp.json();
        return data.result;
    }
}

registry.category("actions").add("ChatAIComponent", ChatAIComponent);
