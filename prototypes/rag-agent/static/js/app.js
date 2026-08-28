/**
 * Main application entry point
 */

import { API } from './api.js?v=2';
import { initUpload } from './components/upload.js?v=2';
import { initSearch } from './components/search.js';
import { initChat } from './components/chat.js';

/**
 * Initialize the application
 */
async function init() {
    console.log('Initializing Document Embedder UI...');

    // Load version info
    try {
        const versionInfo = await API.getVersion();
        const versionDisplay = document.getElementById('versionInfo');
        if (versionDisplay) {
            const sha = versionInfo.git_sha !== 'development'
                ? ` (${versionInfo.git_sha.substring(0, 7)})`
                : '';
            versionDisplay.textContent = `v${versionInfo.version}${sha}`;
        }
    } catch (error) {
        console.warn('Failed to load version info:', error);
    }

    // Load storage options for upload and search tabs
    try {
        const { storages } = await API.getStorages();
        const storageSelects = [
            document.getElementById('uploadStorage'),
            document.getElementById('searchStorage'),
        ];

        for (const select of storageSelects) {
            if (select && storages.length > 0) {
                select.innerHTML = storages
                    .map(storage => `<option value="${storage}">${storage}</option>`)
                    .join('');
            }
        }
    } catch (error) {
        console.warn('Failed to load storage options:', error);
    }

    // Initialize tab components
    initUpload();
    initSearch();
    initChat();

    console.log('Document Embedder UI initialized');
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
