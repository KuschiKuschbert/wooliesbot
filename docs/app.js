document.addEventListener('DOMContentLoaded', () => {
    feather.replace();
    initDashboard();
});

let _data = [];
let _history = {};
let _volatility = {}; // item -> score
let _lastChecked = null;
let _currentFilter = 'all';
let _currentCatFilter = 'all';
let _searchText = '';
let _currentTab = 'deals';
let _shopMode = localStorage.getItem('shopMode') || 'weekly';
let _shoppingList = JSON.parse(localStorage.getItem('shoppingList') || '[]');
let _selectedItemForModal = null;
let _currentPage = 1;
const _itemsPerPage = 12;
let _currentSort = 'discount';
let _apiUrl = localStorage.getItem('bridge_url') || 'http://localhost:5001';
let _nextRun = null;
const MONTHLY_BUDGET = 800;


async function initDashboard() {
    try {
        const dataRes = await fetch('data.json').catch(() => null);

        if (dataRes && dataRes.ok) {
            const parsed = await dataRes.json();
            if (Array.isArray(parsed)) {
                _data = parsed;
            } else {
                _data = parsed.items || [];
                const luEl = document.getElementById('last-updated');
                if (luEl && parsed.last_updated) {
                    _lastChecked = parsed.last_updated;
                    updateLastCheckedDisplay();
                }
            }
        }
        
        setInterval(monitorApi, 30000); // Check local bridge status every 30s
        setInterval(monitorCloudHealth, 300000); // Check cloud health every 5 mins
        monitorApi(); // Initial check
        monitorCloudHealth(); // Initial check

        // Build _history from inline scrape_history (single source of truth)
        _data.forEach(item => {
            if (item.scrape_history && item.scrape_history.length > 0) {
                _history[item.name] = { target: item.target, history: item.scrape_history };
            }
        });

        setupFilters();
        renderDashboard();
    } catch (e) {
        console.error("Failed to initialize dashboard:", e);
        document.getElementById('specials-grid').innerHTML = '<p style="color: #ef4444;">Error loading data.</p>';
    }
}

function setupFilters() {
    // Tab switching
    const navLinks = document.querySelectorAll('.nav-link');
    const mobileNavLinks = document.querySelectorAll('.mobile-nav-link[data-tab]');
    
    const switchTab = (target) => {
        _currentTab = target;
        
        // Sync desktop buttons
        navLinks.forEach(l => l.classList.toggle('active', l.dataset.tab === target));
        
        // Sync mobile buttons
        mobileNavLinks.forEach(l => l.classList.toggle('active', l.dataset.tab === target));
        
        // Switch content
        document.querySelectorAll('.tab-content').forEach(tab => {
            tab.classList.toggle('active', tab.id === `tab-${target}`);
        });
        
        window.scrollTo({ top: 0, behavior: 'smooth' });
        
        if (target === 'analytics') renderAnalytics();
        else renderDashboard();
    };

    navLinks.forEach(link => {
        link.addEventListener('click', () => switchTab(link.dataset.tab));
    });

    mobileNavLinks.forEach(link => {
        link.addEventListener('click', () => switchTab(link.dataset.tab));
    });

    document.getElementById('mobile-refresh-btn')?.addEventListener('click', () => {
        const btn = document.getElementById('mobile-refresh-btn');
        btn.classList.add('spin');
        initDashboard().finally(() => {
            setTimeout(() => btn.classList.remove('spin'), 1000);
        });
    });

    const storeButtons = document.querySelectorAll('.filter-btn');
    storeButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            storeButtons.forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            _currentFilter = e.target.dataset.filter;
            _currentPage = 1;
            renderDashboard();
        });
    });

    const catButtons = document.querySelectorAll('.filter-btn-cat');
    catButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            catButtons.forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            _currentCatFilter = e.target.dataset.cat;
            _currentPage = 1;
            renderDashboard();
        });
    });

    // Shop Mode Toggle
    const modeLabels = document.querySelectorAll('.mode-label');
    modeLabels.forEach(label => {
        label.addEventListener('click', () => {
            _shopMode = label.dataset.mode;
            localStorage.setItem('shopMode', _shopMode);
            modeLabels.forEach(l => l.classList.remove('active'));
            label.classList.add('active');
            renderDashboard();
        });
    });

    // Drawer toggles
    document.getElementById('toggle-list-btn')?.addEventListener('click', toggleDrawer);
    document.getElementById('mobile-toggle-list')?.addEventListener('click', toggleDrawer);
    document.getElementById('close-drawer')?.addEventListener('click', toggleDrawer);
    document.getElementById('drawer-overlay')?.addEventListener('click', toggleDrawer);

    // Search
    document.getElementById('dashboard-search')?.addEventListener('input', (e) => {
        _searchText = e.target.value.toLowerCase();
        _currentPage = 1;
        renderDashboard();
    });

    // Modals
    document.getElementById('modal-cancel')?.addEventListener('click', closeModal);
    document.getElementById('modal-save')?.addEventListener('click', saveItemChanges);
    
    const stockBtns = document.querySelectorAll('.stock-btn');
    stockBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            stockBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        });
    });

    // Sorting
    document.getElementById('sort-select')?.addEventListener('change', (e) => {
        _currentSort = e.target.value;
        _currentPage = 1; // Reset to page 1
        renderSpecials();
    });

    // Clear List
    document.getElementById('clear-list-btn')?.addEventListener('click', () => {
        if (confirm("Clear your entire shopping list?")) {
            _shoppingList = [];
            localStorage.setItem('shoppingList', '[]');
            renderShoppingList();
            updateListCount();
        }
    });

    // Sync Keep
    document.getElementById('sync-keep-btn')?.addEventListener('click', async () => {
        const btn = document.getElementById('sync-keep-btn');
        try {
            btn.innerHTML = '<i data-feather="loader" class=""></i> Starting Sync...';
            feather.replace();
            const response = await fetch(`${_apiUrl}/sync`);
            if (response.ok) {
                btn.innerHTML = '<i data-feather="check"></i> Syncing...';
                setTimeout(() => { btn.innerHTML = '<i data-feather="refresh-cw"></i> Sync to Google Keep'; feather.replace(); }, 3000);
            }
        } catch (e) {
            alert(`Bridge not running at ${_apiUrl}. Update settings if using a tunnel or IP.`);
        }
    });
    
    // Settings Logic
    document.getElementById('settings-btn')?.addEventListener('click', openSettings);
    document.getElementById('settings-cancel')?.addEventListener('click', closeSettings);
    document.getElementById('settings-save')?.addEventListener('click', saveSettings);
}

function openSettings() {
    document.getElementById('bridge-url-input').value = _apiUrl;
    document.getElementById('settings-modal').style.display = 'flex';
}

function closeSettings() {
    document.getElementById('settings-modal').style.display = 'none';
}

function saveSettings() {
    const val = document.getElementById('bridge-url-input').value.trim();
    if (val) {
        _apiUrl = val;
        localStorage.setItem('bridge_url', _apiUrl);
    }
    closeSettings();
    monitorApi();
}

function toggleDrawer() {
    document.getElementById('list-drawer').classList.toggle('open');
    document.getElementById('drawer-overlay').classList.toggle('open');
    if (document.getElementById('list-drawer').classList.contains('open')) renderShoppingList();
}


function renderDashboard() {
    renderCountdown();
    renderStats();
    renderEssentials();
    renderPredictions(); // New Section
    renderNearMisses();
    renderSpecials();
    renderAllItems();
    updateListCount();
}


function formatPrice(item) {
    const effPrice = item.eff_price || item.price;
    if (item.price_mode === 'kg') return `$${effPrice.toFixed(2)}/kg`;
    if (item.price_mode === 'litre') return `$${item.price.toFixed(2)} ($${effPrice.toFixed(2)}/L)`;
    return `$${item.price.toFixed(2)}`;
}

function renderCountdown() {
    const textEl = document.getElementById('countdown-text');
    const pill = document.getElementById('specials-countdown');
    
    // Specials reset every Wed (Woolies/Coles updates on Wed morning).
    // Tuesday is the last day.
    const now = new Date();
    const day = now.getDay(); // 0=Sun, 2=Tue, 3=Wed
    
    let daysLeft = (3 - day + 7) % 7;
    if (daysLeft === 0) daysLeft = 7; // It reset today
    
    if (daysLeft <= 1) {
        pill.classList.add('urgent');
        textEl.textContent = daysLeft === 1 ? "Ends TOMORROW (Tue)" : "Ends TODAY (Tue)";
    } else {
        pill.classList.remove('urgent');
        textEl.textContent = `Specials end in ${daysLeft} days`;
    }
}

function renderStats() {
    const totalItemsEl = document.getElementById('total-items');
    const totalSpecialsEl = document.getElementById('total-specials');
    const cartTotalEl = document.getElementById('cart-total');
    
    if (totalItemsEl) totalItemsEl.textContent = _data.length;
    
    let specialsCount = 0;
    let savingsToday = 0;
    
    _data.forEach(item => {
        const effPrice = item.eff_price || item.price;
        const isSpecial = item.on_special || (item.target > 0 && effPrice <= item.target && !item.price_unavailable);
        if (isSpecial) {
            specialsCount++;
            // Use was_price for savings when available (more accurate than target)
            const ref = item.was_price || item.target || 0;
            savingsToday += Math.max(0, ref - effPrice);
        }
    });

    if (totalSpecialsEl) totalSpecialsEl.textContent = specialsCount;
    if (cartTotalEl) cartTotalEl.textContent = `$${savingsToday.toFixed(2)}`;

    // Monthly Budget Tracker
    let monthlySpent = 0;
    const now = new Date();
    const currentMonth = now.getMonth();
    const currentYear = now.getFullYear();
    
    _data.forEach(item => {
        // Preference 1: Detailed price history (captures multiple purchases)
        if (item.price_history && item.price_history.length > 0) {
            item.price_history.forEach(h => {
                const d = new Date(h.date);
                if (d.getMonth() === currentMonth && d.getFullYear() === currentYear) {
                    monthlySpent += h.price;
                }
            });
        } 
        // Preference 2: Fallback to last_purchased (legacy or single-entry items)
        else if (item.last_purchased) {
            const d = new Date(item.last_purchased);
            if (d.getMonth() === currentMonth && d.getFullYear() === currentYear) {
                monthlySpent += (item.eff_price || item.price || 0);
            }
        }
    });

    const budgetProgress = document.getElementById('budget-progress');
    const budgetText = document.getElementById('budget-spent-text');
    const percent = Math.min((monthlySpent / MONTHLY_BUDGET) * 100, 100);
    
    budgetText.textContent = `$${monthlySpent.toFixed(0)} / $${MONTHLY_BUDGET}`;
    budgetProgress.style.width = `${percent}%`;
    budgetProgress.style.background = monthlySpent > MONTHLY_BUDGET ? 'var(--coles-red)' : 'var(--woolies-green)';

    renderColaBattle();
}


const ESSENTIALS = ["Capsicum", "Onions", "Spinach", "Eggs", "Cream", "Cheese", "Avocado", "Zucchini"];

function renderEssentials() {
    const list = document.getElementById('essentials-list');
    list.innerHTML = '';
    
    const checkedItems = JSON.parse(localStorage.getItem('essentialsChecked') || '[]');

    ESSENTIALS.forEach(item => {
        const div = document.createElement('label');
        div.className = 'essential-item';
        const isChecked = checkedItems.includes(item);
        
        div.innerHTML = `
            <input type="checkbox" ${isChecked ? 'checked' : ''}>
            <span class="${isChecked ? 'checked' : ''}">${item}</span>
        `;
        
        div.querySelector('input').addEventListener('change', (e) => {
            let current = JSON.parse(localStorage.getItem('essentialsChecked') || '[]');
            if (e.target.checked) {
                current.push(item);
            } else {
                current = current.filter(i => i !== item);
            }
            localStorage.setItem('essentialsChecked', JSON.stringify(current));
            renderEssentials();
        });
        
        list.appendChild(div);
    });
}

function renderNearMisses() {
    const section = document.getElementById('near-misses-section');
    const grid = document.getElementById('near-misses-grid');
    grid.innerHTML = '';
    
    const nearMisses = _data.filter(item => {
        const effPrice = item.eff_price || item.price;
        const target = item.target || 0;
        if (target <= 0 || item.price_unavailable || item.on_special) return false;
        // Near miss = within 5% above target
        return effPrice > target && effPrice <= target * 1.05;
    }).sort((a, b) => {
        // Sort by closest to target first
        const ra = (a.eff_price || a.price) / a.target;
        const rb = (b.eff_price || b.price) / b.target;
        return ra - rb;
    });
    
    if (nearMisses.length > 0) {
        section.classList.remove('hidden');
        nearMisses.slice(0, 6).forEach((item, index) => {
            const card = createItemCard(item, index, 'near');
            grid.appendChild(card);
        });
    } else {
        section.classList.add('hidden');
    }
}

function getConfidenceBadge(item) {
    const conf = item.target_confidence;
    const pts = item.target_data_points || 0;
    const method = item.target_method || '';
    if (!conf || conf === 'high') {
        // Treat missing metadata as high if it's a manually-configured item (no target_method)
        const isHigh = conf === 'high';
        const icon = isHigh ? '🟢' : '';
        if (!conf) return ''; // no badge for old items with no metadata yet
        return `<span class="confidence-badge high" title="High confidence: ${method} (${pts} data points)">🟢 High</span>`;
    }
    if (conf === 'medium') {
        return `<span class="confidence-badge medium" title="Medium confidence: ${method} (${pts} data points)">🟡 Med</span>`;
    }
    return `<span class="confidence-badge low" title="Low confidence: ${method} — buy more to improve!">🔴 Low</span>`;
}

function createItemCard(item, index, type = 'special') {
    const effPrice = item.eff_price || item.price;
    const isSpecial = type === 'special' && effPrice <= item.target && !item.price_unavailable;
    const isNearMiss = type === 'near';
    const isPredicted = type === 'predicted';
    
    const card = document.createElement('div');
    const storeClass = item.store || 'woolworths';
    
    card.className = `item-card store-${storeClass} ${isNearMiss ? 'near-miss-card' : ''} ${isPredicted ? 'predicted-card' : ''}`;
    
    let imgSrc = item.local_image || item.image_url;
    let imgHtml = imgSrc 
        ? `<img src="${imgSrc}" class="item-image" loading="lazy" onerror="this.style.display='none'">`
        : `<div class="product-img-placeholder"><i data-feather="image"></i></div>`;

    const stockColor = item.stock === 'low' ? 'low' : (item.stock === 'medium' ? 'medium' : 'full');
    const confidenceBadge = getConfidenceBadge(item);
    const targetTooltip = item.target_method 
        ? `title="${item.target_method}${item.target_data_points ? ` (${item.target_data_points} data points)` : ''}"` 
        : '';

    // Was/Now pricing for store-confirmed specials
    let priceHtml;
    if (item.on_special && item.was_price && item.was_price > effPrice) {
        const savePct = Math.round((1 - effPrice / item.was_price) * 100);
        priceHtml = `
            <span class="item-price">$${effPrice.toFixed(2)}</span>
            <span class="was-price">Was $${item.was_price.toFixed(2)}</span>
            <span class="save-badge">Save ${savePct}%</span>
        `;
    } else if (item.price_unavailable) {
        priceHtml = `<span class="item-price">❓</span>`;
    } else {
        priceHtml = `<span class="item-price">$${effPrice.toFixed(2)}</span>`;
    }
    
    card.innerHTML = `
        ${imgHtml}
        <div class="item-content">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div class="store-badge ${storeClass}">${storeClass === 'woolworths' ? 'Woolies' : 'Coles'}</div>
                <div style="display:flex; gap:4px; align-items:center;">
                    ${confidenceBadge}
                    <div class="stock-dot ${stockColor}" title="Stock: ${item.stock}"></div>
                </div>
            </div>
            <h3 class="item-title" style="margin-top: 8px;">${item.name}</h3>
            <div class="item-price-row">
                ${priceHtml}
                <span class="item-target" ${targetTooltip}>Target: $${(item.target || 0).toFixed(2)}</span>
            </div>
            <button class="add-to-list-btn" onclick="addToList('${item.name.replace(/'/g, "\\'")}')">
                <i data-feather="plus"></i> Add to List
            </button>
            <div class="chart-container-sm" id="chart-${type}-${index}">
                <canvas></canvas>
            </div>
        </div>
    `;
    
    setTimeout(() => {
        if (_history[item.name] && _history[item.name].history.length > 0) {
            renderSparkline(`chart-${type}-${index}`, _history[item.name].history, storeClass);
        }
        feather.replace();
    }, 0);
    
    return card;
}

function renderPredictions() {
    const section = document.getElementById('predictions-section');
    const grid = document.getElementById('predictions-grid');
    if (!grid) return;
    grid.innerHTML = '';
    
    const now = new Date();

    const predicted = _data.filter(item => {
        // Condition 1: Explicitly low stock (Highest priority)
        if (item.stock === 'low') return true;
        
        // Condition 2: Buy frequency prediction based on category
        if (item.last_purchased) {
            const last = new Date(item.last_purchased);
            const diffDays = (now - last) / (1000 * 60 * 60 * 24);
            
            // Per-category heuristics
            let threshold = 10; // Default
            if (item.type === 'fresh_protein' || item.type === 'fresh_veg') threshold = 4;
            else if (item.type === 'fresh_fridge') threshold = 6;
            else if (item.type === 'pet' || item.type === 'household') threshold = 14;
            
            if (diffDays >= threshold) return true;
        }

        // Condition 3: "Stock Up Alert" - Medium stock but currently on a deep special
        const effPrice = item.eff_price || item.price;
        const isOnSpecial = effPrice <= item.target && !item.price_unavailable;
        if (item.stock === 'medium' && isOnSpecial) return true;

        return false;
    });

    // Sort: Low stock first, then deep specials, then frequency
    predicted.sort((a, b) => {
        if (a.stock === 'low' && b.stock !== 'low') return -1;
        if (b.stock === 'low' && a.stock !== 'low') return 1;
        
        const priceA = a.eff_price || a.price;
        const priceB = b.eff_price || b.price;
        const discountA = (a.target - priceA) / a.target;
        const discountB = (b.target - priceB) / b.target;
        
        if (discountA > discountB) return -1;
        if (discountB > discountA) return 1;
        
        return 0;
    });

    if (predicted.length > 0) {
        section.classList.remove('hidden');
        // Show up to 10 to fill up to 2 rows of 5
        predicted.slice(0, 10).forEach((item, idx) => {
            grid.appendChild(createItemCard(item, idx, 'predicted'));
        });
    } else {
        section.classList.add('hidden');
    }
}


function updateListCount() {
    const el = document.getElementById('list-count');
    if (el) el.textContent = _shoppingList.length;
    
    // Update mobile badge
    const mobileEl = document.getElementById('mobile-list-count');
    if (mobileEl) mobileEl.textContent = _shoppingList.length;
    
    const syncBtn = document.getElementById('sync-keep-btn');
    if (syncBtn) syncBtn.disabled = _shoppingList.length === 0;
}

function addToList(itemName) {
    const item = _data.find(i => i.name === itemName);
    if (!item) return;
    
    // Factor in quantities
    let qty = 1;
    if (_shopMode === 'big') {
        if (item.type === 'fresh_protein') qty = 4;
        else if (['pet', 'pantry', 'household', 'freezer'].includes(item.type)) qty = 2;
    }

    const listItem = {
        name: item.name,
        price: item.price,
        qty: qty,
        store: item.store || 'woolworths',
        image: item.image_url
    };
    
    _shoppingList.push(listItem);
    localStorage.setItem('shoppingList', JSON.stringify(_shoppingList));
    updateListCount();
    
    // Visual feedback
    const btn = event?.currentTarget;
    if (btn) {
        const originalText = btn.innerHTML;
        btn.innerHTML = '<i data-feather="check"></i> Added!';
        btn.style.background = 'var(--woolies-green)';
        feather.replace();
        setTimeout(() => {
            btn.innerHTML = originalText;
            btn.style.background = '';
            feather.replace();
        }, 1500);
    }
}

function removeFromList(index) {
    _shoppingList.splice(index, 1);
    localStorage.setItem('shoppingList', JSON.stringify(_shoppingList));
    updateListCount();
    renderShoppingList();
}

function renderShoppingList() {
    const container = document.getElementById('shopping-list-items');
    const totalEl = document.getElementById('list-total-price');
    if (!container) return;
    
    container.innerHTML = '';
    let total = 0;
    
    _shoppingList.forEach((item, index) => {
        const itemTotal = item.price * item.qty;
        total += itemTotal;
        
        const div = document.createElement('div');
        div.className = 'shopping-item';
        div.innerHTML = `
            ${item.image ? `<img src="${item.image}">` : '<div style="width:40px;height:40px;background:rgba(255,255,255,0.05);border-radius:8px;display:flex;align-items:center;justify-content:center;"><i data-feather="image" style="width:16px;"></i></div>'}
            <div class="shopping-item-info">
                <div class="shopping-item-name">${item.qty}× ${item.name}</div>
                <div class="shopping-item-price">${item.store === 'woolworths' ? '🟢W' : '🔴C'} — $${itemTotal.toFixed(2)}</div>
            </div>
            <button class="icon-btn" onclick="removeFromList(${index})"><i data-feather="trash-2"></i></button>
        `;
        container.appendChild(div);
    });
    
    if (_shoppingList.length === 0) {
        container.innerHTML = '<p style="color:var(--text-muted);text-align:center;margin-top:40px;">Your list is empty.</p>';
    }
    
    totalEl.textContent = `$${total.toFixed(2)}`;
    if (typeof feather !== 'undefined') feather.replace();
}

function updateLastCheckedDisplay() {
    const el = document.getElementById('last-updated');
    const nextEl = document.getElementById('next-update');
    if (!el || !_lastChecked) return;
    
    const lastDate = new Date(_lastChecked);
    if (isNaN(lastDate.getTime())) {
        el.textContent = _lastChecked;
        return;
    }
    
    // 1. Update Relative "Last Checked"
    const now = new Date();
    const diffMins = Math.floor((now - lastDate) / 60000);
    
    if (diffMins < 1) el.textContent = "Just now";
    else if (diffMins < 60) el.textContent = `${diffMins}m ago`;
    else {
        const hours = Math.floor(diffMins / 60);
        el.textContent = `${hours}h ${diffMins % 60}m ago`;
    }

    // 2. Update Local Time "Next Check"
    if (nextEl) {
        let nextDate;
        if (_nextRun) {
            nextDate = new Date(_nextRun);
        } else {
            // Fallback: Assume 60 min interval from last success
            nextDate = new Date(lastDate.getTime() + (60 * 60 * 1000));
        }
        
        if (!isNaN(nextDate.getTime())) {
            const options = { hour: 'numeric', minute: '2-digit', hour12: true };
            nextEl.textContent = nextDate.toLocaleTimeString(undefined, options);
        }
    }

    // 3. Refresh feather icons for the new structure
    if (typeof feather !== 'undefined') feather.replace();
}

async function monitorApi() {
    const dot = document.getElementById('api-status-dot');
    const text = document.getElementById('api-status-text');
    if (!dot || !text) return;

    try {
        // Detect HTTPS Mixed Content block
        if (window.location.protocol === 'https:' && _apiUrl.startsWith('http://localhost')) {
            dot.className = 'status-dot offline';
            text.textContent = 'HTTPS Blocked';
            return;
        }

        const res = await fetch(`${_apiUrl}/status`).catch(() => null);
        if (res && res.ok) {
            dot.className = 'status-dot online';
            text.textContent = 'Bridge Online';
        } else {
            dot.className = 'status-dot offline';
            text.textContent = 'Bridge Offline';
        }
    } catch {
        dot.className = 'status-dot offline';
        text.textContent = 'Bridge Offline';
    }
}

async function monitorCloudHealth() {
    const dot = document.getElementById('cloud-status-dot');
    const text = document.getElementById('cloud-status-text');
    if (!dot || !text) return;

    try {
        // Fetch heartbeat from the same origin (GitHub Pages)
        const res = await fetch('heartbeat.json?t=' + Date.now()).catch(() => null);
        if (res && res.ok) {
            const data = await res.json();
            const lastBeat = new Date(data.last_heartbeat);
            const now = new Date();
            const minsAgo = (now - lastBeat) / (1000 * 60);

            if (minsAgo < 35) {
                dot.className = 'status-dot cloud-dot active';
                text.textContent = 'Global Bot: Active';
            } else {
                dot.className = 'status-dot cloud-dot silent';
                text.textContent = `Global Bot: Silent (${Math.round(minsAgo)}m ago)`;
            }

            // Sync the 'Last Checked' header display with the cloud heartbeat
            _lastChecked = data.last_heartbeat;
            _nextRun = data.next_run;
            updateLastCheckedDisplay();
        }
    } catch (e) {
        dot.className = 'status-dot cloud-dot silent';
        text.textContent = 'Global Bot: Unknown';
    }
}

function getItemUnitPrice(item) {
    if (item.eff_price && item.eff_price > 0 && item.price_mode === 'litre') return item.eff_price;
    if (item.price === 0) return Infinity;

    // Smart parsing for items that don't have an effective per-litre price yet
    const name = item.name.toLowerCase();
    let volume = 0;

    // Match patterns like "1.25L", "2L", "600ml"
    const lMatch = name.match(/(\d+\.?\d*)l/);
    const mlMatch = name.match(/(\d+)ml/);
    
    // Match multipacks like "375ml x 30" or "30pk"
    const pkMatch = name.match(/(\d+)pk/);
    const multiMatch = name.match(/(\d+)x(\d+)/);

    if (lMatch) volume = parseFloat(lMatch[1]);
    else if (mlMatch) volume = parseFloat(mlMatch[1]) / 1000;
    
    if (multiMatch) {
        // "30x375" scenario
        const count = parseInt(multiMatch[1]);
        const size = parseInt(multiMatch[2]);
        volume = (count * size) / 1000;
    } else if (pkMatch && volume > 0) {
        // "375ml 30pk" scenario
        volume *= parseInt(pkMatch[1]);
    }

    if (volume > 0) return item.price / volume;
    return item.price; // Fallback to unit price if volume can't be parsed
}

function renderColaBattle() {
    const colaItems = _data.filter(i => (i.compare_group === 'cola' || i.name.toLowerCase().includes('pepsi') || i.name.toLowerCase().includes('coke')) && !i.price_unavailable);
    const container = document.getElementById('cola-battle-container') || document.querySelector('.cola-card');
    if (!container) return;

    // Categorization
    const groups = {
        noSugar: { pepsi: null, coke: null },
        classic: { pepsi: null, coke: null }
    };

    colaItems.forEach(item => {
        const name = item.name.toLowerCase();
        const price = getItemUnitPrice(item);
        if (price === Infinity || price === 0) return;

        const isNoSugar = name.includes('max') || name.includes('zero') || name.includes('no sugar');
        const isPepsi = name.includes('pepsi');
        const isCoke = name.includes('coke') || name.includes('coca');

        const category = isNoSugar ? 'noSugar' : 'classic';
        const brand = isPepsi ? 'pepsi' : (isCoke ? 'coke' : null);

        if (brand && (!groups[category][brand] || price < groups[category][brand].unitPrice)) {
            groups[category][brand] = { ...item, unitPrice: price };
        }
    });

    // Sub-renderer for a battle row
    const renderBattleRow = (title, pepsi, coke) => {
        const pP = pepsi ? pepsi.unitPrice : Infinity;
        const cP = coke ? coke.unitPrice : Infinity;
        const pStore = pepsi ? (pepsi.store === 'woolworths' ? '🟢W' : '🔴C') : '';
        const cStore = coke ? (coke.store === 'woolworths' ? '🟢W' : '🔴C') : '';
        
        const pWinner = pP < cP;
        const cWinner = cP < pP;

        return `
            <div class="battle-arena">
                <div class="arena-title">${title}</div>
                <div class="arena-fighters">
                    <div class="fighter ${pWinner ? `winner winner-${pepsi.store}` : ''}">
                        ${pWinner ? `<div class="winner-badge">🏆 CHEAPEST</div>` : ''}
                        <div class="fighter-brand">Pepsi</div>
                        <div class="fighter-price">$${pP === Infinity ? '—' : pP.toFixed(2)}/L</div>
                        <div class="fighter-product">${pepsi ? pepsi.name : 'No Data'} ${pStore}</div>
                    </div>
                    <div class="battle-vs">VS</div>
                    <div class="fighter ${cWinner ? `winner winner-${coke.store}` : ''}">
                        ${cWinner ? `<div class="winner-badge">🏆 CHEAPEST</div>` : ''}
                        <div class="fighter-brand">Coke</div>
                        <div class="fighter-price">$${cP === Infinity ? '—' : cP.toFixed(2)}/L</div>
                        <div class="fighter-product">${coke ? coke.name : 'No Data'} ${cStore}</div>
                    </div>
                </div>
            </div>
        `;
    };

    container.innerHTML = `
        <div class="cola-battle-header">
            <i data-feather="zap"></i> Ultimate Cola Battle
        </div>
        ${renderBattleRow('No-Sugar Arena', groups.noSugar.pepsi, groups.noSugar.coke)}
        <div class="arena-divider"></div>
        ${renderBattleRow('Classic Arena', groups.classic.pepsi, groups.classic.coke)}
    `;
    feather.replace();
}

function renderSpecials() {
    const grid = document.getElementById('specials-grid');
    grid.innerHTML = '';

    const displayItems = _data.filter(item => {
        const matchesStore = _currentFilter === 'all' || item.store === _currentFilter;
        const matchesCat = _currentCatFilter === 'all' || item.type === _currentCatFilter;
        const matchesSearch = !_searchText || item.name.toLowerCase().includes(_searchText);
        const effPrice = item.eff_price || item.price;
        // Store says "on special" OR price is at/below our target
        const isSpecial = item.on_special || (item.target > 0 && effPrice <= item.target && !item.price_unavailable);
        return matchesStore && matchesCat && matchesSearch && isSpecial;
    });

    // Sorting Logic
    displayItems.sort((a, b) => {
        if (_currentSort === 'name') return a.name.localeCompare(b.name);
        
        const priceA = a.eff_price || a.price;
        const priceB = b.eff_price || b.price;
        
        if (_currentSort === 'price') return priceA - priceB;
        
        if (_currentSort === 'discount') {
            // Use was_price (store signal) if available, otherwise target
            const refA = a.was_price || a.target || priceA;
            const refB = b.was_price || b.target || priceB;
            const savingsA = (refA - priceA) / refA;
            const savingsB = (refB - priceB) / refB;
            return savingsB - savingsA; // Biggest discount first
        }
        return 0;
    });

    if (displayItems.length === 0) {
        grid.innerHTML = '<p style="color: var(--text-muted); grid-column: 1/-1;">No deep deals today.</p>';
        if (typeof renderPagination === 'function') renderPagination(0);
        return;
    }

    // Pagination Slicing
    const totalItems = displayItems.length;
    const startIndex = (_currentPage - 1) * _itemsPerPage;
    const endIndex = startIndex + _itemsPerPage;
    const pagedItems = displayItems.slice(startIndex, endIndex);

    pagedItems.forEach((item, index) => {
        const card = createItemCard(item, startIndex + index);
        grid.appendChild(card);
    });

    renderPagination(totalItems);

    if (typeof feather !== 'undefined') feather.replace();
}

function renderPagination(totalItems) {
    const container = document.getElementById('pagination-controls');
    if (!container) return;
    container.innerHTML = '';

    const totalPages = Math.ceil(totalItems / _itemsPerPage);
    if (totalPages <= 1) return;

    // Previous Button
    const prevBtn = document.createElement('button');
    prevBtn.className = 'pagination-btn';
    prevBtn.disabled = _currentPage === 1;
    prevBtn.innerHTML = '<i data-feather="chevron-left"></i> Prev';
    prevBtn.onclick = () => {
        _currentPage--;
        renderSpecials();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    };
    container.appendChild(prevBtn);

    // Page Info
    const info = document.createElement('span');
    info.className = 'pagination-info';
    info.textContent = `Page ${_currentPage} of ${totalPages}`;
    container.appendChild(info);

    // Next Button
    const nextBtn = document.createElement('button');
    nextBtn.className = 'pagination-btn';
    nextBtn.disabled = _currentPage === totalPages;
    nextBtn.innerHTML = 'Next <i data-feather="chevron-right"></i>';
    nextBtn.onclick = () => {
        _currentPage++;
        renderSpecials();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    };
    container.appendChild(nextBtn);

    if (typeof feather !== 'undefined') feather.replace();
}

function renderAllItems() {
    const tbody = document.getElementById('all-items-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    const filteredData = _data.filter(item => {
        const matchesStore = _currentFilter === 'all' || item.store === _currentFilter;
        const matchesSearch = !_searchText || item.name.toLowerCase().includes(_searchText);
        return matchesStore && matchesSearch;
    }).sort((a, b) => a.name.localeCompare(b.name));

    filteredData.forEach((item, index) => {
        const tr = document.createElement('tr');
        const effPrice = item.eff_price || item.price;
        const isSpecial = item.on_special || ((item.target || 0) > 0 && effPrice <= item.target && !item.price_unavailable);
        const stockColor = item.stock === 'low' ? 'low' : (item.stock === 'medium' ? 'medium' : 'full');
        
        let priceCell;
        if (item.on_special && item.was_price && item.was_price > effPrice) {
            const savePct = Math.round((1 - effPrice / item.was_price) * 100);
            priceCell = `$${effPrice.toFixed(2)} <span class="was-price">$${item.was_price.toFixed(2)}</span> <span class="save-badge">-${savePct}%</span>`;
        } else {
            priceCell = item.price_unavailable ? '❓' : `$${effPrice.toFixed(2)}`;
        }

        tr.innerHTML = `
            <td>
                <span style="font-weight:600;">${item.name}</span>
                ${isSpecial ? ' 🔥' : ''}
            </td>
            <td><span class="store-badge ${item.store}">${item.store === 'woolworths' ? 'W' : 'C'}</span></td>
            <td>
                <div class="stock-clickable" onclick="openStockModal('${item.name.replace(/'/g, "\\'")}')">
                    <div class="stock-dot ${stockColor}"></div> ${item.stock}
                </div>
            </td>
            <td>${priceCell}</td>
            <td>${(item.target || 0) > 0 ? '$' + item.target.toFixed(2) : '<span style="opacity:0.4">watching</span>'}</td>
            <td>
                <div class="chart-container-td" id="chart-td-${index}">
                    <canvas></canvas>
                </div>
            </td>
        `;
        tbody.appendChild(tr);

        if (_history[item.name] && _history[item.name].history.length > 0) {
            renderSparkline(`chart-td-${index}`, _history[item.name].history, item.store);
        }
    });
}

function openStockModal(itemName) {
    const item = _data.find(i => i.name === itemName);
    if (!item) return;
    
    _selectedItemForModal = item;
    document.getElementById('modal-title').textContent = item.name;
    document.getElementById('target-input-modal').value = item.target;
    
    document.querySelectorAll('.stock-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.level === item.stock);
    });
    
    document.getElementById('overlay-modal').style.display = 'flex';
}

function closeModal() {
    document.getElementById('overlay-modal').style.display = 'none';
}

async function saveItemChanges() {
    const activeStock = document.querySelector('.stock-btn.active')?.dataset.level;
    const newTarget = parseFloat(document.getElementById('target-input-modal').value);
    
    if (!_selectedItemForModal || !activeStock) return;
    
    try {
        const response = await fetch('http://localhost:5001/update_stock', {
            method: 'POST',
            body: JSON.stringify({
                name: _selectedItemForModal.name,
                stock: activeStock
            })
        });
        
        if (response.ok) {
            _selectedItemForModal.stock = activeStock;
            _selectedItemForModal.target = newTarget;
            renderDashboard();
            closeModal();
        }
    } catch (e) {
        alert("Bridge error.");
    }
}

function renderAnalytics() {
    // Collect data
    const categories = {};
    const priceIndexByMonth = {}; // YYYY-MM -> { sum: X, count: Y }
    let totalRealizedSavings = 0;
    let itemsBoughtAtTarget = 0;
    let totalItemsTracked = _data.length;

    // 1. Calculate Price Index and historical trends from scrape_history (inline)

    Object.entries(_history).forEach(([name, data]) => {
        const itemInfo = _data.find(i => i.name === name) || {};
        const target = itemInfo.target || data.target || 0;
        
        // Calculate Volatility
        const prices = data.history.map(h => h.price).filter(p => p > 0 && p < 1000);
        if (prices.length > 2) {
            const avg = prices.reduce((a, b) => a + b) / prices.length;
            const variance = prices.reduce((a, b) => a + Math.pow(b - avg, 2), 0) / prices.length;
            const stdDev = Math.sqrt(variance);
            _volatility[name] = (stdDev / avg) * 100; // Relative volatility
        }

        data.history.forEach(h => {
            if (h.price > 1000) return;
            
            // Track total realized savings over full history
            if (target > 0 && h.price <= target) {
                // Heuristic: If we bought it at or below target, we saved vs 'shelf' (approx 30% higher)
                const estimatedShelf = target * 1.4;
                totalRealizedSavings += Math.max(0, estimatedShelf - h.price);
                itemsBoughtAtTarget++;
            }

            const date = h.date.substring(0, 7); // YYYY-MM
            if (!priceIndexByMonth[date]) {
                priceIndexByMonth[date] = { sum: 0, count: 0 };
            }
            priceIndexByMonth[date].sum += h.price;
            priceIndexByMonth[date].count += 1;
        });
    });

    // 2. Calculate Category Split, Brand Split, and efficiency from current _data
    const brandPrices = { 'Private Label': { sum: 0, count: 0 }, 'Name Brand': { sum: 0, count: 0 } };
    _data.forEach(item => {
        const cat = item.subcategory || item.type || 'pantry';
        const price = item.eff_price || item.price || 0;
        if (price > 0 && price < 1000) {
            categories[cat] = (categories[cat] || 0) + price;
            
            const brandType = item.brand === 'Private Label' ? 'Private Label' : 'Name Brand';
            brandPrices[brandType].sum += price;
            brandPrices[brandType].count++;
        }

        if (item.last_purchased) {
            const isSpecial = price <= (item.target || 0);
            if (isSpecial) {
                itemsBoughtAtTarget++;
                totalRealizedSavings += Math.max(0, (item.target || 0) - price);
            }
        }
    });

    const efficiency = _data.filter(i => i.last_purchased).length > 0 
        ? (itemsBoughtAtTarget / _data.filter(i => i.last_purchased).length) * 100 
        : 0;
    
    document.getElementById('analytic-savings-val').textContent = `$${totalRealizedSavings.toFixed(0)}`;
    document.getElementById('analytic-efficiency-val').textContent = `${efficiency.toFixed(0)}%`;

    // Charts
    const spendingCtx = document.getElementById('spending-chart')?.getContext('2d');
    const categoryCtx = document.getElementById('category-chart')?.getContext('2d');

    if (spendingCtx) {
        const sortedDates = Object.keys(priceIndexByMonth).sort();
        const chartData = sortedDates.map(d => priceIndexByMonth[d].sum / priceIndexByMonth[d].count);
        
        // Destroy existing chart if any to avoid overlapping
        if (window.mySpendingChart) window.mySpendingChart.destroy();
        
        window.mySpendingChart = new Chart(spendingCtx, {
            type: 'line',
            data: {
                labels: sortedDates,
                datasets: [{
                    label: 'Avg Item Price ($)',
                    data: chartData,
                    borderColor: '#6366f1',
                    backgroundColor: 'rgba(99, 102, 241, 0.1)',
                    fill: true,
                    tension: 0.4,
                    borderWidth: 3,
                    pointRadius: 4,
                    pointBackgroundColor: '#6366f1'
                }]
            },
            options: { 
                responsive: true, 
                maintainAspectRatio: false,
                plugins: {
                    tooltip: {
                        callbacks: {
                            label: (ctx) => `Avg Price: $${ctx.parsed.y.toFixed(2)}`
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { color: 'rgba(255,255,255,0.05)' },
                        ticks: { maxTicksLimit: 12, color: '#9ca3af' }
                    },
                    y: {
                        beginAtZero: false,
                        grid: { color: 'rgba(255,255,255,0.05)' },
                        ticks: { 
                            callback: (val) => '$' + val,
                            color: '#9ca3af'
                        }
                    }
                }
            }
        });
    }

    if (categoryCtx) {
        const labels = Object.keys(categories);
        const dataValues = Object.values(categories);
        
        if (window.myCategoryChart) window.myCategoryChart.destroy();

        window.myCategoryChart = new Chart(categoryCtx, {
            type: 'doughnut',
            data: {
                labels: labels.map(l => l.replace('_', ' ').toUpperCase()),
                datasets: [{
                    data: dataValues,
                    backgroundColor: ['#10b981', '#ef4444', '#f59e0b', '#6366f1', '#a855f7', '#ec4899', '#06b6d4', '#8b5cf6'],
                    borderWidth: 0,
                    hoverOffset: 15
                }]
            },
            options: { 
                responsive: true, 
                maintainAspectRatio: false,
                plugins: {
                    legend: { 
                        position: window.innerWidth < 768 ? 'bottom' : 'right', 
                        labels: { color: '#9ca3af', font: { size: 10 } } 
                    }
                },
                cutout: '70%'
            }
        });
    }

    renderDeeperInsights(brandPrices);
    renderTargetIntelligence();
}

function renderTargetIntelligence() {
    const container = document.getElementById('target-intelligence-container');
    if (!container) return;

    // Tally confidence levels
    let high = 0, med = 0, low = 0, noMeta = 0;
    const lowConfItems = [];
    const recentChanges = [];

    _data.forEach(item => {
        const conf = item.target_confidence;
        if (!conf) { noMeta++; return; }
        if (conf === 'high') high++;
        else if (conf === 'medium') { med++; }
        else {
            low++;
            if (item.target_data_points === 0) {
                lowConfItems.push(item);
            }
        }
        // Detect recently-changed targets (target_updated = today)
        const today = new Date().toISOString().slice(0, 10);
        if (item.target_updated === today && item.target_method && item.target_method !== 'unchanged') {
            recentChanges.push(item);
        }
    });

    const total = high + med + low + noMeta || 1;
    const highPct = Math.round((high / total) * 100);
    const medPct  = Math.round((med  / total) * 100);
    const lowPct  = Math.round((low  / total) * 100);

    // Needs-data: items with zero price points that have a Woolworths URL
    const needsData = _data.filter(i => (i.target_data_points || 0) === 0 && i.woolworths).length;

    container.innerHTML = `
        <div class="target-intel-header">
            <i data-feather="target"></i>
            <span>Target Intelligence</span>
        </div>

        <div class="target-confidence-bar-wrap">
            <div class="target-conf-bar">
                <div class="tcb-fill high"  style="width:${highPct}%" title="${high} high-confidence"></div>
                <div class="tcb-fill med"   style="width:${medPct}%"  title="${med} medium-confidence"></div>
                <div class="tcb-fill low"   style="width:${lowPct}%"  title="${low} low-confidence"></div>
            </div>
            <div class="tcb-labels">
                <span><span class="conf-dot high"></span>${high} High</span>
                <span><span class="conf-dot med"></span>${med} Medium</span>
                <span><span class="conf-dot low"></span>${low} Low</span>
            </div>
        </div>

        <div class="target-intel-stats">
            <div class="ti-stat">
                <div class="ti-val">${high}</div>
                <div class="ti-label">Data-Driven<br>Targets</div>
            </div>
            <div class="ti-stat">
                <div class="ti-val">${low + noMeta}</div>
                <div class="ti-label">Need More<br>Receipts</div>
            </div>
            <div class="ti-stat">
                <div class="ti-val">${Math.round((high + med) / total * 100)}%</div>
                <div class="ti-label">Confidence<br>Score</div>
            </div>
        </div>

        ${needsData > 0 ? `
        <div class="ti-tip">
            <i data-feather="info"></i>
            <span>Run <strong>receipt_sync.py</strong> to unlock better targets for <strong>${needsData}</strong> untracked items.</span>
        </div>` : '<div class="ti-tip success"><i data-feather="check-circle"></i><span>All items have price observations — great coverage!</span></div>'}
    `;

    if (typeof feather !== 'undefined') feather.replace();
}

function renderDeeperInsights(brandPrices) {
    const container = document.getElementById('deep-insights-container');
    if (!container) return;

    // 1. Smart Buys (Low price, high volatility)
    const smartBuys = _data
        .filter(item => {
            const vol = _volatility[item.name] || 0;
            const isOnSpecial = (item.eff_price || 999) <= (item.target || 0);
            return isOnSpecial && vol > 10; // High confidence special
        })
        .sort((a, b) => (_volatility[b.name] || 0) - (_volatility[a.name] || 0))
        .slice(0, 3);

    // 2. Store Bias
    let wooliesCheaper = 0;
    let colesCheaper = 0;
    _data.forEach(item => {
        // Fallback: if all_stores is missing, use the current best store
        const w = item.all_stores?.woolworths?.eff_price || (item.store === 'woolworths' ? item.eff_price : null);
        const c = item.all_stores?.coles?.eff_price || (item.store === 'coles' ? item.eff_price : null);
        
        if (w && c) {
            if (w < c) wooliesCheaper++;
            else if (c < w) colesCheaper++;
        } else if (w) {
            wooliesCheaper++;
        } else if (c) {
            colesCheaper++;
        }
    });

    const total = (wooliesCheaper + colesCheaper) || 1;
    const wPercent = (wooliesCheaper / total) * 100;
    const cPercent = (colesCheaper / total) * 100;

    // 3. Brand Premium Analysis
    const privateAvg = brandPrices['Private Label'].sum / (brandPrices['Private Label'].count || 1);
    const nameAvg = brandPrices['Name Brand'].sum / (brandPrices['Name Brand'].count || 1);
    const premium = ((nameAvg - privateAvg) / privateAvg) * 100;

    let html = `
        <div class="deep-insight-card">
            <h4>🔥 Smart Buy Recommendations</h4>
            <div class="smart-buy-list">
                ${smartBuys.map(item => `
                    <div class="smart-buy-item">
                        <span class="name">${item.name}</span>
                        <div class="meta">
                            <span class="price">$${item.eff_price?.toFixed(2)}</span>
                            <span class="volatility-tag high">Volatility: ${(_volatility[item.name] || 0).toFixed(0)}%</span>
                        </div>
                    </div>
                `).join('')}
            </div>
            <p class="insight-tip">These items are at their target price and historically jump back up quickly.</p>
        </div>

        <div class="deep-insight-card">
            <h4>🏷 Brand Premium Index</h4>
            <div class="premium-viz">
                <div class="premium-value">${premium > 0 ? '+' : ''}${premium.toFixed(0)}%</div>
                <div class="premium-label">Avg. markup for name brands</div>
                <div class="premium-comparison">
                    <span>Private: $${privateAvg.toFixed(2)}</span>
                    <span>Name: $${nameAvg.toFixed(2)}</span>
                </div>
            </div>
            <p class="insight-tip">Average price difference between generic and name-brand items in your list.</p>
        </div>

        <div class="deep-insight-card">
            <h4>🏛 Store Price Bias</h4>
            <div class="bias-viz">
                <div class="bias-bar">
                    <div class="bias-fill woolies" style="width: ${wPercent}%"></div>
                    <div class="bias-fill coles" style="width: ${cPercent}%"></div>
                </div>
                <div class="bias-labels">
                    <span>Woolies: ${wooliesCheaper} items</span>
                    <span>Coles: ${colesCheaper} items</span>
                </div>
            </div>
            <p class="insight-tip">Based on current cheaper-entry count across your watchlist.</p>
        </div>
    `;

    container.innerHTML = html;
}


function renderSparkline(containerId, historyData, storeClass) {
    const container = document.getElementById(containerId);
    if (!container) return;
    
    const canvas = container.querySelector('canvas');
    if (!canvas) return;

    const color = storeClass === 'woolworths' ? '#10b981' : '#ef4444';
    
    // Sort history by date just in case
    const sorted = [...historyData].sort((a, b) => new Date(a.date) - new Date(b.date));
    
    // Take up to last 14 data points
    const recent = sorted.slice(-14);
    
    const labels = recent.map(h => h.date);
    const data = recent.map(h => h.price);

    new Chart(canvas, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                data: data,
                borderColor: color,
                borderWidth: 2,
                pointRadius: 0,
                pointHoverRadius: 4,
                fill: true,
                backgroundColor: color + '20', // 20 hex is approx 12% opacity
                tension: 0.3
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            return '$' + context.parsed.y.toFixed(2);
                        }
                    }
                }
            },
            scales: {
                x: { display: false },
                y: { display: false, min: Math.min(...data) * 0.95, max: Math.max(...data) * 1.05 }
            },
            layout: { padding: 0 }
        }
    });
}
