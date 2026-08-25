/**
 * Search tab component
 */

import { API } from '../api.js';

/**
 * Initialize search tab
 */
export function initSearch() {
    const form = document.getElementById('searchForm');
    const queryInput = document.getElementById('searchQuery');
    const limitSlider = document.getElementById('searchLimit');
    const limitValue = document.getElementById('searchLimitValue');
    const embedderSelect = document.getElementById('searchEmbedder');
    const submitBtn = document.getElementById('searchBtn');
    const resultDiv = document.getElementById('searchResult');
    const resultJson = document.getElementById('searchResultJson');
    const errorDiv = document.getElementById('searchError');

    // Load available models
    loadModels(embedderSelect);

    // Update limit display when slider changes
    limitSlider.addEventListener('input', () => {
        limitValue.textContent = limitSlider.value;
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
            const result = await API.search(query, limit, model);

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
        selectElement.innerHTML = models
            .map(model => `<option value="${model}">${model}</option>`)
            .join('');
    } catch (error) {
        console.error('Failed to load models:', error);
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
