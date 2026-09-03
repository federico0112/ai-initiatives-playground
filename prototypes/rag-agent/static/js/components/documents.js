/**
 * Documents tab component
 */

import { API } from '../api.js';

let documentsList = null;
let errorDiv = null;
let refreshBtn = null;
let modelFilter = null;

/**
 * Initialize documents tab
 */
export function initDocuments() {
    documentsList = document.getElementById('documentsList');
    errorDiv = document.getElementById('documentsError');
    refreshBtn = document.getElementById('refreshDocsBtn');
    modelFilter = document.getElementById('docsModelFilter');

    // Load model filter options
    loadModelFilterOptions();

    // Load documents on init
    loadDocuments();

    // Refresh button handler
    refreshBtn.addEventListener('click', () => {
        loadDocuments();
    });

    // Model filter change handler
    modelFilter.addEventListener('change', () => {
        loadDocuments();
    });

    // Reload when tab is shown
    const documentsTab = document.getElementById('documents-tab');
    documentsTab.addEventListener('shown.bs.tab', () => {
        loadModelFilterOptions();
        loadDocuments();
    });
}

/**
 * Load model filter dropdown options
 */
async function loadModelFilterOptions() {
    try {
        const { models } = await API.getDocumentModels();
        const currentValue = modelFilter.value;

        // Keep "All Models" option and add actual models
        modelFilter.innerHTML = '<option value="">All Models</option>' +
            models.map(model => `<option value="${model}">${model}</option>`).join('');

        // Restore previous selection if still valid
        if (currentValue && models.includes(currentValue)) {
            modelFilter.value = currentValue;
        }
    } catch (error) {
        console.error('Failed to load model filter options:', error);
    }
}

/**
 * Load and render documents list
 */
async function loadDocuments() {
    hideElement(errorDiv);
    setLoading(refreshBtn, true);

    try {
        const selectedModel = modelFilter.value || null;
        const { documents } = await API.getDocuments(selectedModel);
        renderDocumentList(documents);
    } catch (error) {
        showError(errorDiv, error.message);
    } finally {
        setLoading(refreshBtn, false);
    }
}

/**
 * Render the document list
 */
function renderDocumentList(documents) {
    if (documents.length === 0) {
        const filterActive = modelFilter.value;
        documentsList.innerHTML = `
            <div class="empty-state">
                <i class="bi bi-inbox"></i>
                <p>${filterActive ? 'No documents found for this model' : 'No documents uploaded yet'}</p>
                <p class="small">${filterActive ? 'Try selecting a different model or "All Models"' : 'Upload documents in the "Upload & Vectorize" tab'}</p>
            </div>
        `;
        return;
    }

    const rows = documents.map(doc => {
        const createdAt = doc.created_at
            ? new Date(doc.created_at).toLocaleString()
            : 'Unknown';
        const model = doc.model || 'unknown';

        return `
            <tr data-document-id="${doc.document_id}">
                <td>
                    <i class="bi bi-file-earmark-text me-2"></i>
                    <span class="doc-filename">${escapeHtml(doc.filename)}</span>
                </td>
                <td><span class="badge bg-secondary">${escapeHtml(model)}</span></td>
                <td class="text-muted">${doc.chunk_count} chunks</td>
                <td class="text-muted">${createdAt}</td>
                <td class="text-end">
                    <button class="btn btn-outline-danger btn-sm delete-doc-btn"
                            data-document-id="${doc.document_id}"
                            data-filename="${escapeHtml(doc.filename)}">
                        <i class="bi bi-trash"></i>
                    </button>
                </td>
            </tr>
        `;
    }).join('');

    documentsList.innerHTML = `
        <div class="table-responsive">
            <table class="table table-hover documents-table">
                <thead>
                    <tr>
                        <th>Filename</th>
                        <th>Model</th>
                        <th>Chunks</th>
                        <th>Uploaded</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>
                    ${rows}
                </tbody>
            </table>
        </div>
    `;

    // Attach delete handlers
    documentsList.querySelectorAll('.delete-doc-btn').forEach(btn => {
        btn.addEventListener('click', handleDelete);
    });
}

/**
 * Handle delete button click
 */
async function handleDelete(event) {
    const btn = event.currentTarget;
    const documentId = btn.dataset.documentId;
    const filename = btn.dataset.filename;

    const confirmed = confirm(`Delete "${filename}"?\n\nThis will remove all chunks from the vector database.`);
    if (!confirmed) return;

    hideElement(errorDiv);
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';

    try {
        await API.deleteDocument(documentId);
        // Reload the list
        await loadDocuments();
    } catch (error) {
        showError(errorDiv, `Failed to delete: ${error.message}`);
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-trash"></i>';
    }
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

export default { initDocuments };
