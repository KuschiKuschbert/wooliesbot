document.addEventListener('DOMContentLoaded', () => {
    feather.replace();
    initDashboard();
});

let _data = [];
let _history = {};
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
const MONTHLY_BUDGET = 800;


async function initDashboard() {
    try {
        const [dataRes, histRes] = await Promise.all([
            fetch('data.json').catch(() => null),
            fetch('history.json').catch(() => null)
        ]);

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
        
        // Setup background tasks
        setInterval(updateLastCheckedDisplay, 60000); // Update relative time every min
        setInterval(monitorApi, 30000); // Check API status every 30s
        monitorApi(); // Initial check

        if (histRes && histRes.ok) {
            _history = await histRes.json();
        }

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
    navLinks.forEach(link => {
        link.addEventListener('click', () => {
            const target = link.dataset.tab;
            _currentTab = target;
            
            navLinks.forEach(l => l.classList.remove('active'));
            link.classList.add('active');
            
            document.querySelectorAll('.tab-content').forEach(tab => {
                tab.classList.remove('active');
            });
            document.getElementById(`tab-${target}`).classList.add('active');
            
            if (target === 'analytics') renderAnalytics();
            else renderDashboard();
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
            btn.innerHTML = '<i data-feather="loader" class="spin"></i> Starting Sync...';
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
        if (effPrice && item.target && effPrice <= item.target && !item.price_unavailable) {
            specialsCount++;
            savingsToday += Math.max(0, item.target - effPrice);
        }
    });

    if (totalSpecialsEl) totalSpecialsEl.textContent = specialsCount;
    if (cartTotalEl) cartTotalEl.textContent = `$${savingsToday.toFixed(2)}`;

    // Monthly Budget Tracker
    let monthlySpent = 0;
    const now = new Date();
    
    Object.keys(_history).forEach(itemName => {
        const itemHistory = _history[itemName].history || [];
        // Group by day to prevent background scans from double-counting
        const dailySpend = {};
        
        itemHistory.forEach(h => {
            const d = new Date(h.date);
            if ((now - d) < (30 * 24 * 60 * 60 * 1000)) {
                if (h.store && h.store !== 'none') {
                    // Only count the latest entry for each unique day
                    dailySpend[h.date] = h.price;
                }
            }
        });
        
        monthlySpent += Object.values(dailySpend).reduce((a, b) => a + b, 0);
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
        // Near miss = between target and target * 1.10
        return effPrice > item.target && effPrice <= item.target * 1.10 && !item.price_unavailable;
    });
    
    if (nearMisses.length > 0) {
        section.style.display = 'block';
        nearMisses.slice(0, 4).forEach((item, index) => {
            const card = createItemCard(item, index, true);
            grid.appendChild(card);
        });
    } else {
        section.style.display = 'none';
    }
}

function createItemCard(item, index, type = 'special') {
    const effPrice = item.eff_price || item.price;
    const isSpecial = type === 'special' && effPrice <= item.target && !item.price_unavailable;
    const isNearMiss = type === 'near';
    const isPredicted = type === 'predicted';
    
    const card = document.createElement('div');
    const storeClass = item.store || 'woolworths';
    
    card.className = `item-card store-${storeClass} ${isNearMiss ? 'near-miss-card' : ''} ${isPredicted ? 'predicted-card' : ''}`;
    
    let imgHtml = item.image_url 
        ? `<img src="${item.image_url}" class="item-image" loading="lazy">`
        : `<div class="product-img-placeholder"><i data-feather="image"></i></div>`;

    const stockColor = item.stock === 'low' ? 'low' : (item.stock === 'medium' ? 'medium' : 'full');
    
    card.innerHTML = `
        ${imgHtml}
        <div class="item-content">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div class="store-badge ${storeClass}">${storeClass === 'woolworths' ? 'Woolies' : 'Coles'}</div>
                <div class="stock-dot ${stockColor}" title="Stock: ${item.stock}"></div>
            </div>
            <h3 class="item-title" style="margin-top: 8px;">${item.name}</h3>
            <div class="item-price-row">
                <span class="item-price">${item.price_unavailable ? '❓' : '$' + item.price.toFixed(2)}</span>
                <span class="item-target">Target: $${item.target.toFixed(2)}</span>
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
    grid.innerHTML = '';
    
    const predicted = _data.filter(item => {
        // Condition 1: Explicitly low stock
        if (item.stock === 'low') return true;
        
        // Condition 2: Buy frequency prediction
        if (item.last_purchased) {
            const last = new Date(item.last_purchased);
            const diffDays = (new Date() - last) / (1000 * 60 * 60 * 24);
            // Heuristic: If hasn't been bought in 21 days for pantry items
            if (item.type === 'pantry' && diffDays > 21) return true;
            if (item.type === 'pet' && diffDays > 14) return true;
        }
        return false;
    });

    if (predicted.length > 0) {
        section.style.display = 'block';
        predicted.slice(0, 6).forEach((item, idx) => {
            grid.appendChild(createItemCard(item, idx, 'predicted'));
        });
    } else {
        section.style.display = 'none';
    }
}


function updateListCount() {
    const el = document.getElementById('list-count');
    if (el) el.textContent = _shoppingList.length;
    
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
    if (!el || !_lastChecked) return;
    
    const lastDate = new Date(_lastChecked);
    if (isNaN(lastDate.getTime())) {
        el.textContent = _lastChecked;
        return;
    }
    
    const now = new Date();
    const diffMins = Math.floor((now - lastDate) / 60000);
    
    if (diffMins < 1) el.textContent = "Just now";
    else if (diffMins < 60) el.textContent = `${diffMins}m ago`;
    else {
        const hours = Math.floor(diffMins / 60);
        el.textContent = `${hours}h ${diffMins % 60}m ago`;
    }
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

function renderColaBattle() {
    const colaItems = _data.filter(i => i.compare_group === 'cola' && !i.price_unavailable);
    const winnerEl = document.getElementById('cola-winner');
    const detailsEl = document.getElementById('cola-details');
    
    if (colaItems.length === 0) {
        if (winnerEl) winnerEl.textContent = '—';
        if (detailsEl) detailsEl.textContent = 'No cola data yet';
        return;
    }

    // Find cheapest Pepsi and cheapest Coke by eff_price (per litre), ignoring zero prices
    const pepsis = colaItems.filter(i => i.name.toLowerCase().includes('pepsi') && (i.eff_price || i.price) > 0);
    const cokes = colaItems.filter(i => (i.name.toLowerCase().includes('coke') || i.name.toLowerCase().includes('coca')) && (i.eff_price || i.price) > 0);
    
    const cheapestPepsi = pepsis.length ? pepsis.reduce((a, b) => (a.eff_price || a.price) < (b.eff_price || b.price) ? a : b) : null;
    const cheapestCoke = cokes.length ? cokes.reduce((a, b) => (a.eff_price || a.price) < (b.eff_price || b.price) ? a : b) : null;

    if (!cheapestPepsi && !cheapestCoke) {
        if (winnerEl) winnerEl.textContent = '—';
        if (detailsEl) detailsEl.textContent = 'No cola data';
        return;
    }

    const pepsiPrice = cheapestPepsi ? (cheapestPepsi.eff_price || cheapestPepsi.price) : null;
    const cokePrice = cheapestCoke ? (cheapestCoke.eff_price || cheapestCoke.price) : null;
    
    if (pepsiPrice === null && cokePrice === null) {
        if (winnerEl) winnerEl.textContent = '—';
        if (detailsEl) detailsEl.textContent = 'Prices unavailable';
        return;
    }

    const pP = pepsiPrice || Infinity;
    const cP = cokePrice || Infinity;
    
    const pepsiStore = cheapestPepsi ? (cheapestPepsi.store === 'woolworths' ? '🟢W' : '🔴C') : '';
    const cokeStore = cheapestCoke ? (cheapestCoke.store === 'woolworths' ? '🟢W' : '🔴C') : '';

    if (winnerEl) {
        if (pP < cP) {
            winnerEl.innerHTML = `<span style="color: #3b82f6;">Pepsi Max</span> wins!`;
        } else if (cP < pP) {
            winnerEl.innerHTML = `<span style="color: #ef4444;">Coke Zero</span> wins!`;
        } else {
            winnerEl.textContent = 'Tied!';
        }
    }
    
    if (detailsEl) {
        let details = '';
        if (pepsiPrice !== null) details += `Pepsi $${pepsiPrice.toFixed(2)}/L ${pepsiStore}`;
        if (pepsiPrice !== null && cokePrice !== null) details += ' vs ';
        if (cokePrice !== null) details += `Coke $${cokePrice.toFixed(2)}/L ${cokeStore}`;
        detailsEl.textContent = details;
    }
}

function renderSpecials() {
    const grid = document.getElementById('specials-grid');
    grid.innerHTML = '';

    const displayItems = _data.filter(item => {
        const matchesStore = _currentFilter === 'all' || item.store === _currentFilter;
        const matchesCat = _currentCatFilter === 'all' || item.type === _currentCatFilter;
        const matchesSearch = !_searchText || item.name.toLowerCase().includes(_searchText);
        const isSpecial = (item.eff_price || item.price) <= item.target && !item.price_unavailable;
        return matchesStore && matchesCat && matchesSearch && isSpecial;
    });

    // Sorting Logic
    displayItems.sort((a, b) => {
        if (_currentSort === 'name') return a.name.localeCompare(b.name);
        
        const priceA = a.eff_price || a.price;
        const priceB = b.eff_price || b.price;
        
        if (_currentSort === 'price') return priceA - priceB;
        
        if (_currentSort === 'discount') {
            const savingsA = (a.target - priceA) / a.target;
            const savingsB = (b.target - priceB) / b.target;
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
        const isSpecial = (item.eff_price || item.price) <= item.target && !item.price_unavailable;
        const stockColor = item.stock === 'low' ? 'low' : (item.stock === 'medium' ? 'medium' : 'full');
        
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
            <td>${item.price_unavailable ? '❓' : '$' + item.price.toFixed(2)}</td>
            <td>$${item.target.toFixed(2)}</td>
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
    const spendingHistory = {};
    let totalSavings = 0;
    let itemsBoughtAtTarget = 0;
    let totalItemsPurchased = 0;

    Object.entries(_history).forEach(([name, data]) => {
        const itemInfo = _data.find(i => i.name === name) || {};
        const cat = itemInfo.type || 'pantry';
        
        data.history.forEach(h => {
            const date = h.date.substring(0, 7); // YYYY-MM
            spendingHistory[date] = (spendingHistory[date] || 0) + h.price;
            categories[cat] = (categories[cat] || 0) + h.price;
            
            totalItemsPurchased++;
            if (h.is_special) itemsBoughtAtTarget++;
            
            const shelfPrice = itemInfo.price || h.price;
            totalSavings += Math.max(0, shelfPrice - h.price);
        });
    });

    const efficiency = totalItemsPurchased > 0 ? (itemsBoughtAtTarget / totalItemsPurchased) * 100 : 0;
    
    document.getElementById('analytic-savings-val').textContent = `$${totalSavings.toFixed(0)}`;
    document.getElementById('analytic-efficiency-val').textContent = `${efficiency.toFixed(0)}%`;

    // Charts
    const spendingCtx = document.getElementById('spending-chart')?.getContext('2d');
    const categoryCtx = document.getElementById('category-chart')?.getContext('2d');

    if (spendingCtx) {
        const sortedDates = Object.keys(spendingHistory).sort();
        new Chart(spendingCtx, {
            type: 'bar',
            data: {
                labels: sortedDates,
                datasets: [{
                    label: 'Monthly Spending ($)',
                    data: sortedDates.map(d => spendingHistory[d]),
                    backgroundColor: '#6366f1'
                }]
            },
            options: { responsive: true, maintainAspectRatio: false }
        });
    }

    if (categoryCtx) {
        new Chart(categoryCtx, {
            type: 'doughnut',
            data: {
                labels: Object.keys(categories),
                datasets: [{
                    data: Object.values(categories),
                    backgroundColor: ['#10b981', '#ef4444', '#f59e0b', '#6366f1', '#a855f7', '#ec4899', '#06b6d4', '#8b5cf6']
                }]
            },
            options: { responsive: true, maintainAspectRatio: false }
        });
    }
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
