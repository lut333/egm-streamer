const API_BASE = "";
let currentTab = 'NORMAL';

// Status Polling
setInterval(updateStatus, 2000);
updateStatus();
initTabs();

async function initTabs() {
    try {
        const res = await fetch(`${API_BASE}/api/config/states`);
        const data = await res.json();
        const states = data.states || [];
        
        if (states.length > 0) {
            const tabsContainer = document.querySelector('.tabs');
            if (tabsContainer) {
                tabsContainer.innerHTML = ''; // Clear hardcoded tabs
                
                states.forEach((state, index) => {
                    const btn = document.createElement('button');
                    btn.className = 'tab';
                    btn.textContent = state.charAt(0).toUpperCase() + state.slice(1).toLowerCase(); // Capitalize
                    btn.onclick = () => switchTab(state);
                    tabsContainer.appendChild(btn);
                    
                    if (index === 0) {
                        // Default to first tab
                        currentTab = state;
                        btn.classList.add('active');
                    }
                });
                
                // Load refs for the initial tab
                loadRefs(currentTab);
                
                // Update capture text
                if(window.switchTab) { 
                     // Manually trigger the side effect of updating capture text
                     document.getElementById('capture-target-name').innerText = currentTab;
                }
            }
        }
    } catch (e) {
        console.error("Failed to load states:", e);
        // Fallback or leave hardcoded tabs as backup
        loadRefs(currentTab);
    }
}

// Live Preview (Detector)
const liveImg = document.getElementById('live-img');
const autoRefreshLive = document.getElementById('auto-refresh-live');
const matchTableBody = document.querySelector('#match-table tbody');

// Snapshot Preview (Service)
const snapshotImg = document.getElementById('snapshot-img');
const snapshotLink = document.getElementById('snapshot-link');
const autoRefreshSnapshot = document.getElementById('auto-refresh-snapshot');

setInterval(() => {
    // Only fetch if elements exist (in case of partial page load)
    if(autoRefreshLive && autoRefreshLive.checked) refreshLivePreview();
}, 1000);

setInterval(() => {
    if(autoRefreshSnapshot && autoRefreshSnapshot.checked) refreshSnapshot();
}, 2000);

function refreshSnapshot() {
    const ts = Date.now();
    const url = '/api/snapshot/latest?t=' + ts;
    snapshotImg.src = url;
    snapshotImg.style.display = 'block';
    // Update link to force reload in new tab too so browser doesn't cache old version
    snapshotLink.href = url;
}

async function refreshLivePreview() {
    // 1. Get Image
    const url = '/api/live/frame?t=' + Date.now();
    liveImg.src = url;
    liveImg.style.display = 'block';

// Logic moved to updateStatus
}

async function updateStatus() {
    // 1. Detection State & Matches
    try {
        const res = await fetch(`${API_BASE}/api/state`);
        const data = await res.json();
        const el = document.getElementById('current-state');
        if (data.error) {
            el.textContent = "OFFLINE";
            el.className = "state-other";
        } else {
            el.textContent = data.state || "UNKNOWN";
            el.className = `state-${(data.state || '').toLowerCase()}`;
            
            // Render Match Table
            if (data.matches) {
                let html = '';
                const sortedStates = Object.keys(data.matches).sort();
                
                for (const state of sortedStates) {
                    const info = data.matches[state];
                    const isMatch = info.is_match;
                    const dist = info.avg_distance;
                    
                    let distColor = '#aaa';
                    if (dist < 15) distColor = '#4caf50';
                    else if (dist < 25) distColor = '#f1c40f';
                    else distColor = '#e74c3c';
                    
                    html += `
                        <tr>
                            <td style="padding: 5px; font-weight: bold; color: #eee;">${state}</td>
                            <td style="padding: 5px; color: ${distColor}; font-family: monospace;">${dist < 0 ? 'No Refs' : dist.toFixed(2)}</td>
                            <td style="padding: 5px;">
                                ${isMatch 
                                    ? '<span style="color:#2ecc71; font-weight:bold;">✔ MATCH</span>' 
                                    : '<span style="color:#555;">-</span>'}
                            </td>
                        </tr>
                    `;
                }
                if (matchTableBody) matchTableBody.innerHTML = html;
            }
        }
    } catch (e) { console.error(e); }

    // 2. Streams
    try {
        const res = await fetch(`${API_BASE}/api/streams`);
        const streams = await res.json();
        renderStreams(streams);
    } catch (e) { console.error(e); }
}

function renderStreams(streams) {
    const grid = document.getElementById('stream-grid');
    grid.innerHTML = '';
    
    for (const [name, status] of Object.entries(streams)) {
        const div = document.createElement('div');
        div.className = 'card';
        div.innerHTML = `
            <h3>${name.toUpperCase()}</h3>
            <div>Status: <span class="status-badge ${status.running ? 'status-running':'status-stopped'}">
                ${status.running ? 'RUNNING' : 'STOPPED'}
            </span></div>
            <div style="margin: 10px 0; font-family: monospace; color: #aaa;">
                FPS: ${status.fps.toFixed(1)}<br>
                Bitrate: ${status.bitrate}<br>
                Speed: ${status.speed}<br>
                Frames: ${status.frame}<br>
                Drop: ${status.drop_frames || 0}<br>
                Dup: ${status.dup_frames || 0}<br>
                PID: ${status.pid || '-'}
            </div>
            <div class="controls">
                <button class="btn-start" onclick="controlStream('${name}', 'start')">Start</button>
                <button class="btn-stop" onclick="controlStream('${name}', 'stop')">Stop</button>
                <button class="btn-restart" onclick="controlStream('${name}', 'restart')">Restart</button>
            </div>
        `;
        grid.appendChild(div);
    }
}

async function controlStream(name, action) {
    await fetch(`${API_BASE}/api/streams/${name}/control`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action})
    });
    // Instant update
    setTimeout(updateStatus, 500); 
}

// Refs Manager
function switchTab(state) {
    currentTab = state;
    document.querySelectorAll('.tab').forEach(b => 
        b.classList.toggle('active', b.innerText.toUpperCase() === state)
    );
    loadRefs(state);
}

async function loadRefs(state) {
    const gallery = document.getElementById('ref-gallery');
    gallery.innerHTML = 'Loading...';
    
    try {
        const res = await fetch(`${API_BASE}/api/refs/${state}`);
        if (!res.ok) throw new Error(res.statusText);
        const files = await res.json();
        
        gallery.innerHTML = '';
        if (files.length === 0) {
            gallery.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 20px; color: #666;">No references yet</div>';
            return;
        }

        files.forEach(file => {
            const imgUrl = `${API_BASE}/api/refs/${state}/${file}/image`;
            const div = document.createElement('div');
            div.className = 'ref-item';
            div.innerHTML = `
                <a href="${imgUrl}" target="_blank" style="display:block; width:100%; height:100%;">
                    <img src="${imgUrl}" alt="${file}">
                </a>
                <div class="ref-actions">
                    <button class="btn-stop" onclick="event.preventDefault(); deleteRef('${state}', '${file}')">Delete</button>
                </div>
            `;
            gallery.appendChild(div);
        });
    } catch (e) {
        gallery.innerHTML = `Error: ${e.message}`;
    }
}

async function addReference(state) {
    if (!confirm(`Take current snapshot and save as [${state}] reference?`)) return;
    try {
        const res = await fetch(`${API_BASE}/api/refs/${state}`, { method: 'POST' });
        if (res.ok) {
            alert('Saved!');
            loadRefs(state);
        } else {
            const data = await res.json();
            alert('Error: ' + data.detail);
        }
    } catch (e) { alert('Error: ' + e); }
}

async function deleteRef(state, file) {
    if (!confirm(`Delete ${file}?`)) return;
    try {
        await fetch(`${API_BASE}/api/refs/${state}/${file}`, { method: 'DELETE' });
        loadRefs(state);
    } catch (e) { alert('Error: ' + e); }
}
