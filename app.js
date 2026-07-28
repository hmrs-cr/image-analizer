document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const serverStatus = document.getElementById('server-status');
    const statusText = document.getElementById('status-text');
    const uptimeValue = document.getElementById('uptime-value');

    const cameraSummaryAnalyzed = document.getElementById('camera-summary-analyzed');
    const cameraSummaryMatches = document.getElementById('camera-summary-matches');
    const cameraSummaryAlerts = document.getElementById('camera-summary-alerts');
    const globalNotificationsBtn = document.getElementById('global-notifications-btn');

    const snoozeStatusText = document.getElementById('snooze-status-text');
    const snoozeStatusBar = document.getElementById('snooze-status');
    const snoozeCameraSelect = document.getElementById('snooze-camera-select');
    const camerasStatsBody = document.getElementById('cameras-stats-body');
    const cameraSearchInput = document.getElementById('camera-search-input');
    const btnToggleAllGroups = document.getElementById('btn-toggle-all-groups');
    const imageHistoryList = document.getElementById('image-history-list');
    const imageHistoryEmpty = document.getElementById('image-history-empty');
    const historyFilterLabel = document.getElementById('history-filter-label');
    const btnClearHistoryFilter = document.getElementById('btn-clear-history-filter');
    if (btnClearHistoryFilter) {
        btnClearHistoryFilter.style.display = 'none';
    }

    const noDetectionPlaceholder = document.getElementById('no-detection-placeholder');
    const detectionContent = document.getElementById('detection-content');
    const lastDetectionImage = document.getElementById('last-detection-image');
    const lastDetectionTime = document.getElementById('last-detection-time');
    const lastDetectionLocation = document.getElementById('last-detection-location');
    const lastDetectionObjects = document.getElementById('last-detection-objects');
    const lastDetectionFaces = document.getElementById('last-detection-faces');
    const lastDetectionDesc = document.getElementById('last-detection-desc');
    const detectionTitle = document.getElementById('detection-title');

    const appHeader = document.getElementById('app-header');
    const statsTitle = document.getElementById('stats-title');
    const btnOpenSettings = document.getElementById('btn-open-settings');
    const btnCloseSettings = document.getElementById('close-settings-dialog');
    const btnUnpinHistory = document.getElementById('btn-unpin-history');
    const btnToggleNoDetections = document.getElementById('btn-toggle-no-detections');
    const settingsDialog = document.getElementById('settings-dialog');
    const settingsView = document.getElementById('settings-view');

    const params = new URLSearchParams(window.location.search);
    const hideHeader = params.get('hideHeader') === '1';
    const hideStatsTitle = params.get('hideStatsTitle') === '1';

    if (appHeader && hideHeader) {
        appHeader.style.display = 'none';
    }

    if (statsTitle && hideStatsTitle) {
        statsTitle.style.display = 'none';
    }

    let isFetchingStatus = false;
    let knownCameras = new Set();
    let currentStatusData = null;
    let currentCameraFilter = null;
    let historyData = [];
    let lastHistoryRequestId = 0;
    let pinnedHistoryEntry = null;
    let showNoDetectionImages = false;
    let collapsedGroups = new Set();

    // Infinite scroll state for the image history list
    const HISTORY_PAGE_SIZE = 20;
    let currentFilteredHistoryItems = [];
    let historyRenderedCount = 0;
    let historyHasMore = true;
    let isLoadingMoreHistory = false;
    const useMockData = new URLSearchParams(window.location.search).get('mock') === '1' || window.location.protocol === 'file:';

    function createMockImageDataUrl(label, accent, bg) {
        const svg = `
            <svg xmlns="http://www.w3.org/2000/svg" width="800" height="600" viewBox="0 0 800 600">
                <rect width="800" height="600" fill="${bg}" rx="24" ry="24" />
                <rect x="36" y="36" width="728" height="528" rx="20" ry="20" fill="rgba(255,255,255,0.06)" stroke="rgba(255,255,255,0.2)" />
                <circle cx="250" cy="260" r="92" fill="${accent}" opacity="0.8" />
                <rect x="380" y="180" width="210" height="120" rx="16" ry="16" fill="rgba(255,255,255,0.16)" />
                <rect x="380" y="340" width="260" height="90" rx="16" ry="16" fill="rgba(255,255,255,0.12)" />
                <text x="60" y="560" font-family="Outfit, Arial, sans-serif" font-size="34" fill="white">${label}</text>
            </svg>`;
        return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
    }

    function getMockSettingsData() {
        return {
            imap_server: 'imap.example.com',
            email: 'demo@example.com',
            from_address: 'demo@example.com',
            subject: 'Mock motion alert',
            download_folder: './cam_attachments',
            model_name: 'yolov8n.pt',
            gemini_model: 'gemini-3.5-flash',
            telegram_chat_id: '123456789'
        };
    }

    function getMockHistoryData() {
        const now = Math.floor(Date.now() / 1000);
        return [
            {
                timestamp: now - 30,
                camera_id: 'Casa - Front Door',
                location: 'Front Door • Patio',
                objects: [{ class: 'person', confidence: 0.94 }, { class: 'car', confidence: 0.82 }],
                description: 'Person detected near the entrance with a vehicle passing by.',
                image_url: createMockImageDataUrl('Front Door', '#4e70f9', '#11131b')
            },
            {
                timestamp: now - 180,
                camera_id: 'Casa - Back Yard',
                location: 'Back Yard • Garden',
                objects: [{ class: 'dog', confidence: 0.91 }],
                description: 'A dog was detected in the garden area.',
                image_url: createMockImageDataUrl('Back Yard', '#00bbf9', '#101a1e')
            },
            {
                timestamp: now - 420,
                camera_id: 'Casa - Garage',
                location: 'Garage • Driveway',
                objects: [{ class: 'car', confidence: 0.87 }],
                description: 'Vehicle detected parked in the driveway.',
                image_url: createMockImageDataUrl('Garage', '#ffb703', '#1f1a0f')
            },
            {
                timestamp: now - 780,
                camera_id: 'Casa - Front Door',
                location: 'Front Door • Patio',
                objects: [],
                description: 'No relevant detections for this frame.',
                image_url: createMockImageDataUrl('Front Door (No Det)', '#9ba0b0', '#16181d')
            },
            {
                timestamp: now - 1100,
                camera_id: 'Casa - Back Yard',
                location: 'Back Yard • Garden',
                objects: [{ class: 'person', confidence: 0.93 }, { class: 'cat', confidence: 0.86 }],
                description: 'Person and cat detected in the garden area.',
                image_url: createMockImageDataUrl('Back Yard', '#00b249', '#0f1a14')
            },
            {
                timestamp: now - 1600,
                camera_id: 'Casa - Garage',
                location: 'Garage • Driveway',
                objects: [{ class: 'person', confidence: 0.9 }, { class: 'motorcycle', confidence: 0.77 }],
                description: 'Person and motorcycle detected near the garage.',
                image_url: createMockImageDataUrl('Garage', '#ff477e', '#1d1219')
            },
            {
                timestamp: now - 30,
                camera_id: 'Casa - Front Door',
                location: 'Front Door • Patio',
                objects: [{ class: 'person', confidence: 0.94 }, { class: 'car', confidence: 0.82 }],
                description: 'Person detected near the entrance with a vehicle passing by.',
                image_url: createMockImageDataUrl('Front Door', '#4e70f9', '#11131b')
            },
            {
                timestamp: now - 180,
                camera_id: 'Galeron - Back Yard',
                location: 'Back Yard • Garden',
                objects: [{ class: 'dog', confidence: 0.91 }],
                description: 'A dog was detected in the garden area.',
                image_url: createMockImageDataUrl('Back Yard', '#00bbf9', '#101a1e')
            },
            {
                timestamp: now - 420,
                camera_id: 'Galeron - Garage',
                location: 'Garage • Driveway',
                objects: [{ class: 'car', confidence: 0.87 }],
                description: 'Vehicle detected parked in the driveway.',
                image_url: createMockImageDataUrl('Garage', '#ffb703', '#1f1a0f')
            },
            {
                timestamp: now - 780,
                camera_id: 'Galeron - Front Door',
                location: 'Front Door • Patio',
                objects: [],
                description: 'No relevant detections for this frame.',
                image_url: createMockImageDataUrl('Front Door (No Det)', '#9ba0b0', '#16181d')
            },
            {
                timestamp: now - 1100,
                camera_id: 'Galeron - Back Yard',
                location: 'Back Yard • Garden',
                objects: [{ class: 'person', confidence: 0.93 }, { class: 'cat', confidence: 0.86 }],
                description: 'Person and cat detected in the garden area.',
                image_url: createMockImageDataUrl('Back Yard', '#00b249', '#0f1a14')
            },
            {
                timestamp: now - 1600,
                camera_id: 'Galeron - Garage',
                location: 'Garage • Driveway',
                objects: [{ class: 'person', confidence: 0.9 }, { class: 'motorcycle', confidence: 0.77 }],
                description: 'Person and motorcycle detected near the garage.',
                image_url: createMockImageDataUrl('Garage', '#ff477e', '#1d1219')
            }
        ];
    }

    function getMockSnooze() {
        return {
            picture: { remaining: 0, forever: false },
            video: { remaining: 0, forever: false }
        };
    }

    function getMockChatId() {
        return { picture: '', video: '' };
    }

    function getMockStatusData() {
        return {
            uptime: 13200,
            global: {
                pictures_analyzed: 42,
                matches_found: 19,
                notifications_sent: 11,
                snooze: getMockSnooze(),
                chat_id: getMockChatId()
            },
            cameras: {
                'Galeron - Front Door': { pictures_analyzed: 18, matches_found: 9, notifications_sent: 5, snooze: getMockSnooze(), chat_id: getMockChatId() },
                'Galeron - Back Yard': { pictures_analyzed: 14, matches_found: 6, notifications_sent: 3, snooze: getMockSnooze(), chat_id: getMockChatId() },
                'Galeron - Garage': { pictures_analyzed: 10, matches_found: 4, notifications_sent: 3, snooze: getMockSnooze(), chat_id: getMockChatId() },
                'Casa - Front Door': { pictures_analyzed: 18, matches_found: 9, notifications_sent: 5, snooze: getMockSnooze(), chat_id: getMockChatId() },
                'Casa - Back Yard': { pictures_analyzed: 14, matches_found: 6, notifications_sent: 3, snooze: getMockSnooze(), chat_id: getMockChatId() },
                'Casa - Garage': { pictures_analyzed: 10, matches_found: 4, notifications_sent: 3, snooze: getMockSnooze(), chat_id: getMockChatId() },
                'Otra - Front Door': { pictures_analyzed: 18, matches_found: 9, notifications_sent: 5, snooze: getMockSnooze(), chat_id: getMockChatId() },
                'Otra - Back Yard': { pictures_analyzed: 14, matches_found: 6, notifications_sent: 3, snooze: getMockSnooze(), chat_id: getMockChatId() },
                'Otra - Garage': { pictures_analyzed: 10, matches_found: 4, notifications_sent: 3, snooze: getMockSnooze(), chat_id: getMockChatId() }
            },
            devices: {
                Galeron: { snooze: getMockSnooze(), chat_id: getMockChatId() },
                Casa: { snooze: getMockSnooze(), chat_id: getMockChatId() },
                Otra: { snooze: getMockSnooze(), chat_id: getMockChatId() }
            },
            last_detection: {
                timestamp: Math.floor(Date.now() / 1000) - 30,
                location: 'Front Door • Patio',
                description: 'Mock detection for scroll testing.',
                objects: [{ class: 'person', confidence: 0.94, person_name: 'Mia' }],
                camera_id: 'Galeron - Front Door',
                image_filename: 'mock.jpg',
                image_path: createMockImageDataUrl('Mock Detection', '#4e70f9', '#11131b')
            }
        };
    }

    // Toast Notification helper
    function showToast(message, type = 'info') {
        const container = document.getElementById('toast-container');
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;

        let emoji = 'ℹ️';
        if (type === 'success') emoji = '✅';
        else if (type === 'error') emoji = '❌';
        else if (type === 'warning') emoji = '⚠️';

        toast.innerHTML = `<span>${emoji}</span><div>${message}</div>`;
        container.appendChild(toast);

        setTimeout(() => {
            toast.remove();
        }, 3000);
    }

    // Secrets are masked server-side before /api/settings responds; just handle empty values here.
    function maskValue(key, val) {
        return val || 'Not Configured';
    }

    // Fetch and populate settings read-only view
    async function loadSettings() {
        try {
            settingsView.innerHTML = '<div class="placeholder-text">Loading settings...</div>';

            if (useMockData) {
                const settings = getMockSettingsData();
                let html = '';
                for (const key of Object.keys(settings).sort()) {
                    const cleanKey = key.replace(/_/g, ' ');
                    const displayVal = maskValue(key, settings[key]);
                    html += `
                        <div class="setting-row-view">
                            <span class="setting-key">${cleanKey}</span>
                            <span class="setting-val">${displayVal}</span>
                        </div>
                    `;
                }
                settingsView.innerHTML = html;
                return;
            }

            const response = await fetch('/api/settings');
            if (!response.ok) throw new Error('Failed to load settings');
            const settings = await response.json();

            let html = '';
            // Group and format settings
            const sortedKeys = Object.keys(settings).sort();
            for (const key of sortedKeys) {
                const cleanKey = key.replace(/_/g, ' ');
                const displayVal = maskValue(key, settings[key]);

                html += `
                    <div class="setting-row-view">
                        <span class="setting-key">${cleanKey}</span>
                        <span class="setting-val">${displayVal}</span>
                    </div>
                `;
            }
            settingsView.innerHTML = html;
        } catch (error) {
            console.error(error);
            settingsView.innerHTML = '<div class="placeholder-text text-error">Error loading settings.</div>';
        }
    }

    // Modal Action Listeners
    btnOpenSettings.addEventListener('click', () => {
        loadSettings();
        settingsDialog.showModal();
    });

    btnCloseSettings.addEventListener('click', () => {
        settingsDialog.close();
    });

    // Close on backdrop click
    settingsDialog.addEventListener('click', (e) => {
        const rect = settingsDialog.getBoundingClientRect();
        const isInDialog = (rect.top <= e.clientY && e.clientY <= rect.top + rect.height &&
                            rect.left <= e.clientX && e.clientX <= rect.left + rect.width);
        if (!isInDialog) {
            settingsDialog.close();
        }
    });

    function getCameraTreeEntries(cameras, searchTerm = '') {
        const normalizedSearch = searchTerm.trim().toLowerCase();
        const names = Object.keys(cameras)
            .filter(name => !normalizedSearch || name.toLowerCase().includes(normalizedSearch))
            .sort((a, b) => a.localeCompare(b));

        const grouped = new Map();
        names.forEach(name => {
            const separatorIndex = name.indexOf(' - ');
            const home = separatorIndex >= 0 ? name.slice(0, separatorIndex).trim() : 'Other';
            if (!grouped.has(home)) {
                grouped.set(home, []);
            }
            grouped.get(home).push(name);
        });

        const entries = [];
        Array.from(grouped.entries())
            .sort(([homeA], [homeB]) => homeA.localeCompare(homeB))
            .forEach(([home, cameraNames]) => {
                entries.push({ type: 'group', home, cameraNames: [...cameraNames] });
                cameraNames.sort((a, b) => a.localeCompare(b)).forEach(name => {
                    entries.push({ type: 'camera', name, home });
                });
            });

        return entries;
    }

    // Renders one media type's snooze sub-status ({remaining, forever}) as a short label.
    function formatMediaSnooze(status) {
        if (!status) return 'Active';
        if (status.forever) return '∞';
        if (status.remaining > 0) return `${Math.ceil(status.remaining / 60)}m`;
        return 'Active';
    }

    function isSnoozeActive(snooze) {
        if (!snooze) return false;
        return ['picture', 'video'].some(t => snooze[t] && (snooze[t].forever || snooze[t].remaining > 0));
    }

    function formatSnoozeCellLabel(snooze) {
        if (!snooze) return 'Active';
        return `📷 ${formatMediaSnooze(snooze.picture)} · 🎥 ${formatMediaSnooze(snooze.video)}`;
    }

    function updateCamerasStatsTable(cameras, devices) {
        const searchTerm = (cameraSearchInput?.value || '').trim().toLowerCase();
        const treeEntries = getCameraTreeEntries(cameras, searchTerm);

        if (treeEntries.length === 0) {
            camerasStatsBody.innerHTML = `
                <tr>
                    <td colspan="5" style="text-align: center; padding: 12px 0; color: var(--text-muted); font-style: italic;">No matching cameras.</td>
                </tr>
            `;
            return;
        }

        let html = '';
        let currentGroup = null;
        treeEntries.forEach(entry => {
            if (entry.type === 'group') {
                currentGroup = entry.home;
                const isCollapsed = collapsedGroups.has(entry.home);
                const groupedCameras = (entry.cameraNames || []).map(name => cameras[name]).filter(Boolean);
                const totalAnalyzed = groupedCameras.reduce((sum, cam) => sum + (cam.pictures_analyzed || 0), 0);
                const totalMatches = groupedCameras.reduce((sum, cam) => sum + (cam.matches_found || 0), 0);
                const totalAlerts = groupedCameras.reduce((sum, cam) => sum + (cam.notifications_sent || 0), 0);
                const deviceData = (devices || {})[entry.home];
                const deviceSnoozeText = formatSnoozeCellLabel(deviceData?.snooze);
                const deviceSnoozeColor = isSnoozeActive(deviceData?.snooze) ? 'var(--warning)' : 'var(--success)';
                html += `
                    <tr class="camera-group-row">
                        <td colspan="5" style="padding: 8px 0 6px 4px; font-size: 0.78rem; font-weight: 700; color: var(--accent); text-transform: uppercase; letter-spacing: 0.06em;">
                            <div style="display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap;">
                                <button class="camera-group-toggle" type="button" data-home="${entry.home}" style="background:none;border:none;color:inherit;padding:0;cursor:pointer;display:flex;align-items:center;gap:6px;font:inherit;">
                                    <span>${isCollapsed ? '▶' : '▼'}</span>
                                    <span>${entry.home}</span>
                                </button>
                                <span class="camera-group-summary" style="display:flex; align-items:center; gap:10px;">
                                    ${groupedCameras.length} cams • ${totalAnalyzed} analyzed • ${totalMatches} matches • ${totalAlerts} alerts
                                    <button class="notifications-cell-btn device-notifications-btn" data-device="${entry.home}" style="background:none;border:none;color:${deviceSnoozeColor};font-weight:500;cursor:pointer;text-transform:none;letter-spacing:normal;">${deviceSnoozeText}</button>
                                </span>
                            </div>
                        </td>
                    </tr>
                `;
                return;
            }

            if (collapsedGroups.has(currentGroup)) {
                return;
            }

            const name = entry.name;
            const cam = cameras[name];
            const snoozeText = formatSnoozeCellLabel(cam.snooze);
            const snoozeColor = isSnoozeActive(cam.snooze) ? 'var(--warning)' : 'var(--success)';

            html += `
                <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.03);">
                    <td class="camera-row" data-camera="${name}" style="padding: 8px 0 8px 18px; font-weight: 500; cursor: pointer; color: var(--text-main);">${name}</td>
                    <td style="padding: 8px 0; text-align: center;">${cam.pictures_analyzed}</td>
                    <td style="padding: 8px 0; text-align: center; color: var(--accent);">${cam.matches_found}</td>
                    <td style="padding: 8px 0; text-align: center; color: var(--primary);">${cam.notifications_sent}</td>
                    <td style="padding: 8px 0; text-align: right;">
                        <button class="notifications-cell-btn" data-camera="${name}" style="background:none;border:none;color:${snoozeColor};font-weight:500;cursor:pointer;">${snoozeText}</button>
                    </td>
                </tr>
            `;
        });

        if (btnToggleAllGroups) {
            const groupNames = [...new Set(Object.keys(cameras).map(name => {
                const separatorIndex = name.indexOf(' - ');
                return separatorIndex >= 0 ? name.slice(0, separatorIndex).trim() : 'Other';
            }))];
            const allCollapsed = groupNames.length > 0 && groupNames.every(home => collapsedGroups.has(home));
            btnToggleAllGroups.textContent = allCollapsed ? 'Expand All' : 'Collapse All';
        }

        camerasStatsBody.innerHTML = html;
        document.querySelectorAll('.camera-group-toggle').forEach(button => {
            button.addEventListener('click', () => {
                const home = button.dataset.home;
                if (collapsedGroups.has(home)) {
                    collapsedGroups.delete(home);
                } else {
                    collapsedGroups.add(home);
                }
                updateCamerasStatsTable(cameras, devices);
            });
        });
        document.querySelectorAll('.notifications-cell-btn[data-camera]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const camera = btn.dataset.camera;
                showNotificationsMenu({ type: 'camera', name: camera }, btn, cameras[camera]);
                e.stopPropagation();
            });
        });
        document.querySelectorAll('.device-notifications-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const device = btn.dataset.device;
                showNotificationsMenu({ type: 'device', name: device }, btn, (devices || {})[device]);
                e.stopPropagation();
            });
        });
    }

    // Cached so every popover open doesn't re-fetch it; there's no UI that changes it at runtime.
    let cachedDefaultChatId = null;
    async function getDefaultChatId() {
        if (cachedDefaultChatId !== null) return cachedDefaultChatId;
        try {
            const settings = useMockData ? getMockSettingsData() : await (await fetch('/api/settings')).json();
            const val = settings.telegram_chat_id;
            cachedDefaultChatId = (val && val !== 'Not Configured') ? val : '';
        } catch (err) {
            cachedDefaultChatId = '';
        }
        return cachedDefaultChatId;
    }

    // In-place notifications menu (reused): per media type, lets you snooze and/or
    // redirect notifications to a specific Telegram chat ID for this camera ('all' for global).
    let notificationsMenu = null;
    function createNotificationsMenu() {
        notificationsMenu = document.createElement('div');
        notificationsMenu.className = 'notifications-popover';
        notificationsMenu.style.position = 'absolute';
        notificationsMenu.style.zIndex = 9999;
        notificationsMenu.style.background = 'var(--card-bg)';
        notificationsMenu.style.border = '1px solid var(--card-border)';
        notificationsMenu.style.padding = '10px';
        notificationsMenu.style.borderRadius = '6px';
        notificationsMenu.style.boxShadow = '0 6px 14px rgba(0,0,0,0.4)';
        notificationsMenu.innerHTML = `
            <div style="display:flex; flex-direction:column; gap:12px; min-width:270px;">
                ${['picture', 'video'].map(mediaType => `
                    <div>
                        <div style="font-size:0.75rem; color:var(--text-muted); margin-bottom:4px;">
                            ${mediaType === 'picture' ? '📷 Pictures' : '🎥 Videos'}
                        </div>
                        <div style="display:flex; gap:6px; margin-bottom:6px;">
                            <input type="text" class="camera-search-input chat-id-input" data-media="${mediaType}" style="flex:1; width:auto;" placeholder="Chat ID">
                            <button class="btn btn-secondary pop-save-chat" data-media="${mediaType}">Save</button>
                        </div>
                        <div style="display:flex; gap:6px; flex-wrap:wrap;">
                            <button class="btn btn-secondary pop-snooze" data-media="${mediaType}" data-minutes="10">10m</button>
                            <button class="btn btn-secondary pop-snooze" data-media="${mediaType}" data-minutes="60">1h</button>
                            <button class="btn btn-secondary pop-snooze" data-media="${mediaType}" data-minutes="240">4h</button>
                            <button class="btn btn-secondary pop-snooze" data-media="${mediaType}" data-minutes="forever">Forever</button>
                            <button class="btn btn-outline pop-cancel" data-media="${mediaType}">Cancel</button>
                        </div>
                    </div>
                `).join('')}
            </div>
        `;
        document.body.appendChild(notificationsMenu);
        notificationsMenu.addEventListener('click', (e) => e.stopPropagation());
    }

    // `scope` is { type: 'global'|'device'|'camera', name: string } -- 'name' is unused for
    // 'global'. Resolution order enforced server-side is camera > device > global.
    async function showNotificationsMenu(scope, anchorEl, scopeData) {
        if (!notificationsMenu) createNotificationsMenu();
        // Position menu near anchor
        const rect = anchorEl.getBoundingClientRect();
        notificationsMenu.style.top = `${rect.bottom + window.scrollY + 6}px`;
        notificationsMenu.style.left = `${rect.left + window.scrollX}px`;
        notificationsMenu.style.display = 'block';

        const chatIdOverrides = scopeData?.chat_id || {};
        const defaultChatId = await getDefaultChatId();

        // Wire chat ID inputs
        notificationsMenu.querySelectorAll('.chat-id-input').forEach(input => {
            const mediaType = input.dataset.media;
            input.value = chatIdOverrides[mediaType] || '';
            input.placeholder = defaultChatId ? `Default: ${defaultChatId}` : 'Chat ID';
        });
        notificationsMenu.querySelectorAll('.pop-save-chat').forEach(b => {
            b.onclick = async () => {
                const input = notificationsMenu.querySelector(`.chat-id-input[data-media="${b.dataset.media}"]`);
                await sendNotifyChat(scope, input.value.trim(), b.dataset.media);
                hideNotificationsMenu();
            };
        });

        // Wire snooze buttons
        notificationsMenu.querySelectorAll('.pop-snooze').forEach(b => {
            b.onclick = async () => {
                const minutes = b.dataset.minutes === 'forever' ? 'forever' : parseInt(b.dataset.minutes, 10);
                await sendSnooze(scope, minutes, b.dataset.media);
                hideNotificationsMenu();
            };
        });
        notificationsMenu.querySelectorAll('.pop-cancel').forEach(b => {
            b.onclick = async () => {
                await sendSnooze(scope, 0, b.dataset.media);
                hideNotificationsMenu();
            };
        });
    }

    function hideNotificationsMenu() {
        if (notificationsMenu) notificationsMenu.style.display = 'none';
    }

    // Builds the {camera, device} pair the backend expects from a scope object, and a
    // human-readable label for toasts.
    function describeScope(scope) {
        if (scope.type === 'device') return { camera: 'all', device: scope.name, label: `Device "${scope.name}"` };
        if (scope.type === 'camera') return { camera: scope.name, device: '', label: `Camera "${scope.name}"` };
        return { camera: 'all', device: '', label: 'All cameras' };
    }

    // Send chat-ID-override API call for the given scope (camera, device, or global) and
    // media type ('picture' or 'video'). An empty chatId clears the override.
    async function sendNotifyChat(scope, chatId, mediaType) {
        const { camera, device, label } = describeScope(scope);
        try {
            const response = await fetch('/api/notify-chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ chat_id: chatId, camera, device, media_type: mediaType })
            });
            if (!response.ok) throw new Error('notify-chat failed');
            if (chatId) showToast(`${label} (${mediaType}s) will notify chat ${chatId}`, 'success');
            else showToast(`${label} (${mediaType}s) chat override cleared`, 'success');
            fetchStatus();
        } catch (err) {
            showToast('Failed to save chat ID', 'error');
        }
    }

    // Send snooze API call for the given scope (camera, device, or global) and media type
    // ('picture', 'video', or 'all' for both).
    async function sendSnooze(scope, minutes, mediaType = 'all') {
        const { camera, device, label } = describeScope(scope);
        try {
            const response = await fetch('/api/snooze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ minutes, camera, device, media_type: mediaType })
            });
            if (!response.ok) throw new Error('snooze failed');
            const mediaText = mediaType === 'all' ? '' : ` (${mediaType}s)`;
            if (minutes === 0) showToast(`Snooze cancelled for ${label}${mediaText}`, 'success');
            else if (minutes === 'forever') showToast(`${label}${mediaText} snoozed forever`, 'warning');
            else showToast(`${label}${mediaText} snoozed for ${minutes} minutes`, 'warning');
            fetchStatus();
        } catch (err) {
            showToast('Failed to apply snooze', 'error');
        }
    }

    // Hide menu when clicking outside
    document.addEventListener('click', () => hideNotificationsMenu());

    function setSelectedDetection(entry, pinned = false) {
        if (pinned) {
            pinnedHistoryEntry = entry;
        } else {
            pinnedHistoryEntry = null;
        }
        updateUnpinButton();
        noDetectionPlaceholder.style.display = 'none';
        detectionContent.style.display = 'block';
        lastDetectionImage.src = `${entry.imageUrl}`;
        lastDetectionTime.textContent = new Date(entry.timestamp * 1000).toLocaleString();
        lastDetectionLocation.textContent = entry.location || 'API Upload';
        if (entry.objects && entry.objects.length > 0) {
            lastDetectionObjects.textContent = entry.objects.map(o => {
                let text = `${o.class} (${Math.round(o.confidence * 100)}%)`;
                if (o.person_name) text += ` [${o.person_name}]`;
                return text;
            }).join(', ');
        } else {
            lastDetectionObjects.textContent = 'None';
        }
        const faceMatches = (entry.objects || []).filter(o => o.class === 'person' && o.person_name).map(o => o.person_name);
        if (faceMatches.length > 0) {
            lastDetectionFaces.textContent = [...new Set(faceMatches)].join(', ');
            lastDetectionFaces.className = 'detail-value highlight-name';
        } else if ((entry.objects || []).some(o => o.class === 'person')) {
            lastDetectionFaces.textContent = 'Person detected, face unrecognized';
            lastDetectionFaces.className = 'detail-value';
        } else {
            lastDetectionFaces.textContent = 'No match found';
            lastDetectionFaces.className = 'detail-value';
        }
        lastDetectionDesc.textContent = entry.description || 'No Gemini description generated.';
        if (detectionTitle) {
            detectionTitle.textContent = pinnedHistoryEntry ? 'History Selection' : 'Last Detection';
        }
    }

    function updateClearHistoryButton() {
        if (btnClearHistoryFilter) {
            btnClearHistoryFilter.style.display = currentCameraFilter ? 'inline-flex' : 'none';
        }
    }

    function updateHistoryVisibilityToggle() {
        if (btnToggleNoDetections) {
            btnToggleNoDetections.textContent = showNoDetectionImages ? 'Hide No Detections' : 'Show No Detections';
            btnToggleNoDetections.classList.toggle('btn-toggle-active', showNoDetectionImages);
        }
    }

    function shouldShowHistoryEntry(entry) {
        if (showNoDetectionImages) return true;
        return Array.isArray(entry.objects) && entry.objects.length > 0;
    }

    function updateUnpinButton() {
        if (btnUnpinHistory) {
            btnUnpinHistory.style.display = pinnedHistoryEntry ? 'inline-flex' : 'none';
        }
    }

    // Loads (or refreshes) the head of the history list for a camera filter. Refetches from
    // offset 0 each time, sized to cover at least what's already loaded, so a periodic refresh
    // picks up new detections without losing entries the user has already scrolled through.
    async function loadHistory(camera = null) {
        const requestId = ++lastHistoryRequestId;
        const filterChanged = camera !== currentCameraFilter;
        if (filterChanged) {
            historyData = [];
            historyHasMore = true;
        }
        const limit = Math.max(HISTORY_PAGE_SIZE, historyData.length);
        const query = `?limit=${limit}${camera ? `&camera=${encodeURIComponent(camera)}` : ''}`;

        if (useMockData) {
            historyData = getMockHistoryData();
            historyHasMore = false;
            if (requestId !== lastHistoryRequestId) return;
            currentCameraFilter = camera;
            updateClearHistoryButton();
            renderHistoryList();
            return;
        }

        try {
            const response = await fetch(`/api/history${query}`);
            if (!response.ok) throw new Error('Unable to load image history.');
            const page = await response.json();
            if (requestId !== lastHistoryRequestId) return;
            historyData = page;
            historyHasMore = page.length >= limit;
            currentCameraFilter = camera;
            updateClearHistoryButton();
            renderHistoryList();
        } catch (error) {
            if (requestId !== lastHistoryRequestId) return;
            historyData = getMockHistoryData();
            historyHasMore = false;
            currentCameraFilter = camera;
            updateClearHistoryButton();
            renderHistoryList();
        }
    }

    // Pulls the next older page from the server (offset = what we've already fetched) and
    // appends it. Once this goes past the server's in-memory cache it falls back to reading
    // sidecars off disk, which is how infinite scroll reaches beyond MAX_CACHE_SIZE.
    async function fetchMoreHistoryPage() {
        if (isLoadingMoreHistory || !historyHasMore || useMockData) return;
        isLoadingMoreHistory = true;
        const requestId = lastHistoryRequestId;
        const camera = currentCameraFilter;
        const offset = historyData.length;
        const query = `?offset=${offset}&limit=${HISTORY_PAGE_SIZE}${camera ? `&camera=${encodeURIComponent(camera)}` : ''}`;
        try {
            const response = await fetch(`/api/history${query}`);
            if (!response.ok) throw new Error('Unable to load more image history.');
            const page = await response.json();
            if (requestId !== lastHistoryRequestId) return;
            historyData = historyData.concat(page);
            historyHasMore = page.length >= HISTORY_PAGE_SIZE;
            renderHistoryList();
        } catch (error) {
            historyHasMore = false;
        } finally {
            isLoadingMoreHistory = false;
        }
    }

    function buildHistoryItemElement(entry) {
        const date = new Date(entry.timestamp * 1000).toLocaleString();
        const title = `${entry.camera_id} • ${date}`;
        const detectionSummary = entry.objects && entry.objects.length > 0
            ? entry.objects.map(o => `${o.class} (${Math.round(o.confidence * 100)}%)`).join(', ')
            : 'No detections';

        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'history-item';
        button.dataset.imageUrl = entry.image_url;

        const thumbnail = document.createElement('div');
        thumbnail.className = 'history-thumbnail';
        const img = document.createElement('img');
        img.src = entry.image_url;
        img.alt = title;
        img.loading = 'lazy';
        thumbnail.appendChild(img);

        const meta = document.createElement('div');
        meta.className = 'history-meta';
        const titleEl = document.createElement('strong');
        titleEl.textContent = title;
        const summaryEl = document.createElement('span');
        summaryEl.textContent = detectionSummary;
        meta.appendChild(titleEl);
        meta.appendChild(summaryEl);

        button.appendChild(thumbnail);
        button.appendChild(meta);

        button.addEventListener('click', () => {
            setSelectedDetection({
                imageUrl: entry.image_url,
                timestamp: entry.timestamp,
                location: entry.location,
                objects: entry.objects,
                description: entry.description
            }, true);
        });

        return button;
    }

    // Appends the next page of already-filtered history items to the DOM.
    function renderNextHistoryPage(count = HISTORY_PAGE_SIZE) {
        const nextItems = currentFilteredHistoryItems.slice(historyRenderedCount, historyRenderedCount + count);
        if (nextItems.length === 0) return;
        const fragment = document.createDocumentFragment();
        nextItems.forEach(entry => fragment.appendChild(buildHistoryItemElement(entry)));
        imageHistoryList.appendChild(fragment);
        historyRenderedCount += nextItems.length;
    }

    // Loads more pages while there's still room in the scroll container (e.g. on a tall screen)
    // or the user has scrolled near the bottom of the list. Once the locally-buffered items
    // (already fetched from the server) run out, pulls another page from the server instead.
    function maybeLoadMoreHistory() {
        if (historyRenderedCount < currentFilteredHistoryItems.length) {
            const nearBottom = imageHistoryList.scrollTop + imageHistoryList.clientHeight >= imageHistoryList.scrollHeight - 150;
            const notScrollableYet = imageHistoryList.scrollHeight <= imageHistoryList.clientHeight;
            if (nearBottom || notScrollableYet) {
                renderNextHistoryPage();
                maybeLoadMoreHistory();
            }
            return;
        }
        if (historyHasMore) {
            fetchMoreHistoryPage();
        }
    }

    function renderHistoryList() {
        const filteredItems = (currentCameraFilter ? historyData.filter(entry => entry.camera_id === currentCameraFilter) : historyData)
            .filter(shouldShowHistoryEntry);
        historyFilterLabel.textContent = currentCameraFilter ? `Showing ${currentCameraFilter}` : 'Showing all cameras';
        updateClearHistoryButton();
        updateHistoryVisibilityToggle();

        // Keep showing as many items as were already loaded (at least one page), so a periodic
        // background refresh doesn't collapse a scrolled-down infinite-scroll list back to page one.
        const previousScrollTop = imageHistoryList.scrollTop;
        const itemsToRestore = Math.max(HISTORY_PAGE_SIZE, historyRenderedCount);

        currentFilteredHistoryItems = filteredItems;
        historyRenderedCount = 0;
        imageHistoryList.innerHTML = '';
        imageHistoryEmpty.style.display = filteredItems.length ? 'none' : 'block';
        imageHistoryEmpty.textContent = filteredItems.length ? '' : (
            currentCameraFilter
                ? 'No images found for this camera.'
                : (showNoDetectionImages ? 'No images recorded yet.' : 'No images with detections recorded yet.')
        );

        renderNextHistoryPage(itemsToRestore);
        imageHistoryList.scrollTop = previousScrollTop;
        maybeLoadMoreHistory();
    }

    function setTableCameraSelection() {
        document.querySelectorAll('.camera-row').forEach(row => {
            row.onclick = () => {
                const camera = row.dataset.camera;
                currentCameraFilter = camera;
                updateClearHistoryButton();
                loadHistory(camera);
                document.querySelectorAll('.camera-row').forEach(r => r.classList.toggle('selected-camera', r.dataset.camera === camera));
            };
            row.classList.toggle('selected-camera', currentCameraFilter === row.dataset.camera);
        });
    }

    function clearCameraSelection() {
        currentCameraFilter = null;
        document.querySelectorAll('.camera-row').forEach(row => row.classList.remove('selected-camera'));
        updateClearHistoryButton();
        loadHistory();
    }

    // Fetch and update status and statistics
    async function fetchStatus() {
        if (isFetchingStatus) return;
        isFetchingStatus = true;

        try {
            let data;
            if (useMockData) {
                data = getMockStatusData();
            } else {
                const response = await fetch('/api/status');
                if (!response.ok) throw new Error('Offline');
                data = await response.json();
            }

            currentStatusData = data;

            // Server status header
            serverStatus.className = 'status-badge';
            statusText.textContent = useMockData ? 'Mock Data' : 'Online';

            // Uptime
            const hours = Math.floor(data.uptime / 3600);
            const minutes = Math.floor((data.uptime % 3600) / 60);
            uptimeValue.textContent = `${hours}h ${minutes}m`;

            // Footer summary in camera table
            const totalAnalyzed = Object.values(data.cameras || {}).reduce((sum, cam) => sum + (cam.pictures_analyzed || 0), 0);
            const totalMatches = Object.values(data.cameras || {}).reduce((sum, cam) => sum + (cam.matches_found || 0), 0);
            const totalAlerts = Object.values(data.cameras || {}).reduce((sum, cam) => sum + (cam.notifications_sent || 0), 0);
            if (cameraSummaryAnalyzed) cameraSummaryAnalyzed.textContent = totalAnalyzed;
            if (cameraSummaryMatches) cameraSummaryMatches.textContent = totalMatches;
            if (cameraSummaryAlerts) cameraSummaryAlerts.textContent = totalAlerts;

            // Global notifications status (footer row)
            if (globalNotificationsBtn && data.global) {
                globalNotificationsBtn.textContent = formatSnoozeCellLabel(data.global.snooze);
                globalNotificationsBtn.style.color = isSnoozeActive(data.global.snooze) ? 'var(--warning)' : 'var(--success)';
            }

            // Cameras Stats & Dropdown Select Option Updates
            updateCamerasStatsTable(data.cameras, data.devices);
            setTableCameraSelection();
            await loadHistory(currentCameraFilter);

            // Last Detection
            if (!pinnedHistoryEntry && data.last_detection) {
                if (detectionTitle) detectionTitle.textContent = 'Last Detection';
                noDetectionPlaceholder.style.display = 'none';
                detectionContent.style.display = 'block';

                // Set image source (with cache buster)
                lastDetectionImage.src = useMockData ? data.last_detection.image_path : `/api/last-image?t=${new Date().getTime()}`;

                const timeStr = new Date(data.last_detection.timestamp * 1000).toLocaleString();
                lastDetectionTime.textContent = timeStr;
                lastDetectionLocation.textContent = data.last_detection.location || 'API Upload';

                // Formatted objects list
                const objects = data.last_detection.objects || [];
                if (objects.length > 0) {
                    lastDetectionObjects.textContent = objects.map(o => {
                        let text = `${o.class} (${Math.round(o.confidence * 100)}%)`;
                        if (o.person_name) text += ` [${o.person_name}]`;
                        return text;
                    }).join(', ');
                } else {
                    lastDetectionObjects.textContent = 'None';
                }

                // Faces
                const faceMatches = objects.filter(o => o.class === 'person' && o.person_name).map(o => o.person_name);
                if (faceMatches.length > 0) {
                    lastDetectionFaces.textContent = [...new Set(faceMatches)].join(', ');
                    lastDetectionFaces.className = 'detail-value highlight-name';
                } else if (objects.some(o => o.class === 'person')) {
                    lastDetectionFaces.textContent = 'Person detected, face unrecognized';
                    lastDetectionFaces.className = 'detail-value';
                } else {
                    lastDetectionFaces.textContent = 'No match found';
                    lastDetectionFaces.className = 'detail-value';
                }

                // Gemini Analysis Description
                lastDetectionDesc.textContent = data.last_detection.description || 'No Gemini description generated.';
            } else if (!pinnedHistoryEntry) {
                noDetectionPlaceholder.style.display = 'block';
                detectionContent.style.display = 'none';
            }
        } catch (error) {
            currentStatusData = getMockStatusData();
            serverStatus.className = 'status-badge error-badge';
            statusText.textContent = 'Mock Data';
            console.error('Status fetch error:', error);
            updateCamerasStatsTable(currentStatusData.cameras, currentStatusData.devices);
            setTableCameraSelection();
            await loadHistory(currentCameraFilter);
        } finally {
            isFetchingStatus = false;
        }
    }

    // Snooze Trigger Action
    document.querySelectorAll('.snooze-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const minutes = parseInt(btn.dataset.minutes, 10);
            const camera = snoozeCameraSelect.value;
            try {
                const response = await fetch('/api/snooze', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ minutes, camera })
                });
                if (!response.ok) throw new Error();
                const targetText = camera === 'all' ? 'All cameras' : `Camera "${camera}"`;
                showToast(`${targetText} snoozed for ${minutes} minutes`, 'warning');
                fetchStatus();
            } catch (error) {
                showToast('Failed to apply snooze', 'error');
            }
        });
    });

    if (globalNotificationsBtn) {
        globalNotificationsBtn.addEventListener('click', (e) => {
            showNotificationsMenu({ type: 'global', name: 'all' }, globalNotificationsBtn, currentStatusData?.global);
            e.stopPropagation();
        });
    }

    if (cameraSearchInput) {
        cameraSearchInput.addEventListener('input', () => {
            if (currentStatusData) {
                updateCamerasStatsTable(currentStatusData.cameras, currentStatusData.devices);
                setTableCameraSelection();
            }
        });
    }

    if (btnToggleAllGroups) {
        btnToggleAllGroups.addEventListener('click', () => {
            const allCollapsed = collapsedGroups.size > 0 && collapsedGroups.size === new Set(Object.keys(currentStatusData?.cameras || {}).map(name => {
                const separatorIndex = name.indexOf(' - ');
                return separatorIndex >= 0 ? name.slice(0, separatorIndex).trim() : 'Other';
            })).size;
            const groupNames = [...new Set(Object.keys(currentStatusData?.cameras || {}).map(name => {
                const separatorIndex = name.indexOf(' - ');
                return separatorIndex >= 0 ? name.slice(0, separatorIndex).trim() : 'Other';
            }))];

            if (allCollapsed) {
                collapsedGroups.clear();
                btnToggleAllGroups.textContent = 'Collapse All';
            } else {
                collapsedGroups = new Set(groupNames);
                btnToggleAllGroups.textContent = 'Expand All';
            }

            if (currentStatusData) {
                updateCamerasStatsTable(currentStatusData.cameras, currentStatusData.devices);
                setTableCameraSelection();
            }
        });
    }

    if (btnClearHistoryFilter) {
        btnClearHistoryFilter.addEventListener('click', () => {
            clearCameraSelection();
        });
    }

    if (btnToggleNoDetections) {
        btnToggleNoDetections.addEventListener('click', () => {
            showNoDetectionImages = !showNoDetectionImages;
            updateHistoryVisibilityToggle();
            renderHistoryList();
        });
    }

    if (btnUnpinHistory) {
        btnUnpinHistory.addEventListener('click', () => {
            pinnedHistoryEntry = null;
            updateUnpinButton();
            fetchStatus();
        });
    }

    updateHistoryVisibilityToggle();

    imageHistoryList.addEventListener('scroll', maybeLoadMoreHistory, { passive: true });

    // Splitter Drag Functionality
    const splitter = document.getElementById('splitter-v');
    const topContainer = document.getElementById('cameras-stats-container');
    const bottomContainer = document.getElementById('image-history-container');
    const statsCard = document.querySelector('.stats-card');

    if (splitter && topContainer && bottomContainer && statsCard) {
        let isDragging = false;
        let startY = 0;
        let startTopHeight = 0;

        const onMouseDown = (e) => {
            isDragging = true;
            startY = e.clientY || (e.touches && e.touches[0].clientY);
            startTopHeight = topContainer.getBoundingClientRect().height;
            splitter.classList.add('dragging');
            document.body.style.cursor = 'ns-resize';
            document.body.style.userSelect = 'none';

            document.addEventListener('mousemove', onMouseMove);
            document.addEventListener('mouseup', onMouseUp);
            document.addEventListener('touchmove', onMouseMove);
            document.addEventListener('touchend', onMouseUp);
        };

        const onMouseMove = (e) => {
            if (!isDragging) return;
            const currentY = e.clientY || (e.touches && e.touches[0].clientY);
            const deltaY = currentY - startY;
            const cardHeight = statsCard.getBoundingClientRect().height;
            // Subtract header height and splitter height
            const availableHeight = cardHeight - 50; 

            let newTopHeight = startTopHeight + deltaY;
            const minHeight = 80;
            const maxHeight = availableHeight - minHeight;

            if (newTopHeight < minHeight) newTopHeight = minHeight;
            if (newTopHeight > maxHeight) newTopHeight = maxHeight;

            topContainer.style.flex = `0 0 ${newTopHeight}px`;
            bottomContainer.style.flex = '1 1 0%';
        };

        const onMouseUp = () => {
            if (!isDragging) return;
            isDragging = false;
            splitter.classList.remove('dragging');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';

            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onMouseUp);
            document.removeEventListener('touchmove', onMouseMove);
            document.removeEventListener('touchend', onMouseUp);
        };

        splitter.addEventListener('mousedown', onMouseDown);
        splitter.addEventListener('touchstart', onMouseDown, { passive: true });
    }

    // Vertical Splitter Drag Functionality (Column Resizing)
    const splitterH = document.getElementById('splitter-h');
    const leftCol = document.getElementById('dashboard-col-left');
    const rightCol = document.getElementById('dashboard-col-right');
    const dashboardGrid = document.querySelector('.dashboard-grid');

    if (splitterH && leftCol && rightCol && dashboardGrid) {
        let isDraggingH = false;
        let startX = 0;
        let startLeftWidth = 0;

        const onMouseDownH = (e) => {
            isDraggingH = true;
            startX = e.clientX || (e.touches && e.touches[0].clientX);
            startLeftWidth = leftCol.getBoundingClientRect().width;
            splitterH.classList.add('dragging');
            document.body.style.cursor = 'ew-resize';
            document.body.style.userSelect = 'none';

            document.addEventListener('mousemove', onMouseMoveH);
            document.addEventListener('mouseup', onMouseUpH);
            document.addEventListener('touchmove', onMouseMoveH);
            document.addEventListener('touchend', onMouseUpH);
        };

        const onMouseMoveH = (e) => {
            if (!isDraggingH) return;
            const currentX = e.clientX || (e.touches && e.touches[0].clientX);
            const deltaX = currentX - startX;
            const gridWidth = dashboardGrid.getBoundingClientRect().width;
            const availableWidth = gridWidth - 20; // Subtract splitter width & padding

            let newLeftWidth = startLeftWidth + deltaX;
            const minWidth = 220;
            const maxWidth = availableWidth - minWidth;

            if (newLeftWidth < minWidth) newLeftWidth = minWidth;
            if (newLeftWidth > maxWidth) newLeftWidth = maxWidth;

            leftCol.style.flex = `0 0 ${newLeftWidth}px`;
            rightCol.style.flex = '1 1 0%';
        };

        const onMouseUpH = () => {
            if (!isDraggingH) return;
            isDraggingH = false;
            splitterH.classList.remove('dragging');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';

            document.removeEventListener('mousemove', onMouseMoveH);
            document.removeEventListener('mouseup', onMouseUpH);
            document.removeEventListener('touchmove', onMouseMoveH);
            document.removeEventListener('touchend', onMouseUpH);
        };

        splitterH.addEventListener('mousedown', onMouseDownH);
        splitterH.addEventListener('touchstart', onMouseDownH, { passive: true });
    }

    // Initial Load & Intervals
    fetchStatus();
    loadHistory();
    setInterval(fetchStatus, 3000);
});
