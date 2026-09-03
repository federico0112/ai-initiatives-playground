/**
 * Search tab component
 */

import { API } from '../api.js';

let embedderSelect = null;
let fileSelect = null;
let refreshBtn = null;

/**
 * Initialize search tab
 */
export function initSearch() {
    const form = document.getElementById('searchForm');
    const queryInput = document.getElementById('searchQuery');
    const limitSlider = document.getElementById('searchLimit');
    const limitValue = document.getElementById('searchLimitValue');
    embedderSelect = document.getElementById('searchEmbedder');
    fileSelect = document.getElementById('searchFiles');
    refreshBtn = document.getElementById('refreshSearchFilesBtn');
    const submitBtn = document.getElementById('searchBtn');
    const resultDiv = document.getElementById('searchResult');
    const resultJson = document.getElementById('searchResultJson');
    const errorDiv = document.getElementById('searchError');

    // Load available models first, then documents
    loadModels(embedderSelect).then(() => {
        loadDocumentsForModel();
    });

    // Reload documents when embedder changes
    embedderSelect.addEventListener('change', () => {
        loadDocumentsForModel();
    });

    // Refresh button handler
    refreshBtn.addEventListener('click', () => {
        loadDocumentsForModel();
    });

    // Update limit display when slider changes
    limitSlider.addEventListener('input', () => {
        limitValue.textContent = limitSlider.value;
    });

    // Reload when tab is shown
    const searchTab = document.getElementById('search-tab');
    searchTab.addEventListener('shown.bs.tab', () => {
        loadDocumentsForModel();
    });

    // Handle form submission
    form.addEventListener('submit', async (e) => {
        e.preventDefault();

        const query = queryInput.value.trim();
        if (!query) {
            showError(errorDiv, 'Please enter a search query');
            return;
        }

        // Clear previous results
        hideElement(resultDiv);
        hideElement(errorDiv);

        // Show loading state
        setLoading(submitBtn, true);

        try {
            const limit = parseInt(limitSlider.value, 10);
            const model = embedderSelect.value;

            // Get selected filenames (empty array means search all)
            const selectedFiles = Array.from(fileSelect.selectedOptions).map(opt => opt.value);
            const filenames = selectedFiles.length > 0 ? selectedFiles : null;

            // Use selected model for both embedding query and filtering results
            const result = await API.search(query, limit, model, filenames, model);

            // Show result
            resultJson.textContent = JSON.stringify(result, null, 2);
            showElement(resultDiv);
        } catch (error) {
            showError(errorDiv, error.message);
        } finally {
            setLoading(submitBtn, false);
        }
    });
}

/**
 * Load available embedding models into select
 */
async function loadModels(selectElement) {
    try {
        const { models } = await API.getModels();
        if (models && models.length > 0) {
            selectElement.innerHTML = models
                .map(model => `<option value="${model}">${model}</option>`)
                .join('');
        }
        return true;
    } catch (error) {
        console.error('Failed to load models:', error);
        return false;
    }
}

/**
 * Load documents filtered by the selected embedding model
 */
async function loadDocumentsForModel() {
    const model = embedderSelect?.value;
    if (!model) {
        fileSelect.innerHTML = '<option value="" disabled>Select an embedder first</option>';
        return;
    }
    setLoading(refreshBtn, true);

    try {
        const { documents } = await API.getDocuments(model);
        if (documents.length === 0) {
            fileSelect.innerHTML = `<option value="" disabled>No files embedded with ${model}</option>`;
            return;
        }
        fileSelect.innerHTML = documents
            .map(doc => `<option value="${doc.filename}">${doc.filename} (${doc.chunk_count} chunks)</option>`)
            .join('');
    } catch (error) {
        console.error('Failed to load documents:', error);
        fileSelect.innerHTML = '<option value="" disabled>Failed to load files</option>';
    } finally {
        setLoading(refreshBtn, false);
    }
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

export default { initSearch };
