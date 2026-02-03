const API_BASE = "";
let currentTab = 'NORMAL';

// Status Polling
setInterval(updateStatus, 2000);
updateStatus();
loadRefs(currentTab);

async function updateStatus() {
    // 1. Detection State
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
            // How to display image? Does API serve refs?
            // Actually API endpoints are for listing/adding/deleting.
            // We need a way to VIEW them. 
            // We didn't add a static mount for refs. 
            // Let's implement dynamic image fetching or assume they are public? 
            // They are likely in /tmp or /var/lib.
            // We need an endpoint to serve the image content. 
            // Wait, we can modify api.py to serve content, or maybe add a GET endpoint for image content? 
            // For now let's assume we can fetch via a generic endpoint or base64. 
            // Actually we don't have an endpoint to serve the image binary! 
            // I should add one in the API refactoring. 
            // Let's assume there is /api/refs/{state}/{filename}/view
            
            // Wait, implementation plan said "GET /api/refs/{state}" lists files. 
            // It didn't explicitly specify serving them. 
            // I'll add a view endpoint? Or maybe just rely on users copying them? 
            // No, UI needs to show them.
            
            const div = document.createElement('div');
            div.className = 'ref-item';
            div.innerHTML = `
                <img src="${API_BASE}/api/refs/${state}/${file}/image" alt="${file}">
                <div class="ref-actions">
                    <button class="btn-stop" onclick="deleteRef('${state}', '${file}')">Delete</button>
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
