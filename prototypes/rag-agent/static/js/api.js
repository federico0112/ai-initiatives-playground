/**
 * REST API client for Document Embedder
 */

const API_BASE = '/api/v1';

/**
 * Fetch wrapper with error handling
 */
async function fetchJSON(url, options = {}) {
    const response = await fetch(url, options);

    if (!response.ok) {
        const error = await response.json().catch(() => ({
            error: `HTTP ${response.status}: ${response.statusText}`
        }));
        throw new Error(error.error || error.message || 'Unknown error');
    }

    return response.json();
}

/**
 * API client object
 */
export const API = {
    /**
     * Get available embedding models
     */
    async getModels() {
        return fetchJSON(`${API_BASE}/models`);
    },

    /**
     * Get available storage backends
     */
    async getStorages() {
        return fetchJSON(`${API_BASE}/storages`);
    },

    /**
     * Get available chat models
     */
    async getChatModels() {
        return fetchJSON(`${API_BASE}/chat-models`);
    },

    /**
     * Get version info
     */
    async getVersion() {
        return fetchJSON(`${API_BASE}/version`);
    },

    /**
     * Upload a file for embedding
     * @param {File} file - The file to upload
     * @param {string} model - The embedding model to use
     * @returns {Promise<{document_id: string, chunks_stored: number}>}
     */
    async upload(file, model = 'gemini') {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('model', model);

        return fetchJSON(`${API_BASE}/upload`, {
            method: 'POST',
            body: formData,
        });
    },

    /**
     * Get a signed URL for direct upload to GCS
     * @param {string} filename - The file name
     * @param {string} contentType - The file MIME type
     * @returns {Promise<{signed_url: string, gcs_path: string, expires_in_minutes: number}>}
     */
    async getSignedUploadUrl(filename, contentType = 'application/octet-stream') {
        return fetchJSON(`${API_BASE}/upload/signed-url`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename, content_type: contentType }),
        });
    },

    /**
     * Upload file directly to GCS using signed URL
     * @param {string} signedUrl - The signed URL from getSignedUploadUrl
     * @param {File} file - The file to upload
     * @param {function} onProgress - Optional progress callback (0-100)
     */
    async uploadToGCS(signedUrl, file, onProgress = null) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();

            if (onProgress) {
                xhr.upload.addEventListener('progress', (e) => {
                    if (e.lengthComputable) {
                        onProgress(Math.round((e.loaded / e.total) * 100));
                    }
                });
            }

            xhr.addEventListener('load', () => {
                if (xhr.status >= 200 && xhr.status < 300) {
                    resolve();
                } else {
                    reject(new Error(`Upload failed: ${xhr.status}`));
                }
            });

            xhr.addEventListener('error', () => reject(new Error('Upload failed')));
            xhr.open('PUT', signedUrl);
            xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
            xhr.send(file);
        });
    },

    /**
     * Trigger embedding of a file already uploaded to GCS
     * @param {string} gcsPath - The GCS path from getSignedUploadUrl
     * @param {string} model - The embedding model to use
     * @returns {Promise<{document_id: string, chunks_stored: number}>}
     */
    async embedFromGCS(gcsPath, model = 'gemini') {
        return fetchJSON(`${API_BASE}/embed-from-gcs`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ gcs_path: gcsPath, model, cleanup: true }),
        });
    },

    /**
     * Search for similar documents
     * @param {string} query - The search query
     * @param {number} limit - Maximum results to return
     * @param {string} model - The embedding model to use
     * @returns {Promise<{query: string, results: Array}>}
     */
    async search(query, limit = 5, model = 'gemini') {
        return fetchJSON(`${API_BASE}/search`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query, limit, model }),
        });
    },

    /**
     * Stream chat response using NDJSON
     * @param {string} message - The user's message
     * @param {string|null} sessionId - Optional session ID
     * @param {number} topK - Number of documents to retrieve
     * @param {string} model - The chat model to use
     * @yields {Object} NDJSON events: metadata, sources, chunk, done, error
     */
    async *chatStream(message, sessionId = null, topK = 5, model = 'gemini-2.5-flash') {
        const response = await fetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message,
                session_id: sessionId,
                top_k: topK,
                model,
            }),
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({
                error: `HTTP ${response.status}: ${response.statusText}`
            }));
            throw new Error(error.error || error.message || 'Unknown error');
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        try {
            while (true) {
                const { done, value } = await reader.read();

                if (done) {
                    // Process any remaining buffer content
                    if (buffer.trim()) {
                        try {
                            yield JSON.parse(buffer.trim());
                        } catch (e) {
                            console.warn('Failed to parse final buffer:', buffer);
                        }
                    }
                    break;
                }

                buffer += decoder.decode(value, { stream: true });

                // Process complete lines
                const lines = buffer.split('\n');
                buffer = lines.pop() || ''; // Keep incomplete line in buffer

                for (const line of lines) {
                    if (line.trim()) {
                        try {
                            yield JSON.parse(line);
                        } catch (e) {
                            console.warn('Failed to parse NDJSON line:', line);
                        }
                    }
                }
            }
        } finally {
            reader.releaseLock();
        }
    },
};

export default API;
