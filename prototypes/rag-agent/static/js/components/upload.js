/**
 * Upload tab component
 */

import { API } from '../api.js';

/**
 * Initialize upload tab
 */
export function initUpload() {
    const form = document.getElementById('uploadForm');
    const fileInput = document.getElementById('uploadFile');
    const embedderSelect = document.getElementById('uploadEmbedder');
    const submitBtn = document.getElementById('uploadBtn');
    const resultDiv = document.getElementById('uploadResult');
    const resultJson = document.getElementById('uploadResultJson');
    const errorDiv = document.getElementById('uploadError');

    // Load available models
    loadModels(embedderSelect);

    // Handle form submission
    form.addEventListener('submit', async (e) => {
        e.preventDefault();

        const file = fileInput.files[0];
        if (!file) {
            showError(errorDiv, 'Please select a file to upload');
            return;
        }

        // Clear previous results
        hideElement(resultDiv);
        hideElement(errorDiv);

        // Show loading state
        setLoading(submitBtn, true);

        try {
            const model = embedderSelect.value;
            const result = await API.upload(file, model);

            // Show result
            resultJson.textContent = JSON.stringify(result, null, 2);
            showElement(resultDiv);

            // Reset form
            form.reset();
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

export default { initUpload };
