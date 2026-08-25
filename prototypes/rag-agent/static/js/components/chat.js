/**
 * Chat tab component with streaming support
 */

import { API } from '../api.js';

let sessionId = null;
let messages = [];
let isStreaming = false;

/**
 * Initialize chat tab
 */
export function initChat() {
    const form = document.getElementById('chatForm');
    const input = document.getElementById('chatInput');
    const sendBtn = document.getElementById('chatSendBtn');
    const messagesContainer = document.getElementById('chatMessages');
    const sessionIdDisplay = document.getElementById('chatSessionId');
    const newSessionBtn = document.getElementById('newSessionBtn');
    const topKSlider = document.getElementById('chatTopK');
    const topKValue = document.getElementById('chatTopKValue');
    const modelSelect = document.getElementById('chatModel');
    const errorDiv = document.getElementById('chatError');

    // Load available chat models
    loadChatModels(modelSelect);

    // Update top-k display when slider changes
    topKSlider.addEventListener('input', () => {
        topKValue.textContent = topKSlider.value;
    });

    // Handle new session button
    newSessionBtn.addEventListener('click', () => {
        createNewSession(messagesContainer, sessionIdDisplay);
    });

    // Handle form submission
    form.addEventListener('submit', async (e) => {
        e.preventDefault();

        if (isStreaming) return;

        const message = input.value.trim();
        if (!message) return;

        // Clear input and error
        input.value = '';
        hideElement(errorDiv);

        // Show loading state
        setLoading(sendBtn, true);
        isStreaming = true;

        try {
            const topK = parseInt(topKSlider.value, 10);
            const model = modelSelect.value;

            // Add user message to UI
            addMessage(messagesContainer, 'user', message);

            // Create assistant message placeholder
            const assistantBubble = addMessage(messagesContainer, 'assistant', '', true);

            let sources = [];
            let fullResponse = '';

            // Stream the response
            for await (const event of API.chatStream(message, sessionId, topK, model)) {
                switch (event.type) {
                    case 'metadata':
                        sessionId = event.session_id;
                        sessionIdDisplay.textContent = `Session: ${sessionId.substring(0, 8)}...`;
                        break;

                    case 'sources':
                        sources = event.documents || [];
                        // Show sources above the message
                        if (sources.length > 0) {
                            showSources(assistantBubble, sources);
                        }
                        break;

                    case 'chunk':
                        fullResponse += event.content || '';
                        updateMessageContent(assistantBubble, fullResponse, true);
                        break;

                    case 'done':
                        updateMessageContent(assistantBubble, fullResponse, false);
                        // Store message in local history
                        messages.push({ role: 'user', content: message });
                        messages.push({ role: 'assistant', content: fullResponse, sources });
                        break;

                    case 'error':
                        throw new Error(event.error);
                }
            }
        } catch (error) {
            showError(errorDiv, error.message);
        } finally {
            setLoading(sendBtn, false);
            isStreaming = false;
        }
    });

    // Initialize with empty state
    showEmptyState(messagesContainer);
}

/**
 * Load available chat models into select
 */
async function loadChatModels(selectElement) {
    try {
        const { models } = await API.getChatModels();
        selectElement.innerHTML = models
            .map(model => `<option value="${model}"${model === 'gemini-2.5-flash' ? ' selected' : ''}>${model}</option>`)
            .join('');
    } catch (error) {
        console.error('Failed to load chat models:', error);
    }
}

/**
 * Create a new chat session
 */
function createNewSession(messagesContainer, sessionIdDisplay) {
    sessionId = null;
    messages = [];
    sessionIdDisplay.textContent = 'Session: new';
    messagesContainer.innerHTML = '';
    showEmptyState(messagesContainer);
}

/**
 * Show empty state message
 */
function showEmptyState(container) {
    container.innerHTML = `
        <div class="empty-state">
            <i class="bi bi-chat-square-text"></i>
            <p>Ask a question about your uploaded documents</p>
        </div>
    `;
}

/**
 * Add a message to the chat
 * @returns {HTMLElement} The message bubble element
 */
function addMessage(container, role, content, streaming = false) {
    // Remove empty state if present
    const emptyState = container.querySelector('.empty-state');
    if (emptyState) {
        emptyState.remove();
    }

    const messageDiv = document.createElement('div');
    messageDiv.className = `chat-message ${role}`;

    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble';

    if (content) {
        bubble.innerHTML = escapeHtml(content);
    }

    if (streaming) {
        const cursor = document.createElement('span');
        cursor.className = 'streaming-cursor';
        bubble.appendChild(cursor);
    }

    messageDiv.appendChild(bubble);
    container.appendChild(messageDiv);

    // Scroll to bottom
    container.scrollTop = container.scrollHeight;

    return bubble;
}

/**
 * Update message content during streaming
 */
function updateMessageContent(bubble, content, streaming) {
    // Remove cursor if present
    const cursor = bubble.querySelector('.streaming-cursor');

    bubble.innerHTML = escapeHtml(content);

    if (streaming) {
        const newCursor = document.createElement('span');
        newCursor.className = 'streaming-cursor';
        bubble.appendChild(newCursor);
    }

    // Scroll to bottom
    const container = document.getElementById('chatMessages');
    container.scrollTop = container.scrollHeight;
}

/**
 * Show sources accordion above the message
 */
function showSources(bubble, sources) {
    const messageDiv = bubble.parentElement;
    const accordionId = `sources-${Date.now()}`;

    const sourcesDiv = document.createElement('div');
    sourcesDiv.className = 'chat-sources';
    sourcesDiv.innerHTML = `
        <div class="accordion" id="${accordionId}">
            <div class="accordion-item">
                <h2 class="accordion-header">
                    <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#${accordionId}-body">
                        <i class="bi bi-file-earmark-text me-2"></i>Sources (${sources.length})
                    </button>
                </h2>
                <div id="${accordionId}-body" class="accordion-collapse collapse" data-bs-parent="#${accordionId}">
                    <div class="accordion-body">
                        ${sources.map(source => {
                            const pages = source.pages && source.pages.length > 0
                                ? ` (pages ${source.pages.join(', ')})`
                                : '';
                            const score = source.score
                                ? ` - ${(source.score * 100).toFixed(0)}%`
                                : '';
                            return `<div class="source-item"><strong>${escapeHtml(source.filename)}</strong>${pages}${score}</div>`;
                        }).join('')}
                    </div>
                </div>
            </div>
        </div>
    `;

    // Insert sources before the bubble
    messageDiv.insertBefore(sourcesDiv, bubble);
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Show an element
 */
function showElement(element) {
    element.classList.remove('d-none');
}

/**
 * Hide an element
 */
function hideElement(element) {
    element.classList.add('d-none');
}

/**
 * Show error message
 */
function showError(element, message) {
    element.textContent = message;
    showElement(element);
}

/**
 * Set loading state on button
 */
function setLoading(button, loading) {
    if (loading) {
        button.classList.add('loading');
        button.disabled = true;
    } else {
        button.classList.remove('loading');
        button.disabled = false;
    }
}

export default { initChat };
