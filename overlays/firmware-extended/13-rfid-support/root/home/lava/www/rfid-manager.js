// RFID Tag Manager JavaScript

// API base URL (relative to current origin)
const API_BASE = '/server/rfid';

// Material default temperatures (for reference)
const MATERIAL_DEFAULTS = {
    'PLA': { min_temp: 190, max_temp: 220, bed_temp: 60, density: 1.24 },
    'PETG': { min_temp: 220, max_temp: 250, bed_temp: 80, density: 1.27 },
    'ABS': { min_temp: 230, max_temp: 260, bed_temp: 100, density: 1.04 },
    'TPU': { min_temp: 210, max_temp: 230, bed_temp: 50, density: 1.21 },
    'PVA': { min_temp: 190, max_temp: 210, bed_temp: 60, density: 1.19 },
    'NYLON': { min_temp: 240, max_temp: 270, bed_temp: 80, density: 1.14 },
    'ASA': { min_temp: 240, max_temp: 260, bed_temp: 100, density: 1.07 },
    'PC': { min_temp: 260, max_temp: 290, bed_temp: 110, density: 1.20 }
};

// State
let channelsData = [];

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    initializeEventListeners();
    refreshAllChannels();
});

// Event Listeners
function initializeEventListeners() {
    document.getElementById('refresh-all').addEventListener('click', refreshAllChannels);
    document.getElementById('write-form').addEventListener('submit', handleWriteTag);
    document.getElementById('erase-form').addEventListener('submit', handleEraseTag);

    // Pre-populate form and update button states when write channel changes
    const writeChannelSelect = document.querySelector('#write-form select[name="channel"]');
    writeChannelSelect.addEventListener('change', (e) => {
        const channel = parseInt(e.target.value);
        populateWriteFormFromChannel(channel);
        updateButtonStates();
    });

    // Update button states when erase channel changes
    const eraseChannelSelect = document.querySelector('#erase-form select[name="channel"]');
    eraseChannelSelect.addEventListener('change', updateButtonStates);

    // Sync color picker and hex input
    const colorPicker = document.getElementById('color-picker');
    const colorHex = document.getElementById('color-hex');

    colorPicker.addEventListener('input', (e) => {
        colorHex.value = e.target.value.toUpperCase();
    });

    colorHex.addEventListener('input', (e) => {
        const value = e.target.value;
        // Validate hex format
        if (/^#[0-9A-Fa-f]{6}$/.test(value)) {
            colorPicker.value = value;
        }
    });

    // Auto-fill defaults when material type changes
    const typeSelect = document.querySelector('#write-form select[name="type"]');
    typeSelect.addEventListener('change', (e) => {
        const material = e.target.value;
        const defaults = MATERIAL_DEFAULTS[material];
        if (defaults) {
            const form = document.getElementById('write-form');
            form.querySelector('input[name="density"]').placeholder = `Auto (${defaults.density})`;
            form.querySelector('input[name="min_temp"]').placeholder = `Auto (${defaults.min_temp})`;
            form.querySelector('input[name="max_temp"]').placeholder = `Auto (${defaults.max_temp})`;
            form.querySelector('input[name="bed_temp"]').placeholder = `Auto (${defaults.bed_temp})`;
        }
    });
}

// API Calls
async function apiCall(endpoint, method = 'GET', data = null) {
    const options = {
        method,
        headers: {
            'Content-Type': 'application/json'
        }
    };

    if (data && method !== 'GET') {
        options.body = JSON.stringify(data);
    }

    try {
        const response = await fetch(`${API_BASE}${endpoint}`, options);
        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || `HTTP ${response.status}`);
        }

        return result;
    } catch (error) {
        console.error('API call failed:', error);
        throw error;
    }
}

// Channel Management
async function refreshAllChannels() {
    try {
        showStatus('Refreshing channels...', 'info');
        const response = await apiCall('/tags');
        // Moonraker wraps response in {result: {...}}
        channelsData = response.result?.channels || response.channels;
        renderChannels();
        showStatus('Channels refreshed successfully', 'success');
    } catch (error) {
        showStatus(`Failed to refresh channels: ${error.message}`, 'error');
    }
}

function renderChannels() {
    const grid = document.getElementById('channels-grid');
    grid.innerHTML = '';

    channelsData.forEach(channel => {
        const card = createChannelCard(channel);
        grid.appendChild(card);
    });

    // Auto-populate write form for currently selected channel
    const writeChannelSelect = document.querySelector('#write-form select[name="channel"]');
    if (writeChannelSelect) {
        const selectedChannel = parseInt(writeChannelSelect.value);
        populateWriteFormFromChannel(selectedChannel);
    }

    // Update button states based on current channel data
    updateButtonStates();
}

function populateWriteFormFromChannel(channel) {
    const channelData = channelsData.find(c => c.channel === channel);
    if (!channelData) return;

    const form = document.getElementById('write-form');
    const filament = channelData.filament || {};

    // Helper to check if a value is valid (not empty, not "NONE", not 0)
    const hasValue = (val) => {
        if (!val) return false;
        if (typeof val === 'string' && (val.trim() === '' || val.toUpperCase() === 'NONE')) return false;
        if (typeof val === 'number' && val === 0) return false;
        return true;
    };

    // Populate form with available data, use defaults for missing fields
    // Material type: use tag data if available, otherwise default to PLA
    const materialType = hasValue(filament.type) ? filament.type : 'PLA';
    form.querySelector('select[name="type"]').value = materialType;
    form.querySelector('select[name="type"]').dispatchEvent(new Event('change'));

    // Brand: use tag data if available, otherwise default to 'Generic'
    form.querySelector('input[name="brand"]').value = hasValue(filament.brand) ? filament.brand : 'Generic';

    // Subtype: use tag data if available, otherwise clear
    form.querySelector('input[name="subtype"]').value = hasValue(filament.subtype) ? filament.subtype : '';

    // Color: use tag data if available, otherwise default to white
    if (hasValue(filament.color_hex)) {
        const hexColor = `#${filament.color_hex}`;
        form.querySelector('input[name="color_hex"]').value = hexColor;
        document.getElementById('color-picker').value = hexColor;
    } else {
        form.querySelector('input[name="color_hex"]').value = '#FFFFFF';
        document.getElementById('color-picker').value = '#FFFFFF';
    }

    // Diameter: use tag data if available, otherwise default to 1.75mm
    form.querySelector('input[name="diameter"]').value = hasValue(filament.diameter) ? filament.diameter : '1.75';

    // Density: use tag data if available, otherwise clear (auto-fill from material)
    form.querySelector('input[name="density"]').value = hasValue(filament.density) ? filament.density : '';

    // Temperatures: use tag data if available, otherwise clear (auto-fill from material)
    form.querySelector('input[name="min_temp"]').value = hasValue(filament.min_temp) ? filament.min_temp : '';
    form.querySelector('input[name="max_temp"]').value = hasValue(filament.max_temp) ? filament.max_temp : '';
    form.querySelector('input[name="bed_temp"]').value = hasValue(filament.bed_temp) ? filament.bed_temp : '';
}

function createChannelCard(channel) {
    const card = document.createElement('div');
    card.className = 'channel-card';

    const tagPresent = channel.tag_present;
    const tagEmpty = channel.tag_empty;

    let statusClass, statusText;
    if (tagEmpty) {
        statusClass = 'empty';
        statusText = 'Empty Tag';
    } else if (tagPresent) {
        statusClass = 'present';
        statusText = 'Tag Present';
    } else {
        statusClass = 'absent';
        statusText = 'No Tag';
    }

    let content = `
        <div class="channel-header">
            <div class="channel-title">Channel ${channel.channel}</div>
            <div class="tag-status ${statusClass}">${statusText}</div>
        </div>
    `;

    if (tagPresent) {
        const filament = channel.filament || {};

        content += '<div class="tag-info">';

        // Tag type and UID
        if (channel.tag_type) {
            content += `
                <div class="info-row">
                    <span class="info-label">Tag Type</span>
                    <span class="info-value">${channel.tag_type}</span>
                </div>
            `;
        }

        if (channel.uid) {
            content += `
                <div class="info-row">
                    <span class="info-label">UID</span>
                    <span class="info-value">${channel.uid}</span>
                </div>
            `;
        }

        // Show message for empty tags
        if (tagEmpty) {
            content += `
                <div class="info-row">
                    <span class="info-label" style="grid-column: 1 / -1;">Tag detected but not programmed. Use the Tag Operations block to add filament information.</span>
                </div>
            `;
        }

        // Filament info (only if tag is programmed)
        if (!tagEmpty && (filament.brand || filament.type)) {
            // Build filament description: "Brand Type (Subtype)" or "Brand Type" if no subtype
            let filamentDesc = `${filament.brand || 'Unknown'} ${filament.type || ''}`;
            if (filament.subtype && filament.subtype !== 'Basic' && filament.subtype !== 'Reserved') {
                filamentDesc += ` (${filament.subtype})`;
            }
            content += `
                <div class="info-row">
                    <span class="info-label">Filament</span>
                    <span class="info-value">${filamentDesc}</span>
                </div>
            `;
        }

        if (!tagEmpty && filament.color_hex) {
            content += `
                <div class="info-row">
                    <span class="info-label">Color</span>
                    <span class="info-value">
                        #${filament.color_hex}
                        <span class="color-swatch" style="background-color: #${filament.color_hex};"></span>
                    </span>
                </div>
            `;
        }

        if (!tagEmpty && filament.diameter) {
            content += `
                <div class="info-row">
                    <span class="info-label">Diameter</span>
                    <span class="info-value">${filament.diameter} mm</span>
                </div>
            `;
        }

        if (!tagEmpty && filament.density) {
            content += `
                <div class="info-row">
                    <span class="info-label">Density</span>
                    <span class="info-value">${filament.density} g/cm³</span>
                </div>
            `;
        }

        // Temperature info
        if (!tagEmpty && filament.min_temp && filament.max_temp) {
            content += `
                <div class="info-row">
                    <span class="info-label">Extruder Temp</span>
                    <span class="info-value">${filament.min_temp}°C - ${filament.max_temp}°C</span>
                </div>
            `;
        }

        if (!tagEmpty && filament.bed_temp) {
            content += `
                <div class="info-row">
                    <span class="info-label">Bed Temp</span>
                    <span class="info-value">${filament.bed_temp}°C</span>
                </div>
            `;
        }

        content += '</div>';
    } else {
        content += '<div class="no-tag">No RFID tag detected on this channel</div>';
    }

    card.innerHTML = content;
    return card;
}

// Form Handlers
async function handleWriteTag(e) {
    e.preventDefault();
    const form = e.target;
    const formData = new FormData(form);

    const data = {
        channel: parseInt(formData.get('channel')),
        type: formData.get('type'),
        brand: formData.get('brand'),
        color_hex: formData.get('color_hex').substring(1), // Remove # prefix
        diameter: parseFloat(formData.get('diameter'))
    };

    // Optional fields
    if (formData.get('subtype')) {
        data.subtype = formData.get('subtype');
    }
    if (formData.get('density')) {
        data.density = parseFloat(formData.get('density'));
    }
    if (formData.get('min_temp')) {
        data.min_temp = parseInt(formData.get('min_temp'));
    }
    if (formData.get('max_temp')) {
        data.max_temp = parseInt(formData.get('max_temp'));
    }
    if (formData.get('bed_temp')) {
        data.bed_temp = parseInt(formData.get('bed_temp'));
    }

    try {
        showStatus('Writing tag...', 'info');
        const response = await apiCall('/write_openspool', 'POST', data);
        // Moonraker wraps response in {result: {...}}
        const result = response.result || response;

        if (result.success) {
            const verifyMsg = result.verified ? ' and verified' : ' (verification pending)';
            showStatus(`Tag written${verifyMsg} successfully on channel ${data.channel}`, 'success');
            form.reset();
            setTimeout(refreshAllChannels, 1000);
        } else {
            showStatus(`Write failed: ${result.error || 'Unknown error'}`, 'error');
        }
    } catch (error) {
        showStatus(`Write failed: ${error.message}`, 'error');
    }
}

async function handleEraseTag(e) {
    e.preventDefault();
    const form = e.target;
    const formData = new FormData(form);

    if (!formData.get('confirm')) {
        showStatus('Please confirm tag erasure', 'error');
        return;
    }

    const data = {
        channel: parseInt(formData.get('channel')),
        confirm: true
    };

    try {
        showStatus('Erasing tag...', 'info');
        const response = await apiCall('/erase', 'POST', data);
        // Moonraker wraps response in {result: {...}}
        const result = response.result || response;

        if (result.success) {
            const verifyMsg = result.verified ? ' and verified' : '';
            showStatus(`Tag erased${verifyMsg} on channel ${data.channel}`, 'success');
            form.reset();
            setTimeout(refreshAllChannels, 1000);
        } else {
            showStatus(`Erase failed: ${result.error || 'Unknown error'}`, 'error');
        }
    } catch (error) {
        showStatus(`Erase failed: ${error.message}`, 'error');
    }
}

// Button State Management
function updateButtonStates() {
    // Write form
    const writeChannel = parseInt(document.querySelector('#write-form select[name="channel"]').value);
    const writeBtn = document.querySelector('#write-form button[type="submit"]');
    const writeChannelData = channelsData.find(c => c.channel === writeChannel);
    const writeIsM1 = writeChannelData?.tag_type === 'M1';

    writeBtn.disabled = writeIsM1;

    // Show/hide M1 warning for write form
    let writeWarning = document.getElementById('write-m1-warning');
    if (!writeWarning) {
        writeWarning = document.createElement('div');
        writeWarning.id = 'write-m1-warning';
        writeWarning.className = 'm1-warning';
        writeWarning.textContent = 'M1 (Snapmaker) tags are read-only. Use an NTAG tag instead.';
        writeBtn.parentNode.appendChild(writeWarning);
    }
    writeWarning.style.display = writeIsM1 ? 'block' : 'none';

    // Erase form
    const eraseChannel = parseInt(document.querySelector('#erase-form select[name="channel"]').value);
    const eraseBtn = document.querySelector('#erase-form button[type="submit"]');
    const eraseChannelData = channelsData.find(c => c.channel === eraseChannel);
    const eraseIsM1 = eraseChannelData?.tag_type === 'M1';

    eraseBtn.disabled = eraseIsM1;

    // Show/hide M1 warning for erase form
    let eraseWarning = document.getElementById('erase-m1-warning');
    if (!eraseWarning) {
        eraseWarning = document.createElement('div');
        eraseWarning.id = 'erase-m1-warning';
        eraseWarning.className = 'm1-warning';
        eraseWarning.textContent = 'M1 (Snapmaker) tags are read-only and cannot be erased.';
        eraseBtn.parentNode.appendChild(eraseWarning);
    }
    eraseWarning.style.display = eraseIsM1 ? 'block' : 'none';
}

// Status Messages
function showStatus(message, type = 'info') {
    const statusEl = document.getElementById('status-message');
    statusEl.textContent = message;
    statusEl.className = `status-message ${type} show`;

    setTimeout(() => {
        statusEl.classList.remove('show');
    }, 4000);
}
