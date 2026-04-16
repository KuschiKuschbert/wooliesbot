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

// Track sparkline Chart instances so we can destroy them before recreating.
// Keyed by container element ID string.
const _sparklineCharts = {};

const _DISPLAY_ABBREVS = [
    [/\bWw\b/gi, 'Woolworths'], [/\bDf\b/gi, 'Dairy Farmers'],
    [/\bEss\b/gi, 'Essentials'], [/\bSrdgh\b/gi, 'Sourdough'],
    [/\bHmlyn\b/gi, 'Himalayan'], [/\bBflied\b/gi, 'Butterflied'],
    [/\bB'Flied\b/gi, 'Butterflied'], [/\bLemn\b/gi, 'Lemon'],
    [/\bGrlc\b/gi, 'Garlic'], [/\bStarwberry\b/gi, 'Strawberry'],
    [/\bConc\b/gi, 'Concentrate'], [/\bRw\b/gi, ''],
    [/\bTrplsmkd\b/gi, 'Triple Smoked'], [/\bShvd\b/gi, 'Shaved'],
    [/\bApprvd\b/gi, 'Approved'], [/\bF\/F\b/gi, 'Fat Free'],
    [/\bF\/C\b/gi, 'Fresh Choice'], [/\bP\/P\b/gi, ''],
    [/\bPnut\b/gi, 'Peanut'], [/\bCrml\b/gi, 'Caramel'],
    [/\bCkie\b/gi, 'Cookie'], [/\bBtr\b/gi, 'Butter'],
    [/\bEfferv\b/gi, 'Effervescent'], [/\bHm\b/gi, 'Ham'],
    [/\bT\/Tiss\b/gi, 'Toilet Tissue'], [/\bLge\b/gi, 'Large'],
    [/\bXl\b/gi, 'XL'], [/\bChoc\b/gi, 'Chocolate'],
    [/\bPud\b/gi, 'Pudding'], [/\bBbq\b/gi, 'BBQ'],
    [/\bPb\b/gi, 'Peanut Butter'], [/\bDbl\b/gi, 'Double'],
    [/\bEsprs\b/gi, 'Espresso'], [/\bFlav\b/gi, 'Flavoured'],
    [/\bWtr\b/gi, 'Water'], [/\bNatrl\b/gi, 'Natural'],
    [/\b35Hr\b/gi, '35 Hour'], [/\bCb\b/gi, 'Carb'],
];
function displayName(name) {
    if (!name) return '';
    let n = name;
    for (const [re, rep] of _DISPLAY_ABBREVS) n = n.replace(re, rep);
    return n.replace(/\s{2,}/g, ' ').trim();
}


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

    // Sort pills (E — replaces native <select>)
    document.querySelectorAll('.sort-pill').forEach(pill => {
        pill.addEventListener('click', () => {
            document.querySelectorAll('.sort-pill').forEach(p => p.classList.remove('active'));
            pill.classList.add('active');
            _currentSort = pill.dataset.sort;
            _currentPage = 1;
            renderSpecials();
        });
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

    // (Keep sync button removed — use copy-list-btn for sharing)
    
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
    renderWednesdayBanner();
    renderStats();
    renderTop5Deals();
    renderEssentials();
    renderBuyNow();         // F: Buy Now priority card
    renderPredictions();
    renderNearMisses();
    renderSpecials();
    // Master table is lazy-loaded on expand (D) — just update the meta count
    const metaEl = document.getElementById('master-table-meta');
    if (metaEl) metaEl.textContent = `${_data.length} items`;
    updateListCount();
    checkPriceDropAlerts();
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
    
    // Days until next Wednesday — 0 means Wednesday IS today (just refreshed)
    let daysLeft = (3 - day + 7) % 7;
    
    if (daysLeft === 0) {
        // It's Wednesday — new specials just dropped
        pill.classList.remove('urgent');
        textEl.textContent = 'New specials live today!';
    } else if (daysLeft === 1) {
        pill.classList.add('urgent');
        textEl.textContent = 'Ends TOMORROW (Tue)';
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
        const shelf = item.price || effPrice;
        const isSpecial = item.on_special || (item.target > 0 && effPrice <= item.target && !item.price_unavailable);
        if (isSpecial) {
            specialsCount++;
            // Compare was_price to shelf price (not eff_price) to avoid unit mismatch
            const ref = item.was_price ? Math.max(0, item.was_price - shelf) : Math.max(0, (item.target || 0) - effPrice);
            savingsToday += ref;
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



// ── Essentials: editable, persisted in localStorage ───────────────────────
const DEFAULT_ESSENTIALS = [
    // Produce (by purchase frequency)
    "Spinach", "Onions", "Avocado", "Baby Potatoes", "Broccolini",
    "Capsicum", "Zucchini", "Cherry Tomatoes", "Sliced Mushrooms", "Pak Choy",
    // Dairy
    "Eggs", "Cream", "Cheese", "Greek Yoghurt", "Bocconcini", "Creamy Vanilla", "Sour Cream",
    // Protein (Beef Mince is #1 most-bought item)
    "Chicken Breast", "Beef Mince",
    // Pantry
    "Passata", "Diced Tomatoes", "Garlic", "Maple Syrup", "Fajita Seasoning",
    // Bakery
    "Sourdough Loaf", "English Muffins",
    // Drinks
    "Nescafe Vanilla", "Pepsi Max",
    // Treats & Pets
    "Lindt 95%", "Whiskas",
];



function getEssentials() {
    const stored = localStorage.getItem('essentialsList');
    return stored ? JSON.parse(stored) : [...DEFAULT_ESSENTIALS];
}

function saveEssentials(list) {
    localStorage.setItem('essentialsList', JSON.stringify(list));
}

// Auto-reset checked items each new grocery week (Sunday)
function maybeResetEssentials() {
    const today = new Date();
    const todayStr = today.toDateString();
    const lastReset = localStorage.getItem('essentialsLastReset');
    // Reset on Sunday (day 0)
    if (today.getDay() === 0 && lastReset !== todayStr) {
        localStorage.removeItem('essentialsChecked');
        localStorage.setItem('essentialsLastReset', todayStr);
    }
}

// ── Fuzzy price lookup — matches display names to tracked product names ─────
// 'Spinach' → 'F/C Babyspinach 120G', 'Chicken Breast' → 'Ww Chicken Breast Fillets...'
// When multiple items match, prefers the one with most purchase history.
function findDataItem(name) {
    const dataArr = _data || [];
    const q = name.toLowerCase().trim();

    const byHistory = (a, b) =>
        (b.price_history?.length || 0) - (a.price_history?.length || 0);

    // 1. Exact match (case insensitive)
    const exact = dataArr.find(i => i.name.toLowerCase() === q);
    if (exact) return exact;

    // 2. All meaningful words in the display name appear in the tracked item name
    //    e.g. "Chicken Breast" → items containing both "chicken" AND "breast"
    //    e.g. "Baby Potatoes" → items containing both "baby" AND "potato"
    const words = q.split(/\s+/).filter(w => w.length > 2);
    if (words.length) {
        // Also try stemmed forms (strip trailing 's'/'es') for each word
        const stems = words.map(w => w.replace(/i?e?s$/, ''));
        const matches = dataArr.filter(i => {
            const n = i.name.toLowerCase();
            return stems.every(s => n.includes(s));
        });
        if (matches.length) return matches.sort(byHistory)[0];
    }

    // 3. Single-word query only: try stem match across all tracked items
    //    e.g. "Onions" → stem "onion" → "Onion Brown 1Kg P/P"
    //    Skip for multi-word to avoid false positives like "Baby" matching "Babyspinach"
    if (words.length <= 1) {
        const sigWords = q.split(/\s+/).filter(w => w.length > 3);
        const stems3 = sigWords.map(w => w.replace(/i?e?s$/, ''));
        for (const s of stems3) {
            const matches = dataArr.filter(i => i.name.toLowerCase().includes(s));
            if (matches.length) return matches.sort(byHistory)[0];
        }
    }

    return null;
}


function renderEssentials() {
    maybeResetEssentials();
    const list = document.getElementById('essentials-list');
    if (!list) return;
    list.innerHTML = '';

    const essentials = getEssentials();
    const checkedItems = JSON.parse(localStorage.getItem('essentialsChecked') || '[]');

    // ── Fuzzy price lookup (delegates to module-level findDataItem) ─────────


    // ── Header row with edit toggle ──────────────────────────────────────
    const header = document.createElement('div');
    header.className = 'essentials-header';
    const doneCount = checkedItems.length;
    const totalCount = essentials.length;
    header.innerHTML = `
        <span class="essentials-progress-text">${doneCount}/${totalCount} got</span>
        <div class="essentials-header-actions">
            <button class="essentials-reset-btn" onclick="resetEssentialsChecked()" title="Uncheck all">↺</button>
            <button class="essentials-edit-btn" onclick="toggleEssentialsEdit()" title="Edit list" id="essentials-edit-btn">✏️</button>
        </div>`;
    list.appendChild(header);

    // ── Progress bar ─────────────────────────────────────────────────────
    const progressWrap = document.createElement('div');
    progressWrap.className = 'essentials-progress-bar-bg';
    progressWrap.innerHTML = `<div class="essentials-progress-fill" style="width:${totalCount ? (doneCount/totalCount*100) : 0}%"></div>`;
    list.appendChild(progressWrap);

    // ── Item rows ─────────────────────────────────────────────────────────
    // Unchecked first, then checked (greyed)
    const sorted = [...essentials].sort((a, b) => {
        const aChecked = checkedItems.includes(a);
        const bChecked = checkedItems.includes(b);
        return aChecked - bChecked;
    });

    sorted.forEach(itemName => {
        const isChecked = checkedItems.includes(itemName);
        const dataItem = findDataItem(itemName);

        const price = dataItem ? (dataItem.eff_price || dataItem.price) : null;
        const onSpecial = dataItem?.on_special;
        const staleBadge = dataItem?.stale ? getStaleBadge(dataItem, true) : '';
        const stock = dataItem?.stock;

        // Stock dot colour
        const dotClass = stock === 'low' ? 'low' : stock === 'medium' ? 'medium' : stock === 'full' ? 'full' : '';
        const stockDot = dotClass ? `<span class="stock-dot ${dotClass}" title="${stock} stock"></span>` : '';

        // Price badge
        const priceBadge = price
            ? `<span class="essential-price ${onSpecial ? 'on-sale' : ''}">${onSpecial ? '🔥' : ''}$${price.toFixed(2)}</span>${staleBadge}`
            : '';

        const row = document.createElement('div');
        row.className = `essential-row${isChecked ? ' checked' : ''}`;
        row.innerHTML = `
            <label class="essential-checkbox-area">
                <input type="checkbox" class="essential-cb" ${isChecked ? 'checked' : ''} data-item="${itemName}">
                <span class="essential-label ${isChecked ? 'checked' : ''}">${stockDot}${itemName}</span>
            </label>
            <div class="essential-meta">
                ${priceBadge}
                <button class="essential-add-btn" title="Add to shopping list"
                    onclick="addEssentialToList('${itemName.replace(/'/g, "\\'")}')"
                    style="${isChecked ? 'opacity:0.4;' : ''}">+</button>
                <button class="essential-remove-btn hidden" title="Remove from essentials"
                    onclick="removeFromEssentials('${itemName.replace(/'/g, "\\'")}')" data-remove>🗑</button>
            </div>`;

        row.querySelector('.essential-cb').addEventListener('change', (e) => {
            let current = JSON.parse(localStorage.getItem('essentialsChecked') || '[]');
            if (e.target.checked) {
                current.push(itemName);
            } else {
                current = current.filter(i => i !== itemName);
            }
            localStorage.setItem('essentialsChecked', JSON.stringify(current));
            renderEssentials();
        });

        list.appendChild(row);
    });

    // ── Edit mode: add new item input ─────────────────────────────────────
    const editMode = list.dataset.editMode === 'true';
    const addRow = document.createElement('div');
    addRow.className = 'essential-add-row' + (editMode ? '' : ' hidden');
    addRow.id = 'essential-add-row';
    addRow.innerHTML = `
        <input type="text" id="essential-new-input" placeholder="Add item..." class="essential-new-input">
        <button class="essential-add-confirm-btn" onclick="addToEssentials()">Add</button>`;
    list.appendChild(addRow);

    // ── Edit mode: reset to defaults button ───────────────────────────────
    const resetRow = document.createElement('div');
    resetRow.className = 'essential-reset-defaults-row' + (editMode ? '' : ' hidden');
    resetRow.innerHTML = `
        <button class="essential-reset-defaults-btn" onclick="resetEssentialsToDefaults()">
            ↺ Reset to default list
        </button>`;
    list.appendChild(resetRow);

    // Restore edit mode visual state
    if (editMode) {
        list.querySelectorAll('[data-remove]').forEach(btn => btn.classList.remove('hidden'));
        const editBtn = document.getElementById('essentials-edit-btn');
        if (editBtn) editBtn.textContent = '✓';
    }

    if (typeof feather !== 'undefined') feather.replace();
}

function resetEssentialsChecked() {
    localStorage.removeItem('essentialsChecked');
    renderEssentials();
}

function resetEssentialsToDefaults() {
    if (!confirm('Reset to the default list? Your custom changes will be lost.')) return;
    localStorage.removeItem('essentialsList');
    localStorage.removeItem('essentialsChecked');
    const list = document.getElementById('essentials-list');
    if (list) list.dataset.editMode = 'false';
    renderEssentials();
}

function toggleEssentialsEdit() {
    const list = document.getElementById('essentials-list');
    if (!list) return;
    const isEditing = list.dataset.editMode === 'true';
    list.dataset.editMode = isEditing ? 'false' : 'true';
    renderEssentials();
    if (!isEditing) {
        // Focus the add input
        setTimeout(() => document.getElementById('essential-new-input')?.focus(), 50);
    }
}

function addToEssentials() {
    const input = document.getElementById('essential-new-input');
    if (!input) return;
    const val = input.value.trim();
    if (!val) return;
    const essentials = getEssentials();
    if (!essentials.map(e => e.toLowerCase()).includes(val.toLowerCase())) {
        essentials.push(val);
        saveEssentials(essentials);
    }
    input.value = '';
    renderEssentials();
    // Keep edit mode open
    const list = document.getElementById('essentials-list');
    if (list) list.dataset.editMode = 'true';
    renderEssentials();
}

function removeFromEssentials(itemName) {
    const essentials = getEssentials().filter(e => e.toLowerCase() !== itemName.toLowerCase());
    saveEssentials(essentials);
    const list = document.getElementById('essentials-list');
    if (list) list.dataset.editMode = 'true';
    renderEssentials();
    if (list) list.dataset.editMode = 'true';
    renderEssentials();
}

function addEssentialToList(itemName) {
    // Use fuzzy matching to find the real tracked product
    const dataItem = findDataItem(itemName);
    if (dataItem) {
        addToList(dataItem.name);
    } else {
        // Fallback: add by display name with no price
        if (!_shoppingList.find(i => i.name === itemName)) {
            _shoppingList.push({ name: itemName, price: null, qty: 1 });
            localStorage.setItem('shoppingList', JSON.stringify(_shoppingList));
            renderShoppingList();
            updateListCount();
        }
    }
    renderEssentials();
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

function getStaleBadge(item, compact = false) {
    if (!item?.stale) return '';
    const asOf = item.stale_as_of ? ` (last good: ${item.stale_as_of})` : '';
    const title = `Using last known good price${asOf}`;
    const label = compact ? 'Stale' : '⏳ Stale';
    return `<span class="stale-badge${compact ? ' compact' : ''}" title="${title}">${label}</span>`;
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
    const staleBadge = getStaleBadge(item);
    const targetTooltip = item.target_method 
        ? `title="${item.target_method}${item.target_data_points ? ` (${item.target_data_points} data points)` : ''}"` 
        : '';

    // Was/Now pricing for store-confirmed specials
    // Compare was_price to shelf price; cap at 70% to catch unit mismatches in stale data
    const shelfPrice = item.price || effPrice;
    let priceHtml;
    const hasSaneWas = item.on_special && item.was_price && item.was_price > shelfPrice && item.was_price < shelfPrice * 4;
    if (hasSaneWas) {
        const savePct = Math.round((1 - shelfPrice / item.was_price) * 100);
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
    
    // Build store comparison row if both stores available
    const allStores = item.all_stores || {};
    const wooliesData = allStores.woolworths;
    const colesData = allStores.coles;
    let storeCompareHtml = '';
    if (wooliesData && colesData) {
        const wp = wooliesData.eff_price || wooliesData.price;
        const cp = colesData.eff_price || colesData.price;
        const wooliesWinner = wp <= cp;
        const saving = Math.abs(wp - cp).toFixed(2);
        storeCompareHtml = `
            <div class="store-compare">
                <div class="store-compare-row ${wooliesWinner ? 'winner' : ''}">
                    <span class="store-compare-label">🟢 Woolies</span>
                    <span class="store-compare-price">$${wp.toFixed(2)}</span>
                    ${wooliesWinner ? '<span class="winner-badge">✓ Best</span>' : ''}
                </div>
                <div class="store-compare-row ${!wooliesWinner ? 'winner' : ''}">
                    <span class="store-compare-label">🔴 Coles</span>
                    <span class="store-compare-price">$${cp.toFixed(2)}</span>
                    ${!wooliesWinner ? `<span class="winner-badge">✓ Save $${saving}</span>` : ''}
                </div>
            </div>
        `;
    }

    // Product URL for store badge link
    const productUrl = item.store === 'coles' 
        ? (item.coles || '#') 
        : (item.woolworths || '#');

    card.innerHTML = `
        ${imgHtml}
        <div class="item-content">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <a href="${productUrl}" target="_blank" rel="noopener" style="text-decoration:none;">
                    <div class="store-badge ${storeClass}">${storeClass === 'woolworths' ? 'Woolies' : 'Coles'}</div>
                </a>
                <div style="display:flex; gap:4px; align-items:center;">
                    ${staleBadge}
                    ${confidenceBadge}
                    <div class="stock-dot ${stockColor}" title="Stock: ${item.stock}"></div>
                </div>
            </div>
            <h3 class="item-title" style="margin-top: 8px;">${displayName(item.name)}</h3>
            <div class="item-price-row">
                ${priceHtml}
                <span class="item-target" ${targetTooltip}>${(item.target || 0) > 0 ? 'Target: $' + item.target.toFixed(2) : '<span style="opacity:0.4">watching</span>'}</span>
            </div>
            ${storeCompareHtml}
            <button class="add-to-list-btn" onclick="addToList('${item.name.replace(/'/g, "\\'")}'  , this)">
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
    
    // Enable copy button
    const copyBtn = document.getElementById('copy-list-btn');
    if (copyBtn) copyBtn.disabled = _shoppingList.length === 0;
}

function addToList(itemName, callerBtn) {
    const item = _data.find(i => i.name === itemName);
    if (!item) return;

    // callerBtn is passed explicitly as `this` from the onclick attribute
    const btn = callerBtn || null;

    // Prevent duplicates
    if (_shoppingList.find(l => l.name === itemName)) {
        if (btn) {
            const originalText = btn.innerHTML;
            btn.innerHTML = '<i data-feather="check"></i> In list!';
            btn.style.background = 'rgba(99,102,241,0.5)';
            feather.replace();
            setTimeout(() => { btn.innerHTML = originalText; btn.style.background = ''; feather.replace(); }, 1500);
        }
        return;
    }
    
    // Factor in quantities
    let qty = 1;
    if (_shopMode === 'big') {
        if (item.type === 'fresh_protein' || item.type === 'meat') qty = 4;
        else if (['pet', 'pantry', 'household', 'frozen'].includes(item.type)) qty = 2;
    }

    const listItem = {
        name: item.name,
        price: item.eff_price || item.price,
        qty: qty,
        store: item.store || 'woolworths',
        image: item.local_image || item.image_url || null,
        on_special: item.on_special || false,
        was_price: item.was_price || null,
    };
    
    _shoppingList.push(listItem);
    localStorage.setItem('shoppingList', JSON.stringify(_shoppingList));
    updateListCount();
    
    // Visual feedback
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
        const itemTotal = (item.price || 0) * item.qty;
        total += itemTotal;
        
        const div = document.createElement('div');
        div.className = 'shopping-item';
        const specialBadge = item.on_special && item.was_price
            ? `<span class="save-badge" style="font-size:9px;">SPECIAL</span>` : '';
        div.innerHTML = `
            ${item.image ? `<img src="${item.image}" onerror="this.style.display='none'">` : '<div style="width:40px;height:40px;background:rgba(255,255,255,0.05);border-radius:8px;display:flex;align-items:center;justify-content:center;"><i data-feather="image" style="width:16px;"></i></div>'}
            <div class="shopping-item-info">
                <div class="shopping-item-name">${item.qty}× ${displayName(item.name)} ${specialBadge}</div>
                <div class="shopping-item-price">${item.store === 'woolworths' ? '🟢 Woolies' : '🔴 Coles'} — $${itemTotal.toFixed(2)}</div>
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
        // HTTPS (e.g. GitHub Pages) cannot call http://localhost — browser mixed-content block (expected).
        const onGithubPages = /\.github\.io$/i.test(window.location.hostname || '');
        if (window.location.protocol === 'https:' && _apiUrl.startsWith('http://localhost')) {
            dot.className = 'status-dot offline';
            text.textContent = onGithubPages
                ? 'Bridge: local only (cloud view)'
                : 'Live Sync: N/A (use bridge URL in settings)';
            text.title = 'Set Bridge URL in settings to your Mac IP or tunnel (HTTPS cannot reach localhost).';
            return;
        }

        const res = await fetch(`${_apiUrl}/status`).catch(() => null);
        if (res && res.ok) {
            dot.className = 'status-dot online';
            text.textContent = 'Live Sync: On';
        } else {
            dot.className = 'status-dot offline';
            text.textContent = 'Live Sync: Off';
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
        const pWinner = pP < cP;
        const cWinner = cP < pP;

        const getStoreUrl = (item) => {
            if (!item) return null;
            const scrapeStore = (item.scrape_history || []).slice(-1)[0]?.store;
            const activeStore = scrapeStore || item.store;
            if (activeStore === 'coles') {
                // Prefer specific Coles product URL, fall back to Coles search
                if (item.coles) return item.coles;
                const q = encodeURIComponent(item.name);
                return `https://www.coles.com.au/search?q=${q}`;
            }
            // Woolworths — prefer specific URL, fall back to search
            return item.woolworths || `https://www.woolworths.com.au/shop/search/products?searchTerm=${encodeURIComponent(item.name)}`;
        };

        const getStoreBadge = (item) => {
            if (!item) return '';
            const scrapeStore = (item.scrape_history || []).slice(-1)[0]?.store;
            const store = scrapeStore || item.store;
            return store === 'woolworths'
                ? '<span class="fighter-store-badge woolies">Woolworths</span>'
                : '<span class="fighter-store-badge coles">Coles</span>';
        };

        const isOnSpecial = (item) => item && (item.on_special || (item.scrape_history || []).slice(-1)[0]?.is_special);

        const viewLink = (item) => {
            const url = getStoreUrl(item);
            return url ? `<a href="${url}" target="_blank" rel="noopener" class="fighter-view-link">View on store →</a>` : '';
        };

        // Use scrape store for winner colour — not stale item.store field
        const getActiveStore = (item) => {
            if (!item) return 'woolworths';
            return (item.scrape_history || []).slice(-1)[0]?.store || item.store || 'woolworths';
        };

        return `
            <div class="battle-arena">
                <div class="arena-title">${title}</div>
                <div class="arena-fighters">
                    <div class="fighter ${pWinner ? `winner winner-${getActiveStore(pepsi)}` : ''}">
                        ${pWinner ? `<div class="winner-badge">🏆 CHEAPEST</div>` : ''}
                        <div class="fighter-brand">Pepsi</div>
                        <div class="fighter-price">$${pP === Infinity ? '—' : pP.toFixed(2)}/L</div>
                        <div class="fighter-product">${pepsi ? displayName(pepsi.name) : 'No Data'}</div>
                        <div class="fighter-meta">${getStoreBadge(pepsi)}${isOnSpecial(pepsi) ? '<span class="fighter-on-special">🔥 On Special</span>' : ''}${getStaleBadge(pepsi, true)}</div>
                        ${viewLink(pepsi)}
                    </div>
                    <div class="battle-vs">VS</div>
                    <div class="fighter ${cWinner ? `winner winner-${getActiveStore(coke)}` : ''}">
                        ${cWinner ? `<div class="winner-badge">🏆 CHEAPEST</div>` : ''}
                        <div class="fighter-brand">Coke</div>
                        <div class="fighter-price">$${cP === Infinity ? '—' : cP.toFixed(2)}/L</div>
                        <div class="fighter-product">${coke ? displayName(coke.name) : 'No Data'}</div>
                        <div class="fighter-meta">${getStoreBadge(coke)}${isOnSpecial(coke) ? '<span class="fighter-on-special">🔥 On Special</span>' : ''}${getStaleBadge(coke, true)}</div>
                        ${viewLink(coke)}
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
        const matchesSearch = !_searchText || item.name.toLowerCase().includes(_searchText) || displayName(item.name).toLowerCase().includes(_searchText);
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
            // Compare was_price to shelf price (not eff_price) to avoid unit mismatch
            const shelfA = a.price || priceA;
            const shelfB = b.price || priceB;
            const refA = a.was_price && a.was_price > shelfA ? a.was_price : (a.target || shelfA);
            const refB = b.was_price && b.was_price > shelfB ? b.was_price : (b.target || shelfB);
            const savingsA = (refA - shelfA) / refA;
            const savingsB = (refB - shelfB) / refB;
            return savingsB - savingsA;
        }
        return 0;
    });

    if (displayItems.length === 0) {
        // B: Show near-miss fallback instead of plain empty state
        const nearMisses = _data
            .filter(item => {
                if (!item.target || !item.price) return false;
                const ratio = item.price / item.target;
                return ratio > 1 && ratio <= 1.10; // within 10% above target
            })
            .sort((a, b) => (a.price / a.target) - (b.price / b.target))
            .slice(0, 6);

        if (nearMisses.length > 0) {
            grid.innerHTML = `
                <div class="no-deals-state" style="grid-column:1/-1;">
                    <p>No active deals matching your filters right now.</p>
                    <div class="no-deals-near-title">🎯 Closest to deal price — worth watching:</div>
                </div>`;
            nearMisses.forEach((item, i) => {
                const card = createItemCard(item, i);
                card.classList.add('near-miss-card');
                grid.appendChild(card);
            });
        } else {
            grid.innerHTML = '<p style="color: var(--text-muted); grid-column: 1/-1; padding: 2rem; text-align:center;">No deals today — check back Wednesday when specials refresh! 🗓️</p>';
        }
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


// ── D: Collapsible Master Tracklist ──────────────────────────────────────
function toggleMasterTable() {
    const btn = document.getElementById('master-table-toggle');
    const body = document.getElementById('master-table-body');
    if (!btn || !body) return;
    const isOpen = btn.getAttribute('aria-expanded') === 'true';
    if (isOpen) {
        body.style.display = 'none';
        btn.setAttribute('aria-expanded', 'false');
    } else {
        body.style.display = 'block';
        btn.setAttribute('aria-expanded', 'true');
        // Lazy-render: only render if empty
        const tbody = document.getElementById('all-items-tbody');
        if (tbody && tbody.children.length === 0) renderAllItems();
        if (typeof feather !== 'undefined') feather.replace();
    }
}

// ── F: Buy Now Priority View ──────────────────────────────────────────────
function renderBuyNow() {
    const card = document.getElementById('buy-now-card');
    const list = document.getElementById('buy-now-list');
    const badge = document.getElementById('buy-now-count');
    if (!card || !list) return;

    const priorityItems = _data.filter(item => {
        const isLow = item.stock === 'low';
        const isOnSpecial = item.on_special === true;
        const atTarget = item.target && item.price && item.price <= item.target;
        return isLow && (isOnSpecial || atTarget);
    }).sort((a, b) => {
        // Sort by savings % descending
        const savA = a.was_price ? (a.was_price - a.price) / a.was_price : 0;
        const savB = b.was_price ? (b.was_price - b.price) / b.was_price : 0;
        return savB - savA;
    }).slice(0, 8);

    if (priorityItems.length === 0) {
        card.style.display = 'none';
        return;
    }

    card.style.display = 'block';
    badge.textContent = priorityItems.length;
    list.innerHTML = priorityItems.map(item => {
        const price = item.eff_price || item.price || 0;
        const wasPr = item.was_price;
        const shelf = item.price || price;
        const saveStr = wasPr && wasPr > shelf ? `-${Math.round((wasPr - shelf) / wasPr * 100)}%` : '🎯';
        const priceStr = price ? `$${price.toFixed(2)}` : '—';
        return `
            <div class="buy-now-row" onclick="openModal('${item.name.replace(/'/g, "\\'")}')">
                <div class="buy-now-stock-dot"></div>
                <div class="buy-now-info">
                    <div class="buy-now-name">${displayName(item.name)}</div>
                    <div class="buy-now-price">${priceStr}${wasPr ? ` <span style="color:var(--text-muted);font-weight:400;text-decoration:line-through;">$${wasPr.toFixed(2)}</span>` : ''}</div>
                </div>
                <div class="buy-now-save">${saveStr}</div>
            </div>`;
    }).join('');
}

function renderAllItems() {

    const tbody = document.getElementById('all-items-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    const filteredData = _data.filter(item => {
        const matchesStore = _currentFilter === 'all' || item.store === _currentFilter;
        const matchesSearch = !_searchText || item.name.toLowerCase().includes(_searchText) || displayName(item.name).toLowerCase().includes(_searchText);
        return matchesStore && matchesSearch;
    }).sort((a, b) => displayName(a.name).localeCompare(displayName(b.name)));

    filteredData.forEach((item, index) => {
        const tr = document.createElement('tr');
        const effPrice = item.eff_price || item.price;
        const isSpecial = item.on_special || ((item.target || 0) > 0 && effPrice <= item.target && !item.price_unavailable);
        const stockColor = item.stock === 'low' ? 'low' : (item.stock === 'medium' ? 'medium' : 'full');
        
        let priceCell;
        const itemShelf = item.price || effPrice;
        if (item.on_special && item.was_price && item.was_price > itemShelf) {
            const savePct = Math.round((1 - itemShelf / item.was_price) * 100);
            priceCell = `$${effPrice.toFixed(2)} <span class="was-price">$${item.was_price.toFixed(2)}</span> <span class="save-badge">-${savePct}%</span>`;
        } else {
            priceCell = item.price_unavailable ? '❓' : `$${effPrice.toFixed(2)}`;
        }

        tr.innerHTML = `
            <td>
                <span style="font-weight:600;">${displayName(item.name)}</span>
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
    document.getElementById('modal-title').textContent = displayName(item.name);
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
        const response = await fetch(`${_apiUrl}/update_stock`, {
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
    // FIXED: Use price_history (has 5 months of data) NOT scrape_history (only 1 entry = today)
    const priceIndexByMonth = {}; // YYYY-MM -> { sum: X, count: Y }
    let totalRealizedSavings = 0;
    let itemsBoughtAtTarget = 0;

    // 1. Build Price Index from price_history + compute volatility from price_history
    //    scrape_history only has 1 entry per item so it's useless for trends/volatility.
    _data.forEach(item => {
        const target = item.target || 0;
        const ph = item.price_history || [];

        // ── Volatility from price_history (the real historical data) ───────
        const phPrices = ph.map(h => h.price).filter(p => p > 0 && p < 1000);
        if (phPrices.length > 2) {
            const avg = phPrices.reduce((a, b) => a + b) / phPrices.length;
            const variance = phPrices.reduce((a, b) => a + Math.pow(b - avg, 2), 0) / phPrices.length;
            const stdDev = Math.sqrt(variance);
            _volatility[item.name] = (stdDev / avg) * 100;
        }

        // ── Price trends from price_history ────────────────────────────────
        ph.forEach(h => {
            const p = h.price;
            if (!p || p > 1000) return;

            // Realized savings: every time price was at or below target
            if (target > 0 && p <= target) {
                const estimatedShelf = target * 1.4;
                totalRealizedSavings += Math.max(0, estimatedShelf - p);
                itemsBoughtAtTarget++;
            }

            const month = h.date.substring(0, 7); // YYYY-MM
            if (!priceIndexByMonth[month]) priceIndexByMonth[month] = { sum: 0, count: 0 };
            priceIndexByMonth[month].sum += p;
            priceIndexByMonth[month].count++;
        });

        // Also accumulate from scrape_history (current prices) into the current month
        // so today's snapshot is always included
        const sh = item.scrape_history || [];
        sh.forEach(h => {
            if (!h.price || h.price > 1000) return;
            const month = h.date.substring(0, 7);
            if (!priceIndexByMonth[month]) priceIndexByMonth[month] = { sum: 0, count: 0 };
            // Only add if this day isn't already covered by price_history
            const alreadyCovered = ph.some(p2 => p2.date === h.date);
            if (!alreadyCovered) {
                priceIndexByMonth[month].sum += h.price;
                priceIndexByMonth[month].count++;
            }
        });
    });

    // 2. Category Split and Brand Premium from current live prices
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
    });

    // 3. Efficiency — % of tracked items currently at or below their target price
    //    (more meaningful than last_purchased which barely has any data)
    const itemsWithTargets = _data.filter(i => (i.target || 0) > 0).length;
    const itemsAtTarget = _data.filter(i => {
        const ep = i.eff_price || i.price || 0;
        return (i.target || 0) > 0 && ep <= i.target && !i.price_unavailable;
    }).length;
    const efficiency = itemsWithTargets > 0 ? (itemsAtTarget / itemsWithTargets) * 100 : 0;

    // 4. Total historical savings: add was_price-based savings for current specials
    _data.forEach(item => {
        const shelf = item.price || 0;
        if (item.on_special && item.was_price && item.was_price > shelf) {
            totalRealizedSavings += (item.was_price - shelf);
        }
    });

    document.getElementById('analytic-savings-val').textContent = `$${totalRealizedSavings.toFixed(2)}`;
    document.getElementById('analytic-efficiency-val').textContent = `${efficiency.toFixed(0)}% (${itemsAtTarget}/${itemsWithTargets} items at target)`;

    // Charts
    const spendingCtx = document.getElementById('spending-chart')?.getContext('2d');
    const categoryCtx = document.getElementById('category-chart')?.getContext('2d');

    if (spendingCtx) {
        const sortedDates = Object.keys(priceIndexByMonth).sort();
        const chartData = sortedDates.map(d => priceIndexByMonth[d].sum / priceIndexByMonth[d].count);

        // Second dataset: count how many distinct items hit their target each month
        const monthSpecialsCount = {};
        const monthItemCount = {};
        _data.forEach(item => {
            const ph = item.price_history || [];
            const tgt = item.target || 0;
            const seenMonths = new Set();
            ph.forEach(h => {
                const m = h.date.substring(0, 7);
                if (!monthItemCount[m]) monthItemCount[m] = new Set();
                monthItemCount[m].add(item.name);
                if (tgt > 0 && h.price <= tgt) {
                    if (!monthSpecialsCount[m]) monthSpecialsCount[m] = new Set();
                    monthSpecialsCount[m].add(item.name);
                }
            });
        });
        const specialsRateLine = sortedDates.map(m => {
            const total = monthItemCount[m] ? monthItemCount[m].size : 0;
            const atTarget = monthSpecialsCount[m] ? monthSpecialsCount[m].size : 0;
            return total > 0 ? parseFloat(((atTarget / total) * 100).toFixed(1)) : 0;
        });

        // Human-readable month labels e.g. "Dec '25"
        const MONTH_SHORT = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        const niceLabels = sortedDates.map(d => {
            const [yr, mo] = d.split('-');
            return `${MONTH_SHORT[parseInt(mo) - 1]} '${yr.slice(2)}`;
        });

        // Destroy existing chart if any
        if (window.mySpendingChart) window.mySpendingChart.destroy();
        
        window.mySpendingChart = new Chart(spendingCtx, {
            type: 'line',
            data: {
                labels: niceLabels,
                datasets: [
                    {
                        label: 'Avg Item Price ($)',
                        data: chartData,
                        borderColor: '#6366f1',
                        backgroundColor: 'rgba(99, 102, 241, 0.08)',
                        fill: true,
                        tension: 0.4,
                        borderWidth: 3,
                        pointRadius: 5,
                        pointBackgroundColor: '#6366f1',
                        yAxisID: 'yPrice',
                    },
                    {
                        label: 'Items at Target (%)',
                        data: specialsRateLine,
                        borderColor: '#10b981',
                        backgroundColor: 'rgba(16, 185, 129, 0.05)',
                        fill: false,
                        tension: 0.4,
                        borderWidth: 2,
                        borderDash: [5, 4],
                        pointRadius: 4,
                        pointBackgroundColor: '#10b981',
                        yAxisID: 'yPct',
                    }
                ]
            },
            options: { 
                responsive: true, 
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { labels: { color: '#9ca3af', padding: 20, usePointStyle: true } },
                    tooltip: {
                        callbacks: {
                            label: (ctx) => ctx.datasetIndex === 0
                                ? `Avg Price: $${ctx.parsed.y.toFixed(2)}`
                                : `At Target: ${ctx.parsed.y.toFixed(1)}%`
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { color: 'rgba(255,255,255,0.05)' },
                        ticks: { color: '#9ca3af', font: { size: 11 } }
                    },
                    yPrice: {
                        type: 'linear',
                        position: 'left',
                        beginAtZero: false,
                        grid: { color: 'rgba(255,255,255,0.05)' },
                        ticks: { callback: val => '$' + val.toFixed(2), color: '#818cf8' }
                    },
                    yPct: {
                        type: 'linear',
                        position: 'right',
                        min: 0,
                        max: 100,
                        grid: { drawOnChartArea: false },
                        ticks: { callback: val => val + '%', color: '#34d399' }
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

    // ── New Analytics Widgets ──────────────────────────────────────────────
    renderSavingsGauge();
    renderWeeklySavings();
    renderCategoryInflation();
    renderDealHeatmap();
    renderVolatilityLeaderboard();
    renderBestTimeToBuy();
    renderPantryHealthScore();
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
                        <span class="name">${displayName(item.name)}</span>
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

    // Destroy existing Chart instance on this canvas before creating a new one
    // to prevent memory leaks on repeated renderDashboard() calls.
    if (_sparklineCharts[containerId]) {
        try { _sparklineCharts[containerId].destroy(); } catch (_) {}
        delete _sparklineCharts[containerId];
    }

    const color = storeClass === 'woolworths' ? '#10b981' : '#ef4444';
    
    // Sort history by date just in case
    const sorted = [...historyData].sort((a, b) => new Date(a.date) - new Date(b.date));
    
    // Take up to last 14 data points
    const recent = sorted.slice(-14);
    
    const labels = recent.map(h => h.date);
    const data = recent.map(h => h.price);

    _sparklineCharts[containerId] = new Chart(canvas, {
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

// ─── WEDNESDAY BANNER ────────────────────────────────────────────────────────
function renderWednesdayBanner() {
    const tueBanner = document.getElementById('wednesday-banner');
    const wedBanner = document.getElementById('wednesday-live-banner');
    if (!tueBanner || !wedBanner) return;

    const day = new Date().getDay(); // 0=Sun, 2=Tue, 3=Wed
    tueBanner.classList.toggle('hidden', day !== 2);   // Tuesday
    wedBanner.classList.toggle('hidden', day !== 3);   // Wednesday
}

// ─── TOP 5 DEALS THIS WEEK ───────────────────────────────────────────────────
function renderTop5Deals() {
    const container = document.getElementById('top5-list');
    if (!container) return;

    // Rank by savings % — store specials first, then target-based
    const deals = _data
        .filter(item => {
            const ep = item.eff_price || item.price || 0;
            return ep > 0 && !item.price_unavailable && (
                item.on_special || ((item.target || 0) > 0 && ep <= item.target)
            );
        })
        .map(item => {
            const ep = item.eff_price || item.price;
            const shelf = item.price || ep;
            const ref = item.was_price && item.was_price > shelf ? item.was_price : (item.target || ep);
            const savePct = ref > shelf ? Math.round((1 - shelf / ref) * 100) : 0;
            return { ...item, _ep: ep, _savePct: savePct };
        })
        .filter(i => i._savePct > 0)
        .sort((a, b) => b._savePct - a._savePct)
        .slice(0, 5);

    if (deals.length === 0) {
        container.innerHTML = '<p style="color:var(--text-muted);font-size:12px;text-align:center;padding:8px 0;">No deals detected yet.</p>';
        return;
    }

    container.innerHTML = deals.map((item, i) => {
        const medal = ['🥇','🥈','🥉','4️⃣','5️⃣'][i];
        const storeColor = item.store === 'coles' ? '#e2231a' : '#00b14f';
        return `
            <div class="top5-row" onclick="document.getElementById('dashboard-search').value='${item.name.substring(0,15)}'; _searchText='${item.name.substring(0,15).toLowerCase()}'; _currentPage=1; renderDashboard();" title="${displayName(item.name)}">
                <span class="top5-medal">${medal}</span>
                <div class="top5-info">
                    <div class="top5-name">${(() => { const dn = displayName(item.name); return dn.length > 28 ? dn.substring(0,28)+'…' : dn; })()}</div>
                    <div class="top5-price" style="color:${storeColor};">$${item._ep.toFixed(2)}</div>
                </div>
                <span class="top5-save">-${item._savePct}%</span>
            </div>
        `;
    }).join('');
}

// ─── COPY SHOPPING LIST ───────────────────────────────────────────────────────
function copyShoppingList() {
    if (_shoppingList.length === 0) return;

    const today = new Date().toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short' });
    const lines = [`🛒 Shopping List — ${today}`, ''];

    // Group by store
    const woolies = _shoppingList.filter(i => i.store === 'woolworths');
    const coles = _shoppingList.filter(i => i.store === 'coles');

    if (woolies.length) {
        lines.push('🟢 Woolworths');
        woolies.forEach(i => {
            const special = i.on_special ? ' 🏷️' : '';
            lines.push(`  ${i.qty > 1 ? i.qty + 'x ' : ''}${displayName(i.name)}${special} — $${((i.price || 0) * i.qty).toFixed(2)}`);
        });
        lines.push('');
    }
    if (coles.length) {
        lines.push('🔴 Coles');
        coles.forEach(i => {
            const special = i.on_special ? ' 🏷️' : '';
            lines.push(`  ${i.qty > 1 ? i.qty + 'x ' : ''}${displayName(i.name)}${special} — $${((i.price || 0) * i.qty).toFixed(2)}`);
        });
        lines.push('');
    }

    const total = _shoppingList.reduce((s, i) => s + (i.price || 0) * i.qty, 0);
    lines.push(`Total: ~$${total.toFixed(2)}`);
    lines.push('');
    lines.push('https://kuschikuschbert.github.io/wooliesbot/');

    navigator.clipboard.writeText(lines.join('\n')).then(() => {
        const btn = document.getElementById('copy-list-btn');
        if (btn) {
            const orig = btn.innerHTML;
            btn.innerHTML = '<i data-feather="check"></i> Copied!';
            btn.style.background = 'var(--woolies-green)';
            feather.replace();
            setTimeout(() => { btn.innerHTML = orig; btn.style.background = ''; feather.replace(); }, 2000);
        }
    }).catch(() => alert('Copy failed — use a secure (HTTPS) connection.'));
}

// ─── PRICE DROP ALERTS (IN-PAGE TOAST) ────────────────────────────────────────
const _alertedItems = new Set(JSON.parse(localStorage.getItem('alertedDrops') || '[]'));

function checkPriceDropAlerts() {
    const newDrops = [];
    _data.forEach(item => {
        const ep = item.eff_price || item.price || 0;
        const isSpecial = item.on_special || ((item.target || 0) > 0 && ep <= item.target && !item.price_unavailable);
        if (isSpecial && !_alertedItems.has(item.name)) {
            newDrops.push(item);
            _alertedItems.add(item.name);
        }
        // Clear alert if item is no longer special (so it can alert again next time)
        if (!isSpecial && _alertedItems.has(item.name)) {
            _alertedItems.delete(item.name);
        }
    });

    localStorage.setItem('alertedDrops', JSON.stringify([..._alertedItems]));

    if (newDrops.length > 0) {
        showPriceDropToast(newDrops);
    }
}

function showPriceDropToast(items) {
    // Remove any existing toast
    document.getElementById('price-drop-toast')?.remove();

    const toast = document.createElement('div');
    toast.id = 'price-drop-toast';
    toast.className = 'price-drop-toast';

    const names = items.slice(0, 3).map(i => displayName(i.name).split(' ').slice(0, 2).join(' ')).join(', ');
    const more = items.length > 3 ? ` +${items.length - 3} more` : '';

    toast.innerHTML = `
        <span style="font-size:18px;">🔥</span>
        <div style="flex:1;">
            <div style="font-weight:700;font-size:13px;">New deals detected!</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px;">${names}${more}</div>
        </div>
        <button onclick="this.parentElement.remove()" style="background:none;border:none;color:var(--text-muted);cursor:pointer;padding:4px;font-size:16px;">×</button>
    `;

    document.body.appendChild(toast);
    // Auto-dismiss after 8 seconds
    setTimeout(() => toast.remove(), 8000);
}

// ═══════════════════════════════════════════════════════════════════════════════
// NEW ANALYTICS WIDGETS
// ═══════════════════════════════════════════════════════════════════════════════

// ─── 1. LIVE SAVINGS GAUGE ───────────────────────────────────────────────────
function renderSavingsGauge() {
    const container = document.getElementById('savings-gauge-container');
    if (!container) return;

    // Current savings from active specials (was_price - eff_price)
    let currentSavings = 0;
    let potentialSavings = 0;
    let specialCount = 0;

    _data.forEach(item => {
        const ep = item.eff_price || item.price || 0;
        const shelf = item.price || ep;
        if (ep <= 0 || item.price_unavailable) return;

        // Compare was_price to shelf price (not eff_price) to avoid unit mismatch
        if (item.on_special && item.was_price && item.was_price > shelf) {
            currentSavings += (item.was_price - shelf);
            potentialSavings += (item.was_price - shelf);
            specialCount++;
        } else if (item.target > 0 && ep <= item.target) {
            // Target-based saving
            const saving = item.target - ep;
            currentSavings += saving;
            potentialSavings += saving;
            specialCount++;
        }

        // Add non-special items' potential (estimated 15% saving if they go on special)
        if (!item.on_special && item.target > 0) {
            potentialSavings += Math.max(0, ep - item.target);
        }
    });

    const pct = potentialSavings > 0 ? Math.min((currentSavings / Math.max(potentialSavings, currentSavings)) * 100, 100) : 0;
    const radius = 54;
    const circ = 2 * Math.PI * radius;
    const dash = (pct / 100) * circ;
    const gap = circ - dash;

    // Colour: red 0-30, amber 30-60, green 60+
    const color = pct >= 60 ? '#10b981' : pct >= 30 ? '#f59e0b' : '#6366f1';

    container.innerHTML = `
        <div class="gauge-wrap">
            <svg class="gauge-svg" viewBox="0 0 120 120">
                <circle cx="60" cy="60" r="${radius}" fill="none" stroke="rgba(255,255,255,0.05)" stroke-width="12"/>
                <circle cx="60" cy="60" r="${radius}" fill="none" stroke="${color}"
                    stroke-width="12" stroke-linecap="round"
                    stroke-dasharray="${dash} ${gap}"
                    stroke-dashoffset="${circ * 0.25}"
                    style="filter: drop-shadow(0 0 8px ${color}); transition: stroke-dasharray 1s ease;">
                </circle>
                <text x="60" y="55" text-anchor="middle" fill="white" font-size="16" font-weight="800" font-family="Outfit,sans-serif">$${currentSavings.toFixed(0)}</text>
                <text x="60" y="72" text-anchor="middle" fill="#9ca3af" font-size="9" font-family="Inter,sans-serif">SAVED NOW</text>
            </svg>
            <div class="gauge-stats">
                <div class="gauge-stat">
                    <span class="gauge-stat-val" style="color:${color}">${pct.toFixed(0)}%</span>
                    <span class="gauge-stat-label">Capture Rate</span>
                </div>
                <div class="gauge-stat">
                    <span class="gauge-stat-val">${specialCount}</span>
                    <span class="gauge-stat-label">Active Deals</span>
                </div>
            </div>
            <p class="insight-tip" style="margin-top:1rem;">
                ${pct >= 60 ? '🔥 Great week! You\'re capturing most of the available savings.' :
                  pct >= 30 ? '⚡ Some good deals active. Check the Deals tab for more.' :
                  '💡 Quiet on deals — set more targets to get alerted when prices drop.'}
            </p>
        </div>
    `;
}

// ─── 2. WEEKLY SAVINGS SUMMARY ───────────────────────────────────────────────
function renderWeeklySavings() {
    const container = document.getElementById('weekly-savings-container');
    if (!container) return;

    let totalSaved = 0;
    let totalWouldCost = 0;
    const dealItems = [];

    _data.forEach(item => {
        const shelf = item.price || 0;
        if (item.on_special && item.was_price && item.was_price > shelf && shelf > 0) {
            const saved = item.was_price - shelf;
            totalSaved += saved;
            totalWouldCost += item.was_price;
            dealItems.push({ name: item.name, saved, savePct: Math.round((saved / item.was_price) * 100), store: item.store });
        }
    });

    const topDeals = dealItems.sort((a, b) => b.saved - a.saved).slice(0, 4);
    const pct = totalWouldCost > 0 ? ((totalSaved / totalWouldCost) * 100).toFixed(1) : 0;

    container.innerHTML = `
        <div class="weekly-savings-number">$${totalSaved.toFixed(2)}</div>
        <div class="weekly-savings-sub">saved this cycle vs normal prices · <strong>${pct}% off</strong></div>
        <div class="weekly-deals-list">
            ${topDeals.map(d => `
                <div class="weekly-deal-row">
                    <span class="wdr-name">${(() => { const dn = displayName(d.name); return dn.length > 26 ? dn.slice(0, 26) + '…' : dn; })()}</span>
                    <span class="wdr-save">-${d.savePct}% ($${d.saved.toFixed(2)})</span>
                </div>
            `).join('')}
            ${topDeals.length === 0 ? '<p style="color:var(--text-muted);font-size:12px;text-align:center;padding:1rem 0;">No store-confirmed specials with was_price data yet.</p>' : ''}
        </div>
    `;
}

// ─── 3. CATEGORY PRICE INFLATION ─────────────────────────────────────────────
function renderCategoryInflation() {
    const container = document.getElementById('category-inflation-container');
    if (!container) return;

    const now = new Date();
    const cutoff60 = new Date(now.getTime() - 60 * 24 * 60 * 60 * 1000); // 60 days ago
    const cutoff30 = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000); // 30 days ago

    // Per-category: avg price in [0-30 days ago] vs [30-60 days ago]
    const catRecent = {}; // recent 30d
    const catOld    = {}; // 30-60d

    _data.forEach(item => {
        const cat = item.type || 'other';
        const ph = item.price_history || [];
        ph.forEach(h => {
            const d = new Date(h.date);
            const p = parseFloat(h.price);
            if (!p || p <= 0 || p > 500) return;
            if (d >= cutoff30) {
                if (!catRecent[cat]) catRecent[cat] = [];
                catRecent[cat].push(p);
            } else if (d >= cutoff60) {
                if (!catOld[cat]) catOld[cat] = [];
                catOld[cat].push(p);
            }
        });
    });

    const CAT_EMOJI = {
        produce:'🥬', meat:'🥩', dairy:'🧀', beverages:'🥤', snacks:'🍫',
        pantry:'🫙', bakery:'🍞', frozen:'🧊', household:'🧹',
        personal_care:'🪥', pet:'🐾', other:'📦'
    };

    const rows = [];
    Object.keys(catRecent).forEach(cat => {
        if (!catOld[cat] || catOld[cat].length < 2) return;
        const avgRecent = catRecent[cat].reduce((a, b) => a + b, 0) / catRecent[cat].length;
        const avgOld    = catOld[cat].reduce((a, b) => a + b, 0) / catOld[cat].length;
        const change    = ((avgRecent - avgOld) / avgOld) * 100;
        rows.push({ cat, change, avgRecent, avgOld });
    });

    if (rows.length === 0) {
        // Fallback: use current prices vs targets to show relative position
        const catData = {};
        _data.forEach(item => {
            const cat = item.type || 'other';
            const ep = item.eff_price || item.price || 0;
            const tgt = item.target || 0;
            if (ep > 0 && tgt > 0) {
                if (!catData[cat]) catData[cat] = [];
                catData[cat].push(((ep - tgt) / tgt) * 100);
            }
        });
        Object.entries(catData).forEach(([cat, changes]) => {
            if (changes.length < 2) return;
            const avg = changes.reduce((a, b) => a + b, 0) / changes.length;
            rows.push({ cat, change: avg, avgRecent: 0, avgOld: 0, isTargetBased: true });
        });
    }

    rows.sort((a, b) => Math.abs(b.change) - Math.abs(a.change));

    const maxChange = Math.max(...rows.map(r => Math.abs(r.change)), 1);

    container.innerHTML = rows.slice(0, 10).map(r => {
        const pct = r.change;
        const barWidth = Math.min(Math.abs(pct) / maxChange * 100, 100);
        const up = pct > 0;
        const label = r.isTargetBased ? `${pct > 0 ? '+' : ''}${pct.toFixed(1)}% above target avg` :
                      `${pct > 0 ? '↑' : '↓'} ${Math.abs(pct).toFixed(1)}% vs 60d ago`;
        const emoji = CAT_EMOJI[r.cat] || '📦';
        return `
            <div class="inflation-row">
                <div class="inflation-cat">${emoji} ${r.cat.replace('_', ' ')}</div>
                <div class="inflation-bar-wrap">
                    <div class="inflation-bar ${up ? 'up' : 'down'}" style="width:${barWidth}%"></div>
                </div>
                <div class="inflation-label ${up ? 'up' : 'down'}">${label}</div>
            </div>
        `;
    }).join('');

    if (rows.length === 0) {
        container.innerHTML = '<p style="color:var(--text-muted);font-size:13px;">Not enough price history yet to compute inflation trends. Data will populate as the bot runs daily.</p>';
    }
}

// ─── 4. DEAL HEAT MAP ────────────────────────────────────────────────────────
function renderDealHeatmap() {
    const container = document.getElementById('deal-heatmap-container');
    if (!container) return;

    const CAT_EMOJI = {
        produce:'🥬', meat:'🥩', dairy:'🧀', beverages:'🥤', snacks:'🍫',
        pantry:'🫙', bakery:'🍞', frozen:'🧊', household:'🧹',
        personal_care:'🪥', pet:'🐾', other:'📦'
    };

    // Build per-category, per-store stats
    const cats = [...new Set(_data.map(i => i.type || 'other'))].filter(c => c);
    const heatData = {};

    cats.forEach(cat => {
        heatData[cat] = { woolworths: { specials: 0, total: 0, savings: 0 }, coles: { specials: 0, total: 0, savings: 0 } };
    });

    _data.forEach(item => {
        const cat = item.type || 'other';
        const store = item.store;
        if (!store || store === 'none' || !heatData[cat]?.[store]) return;

        const ep = item.eff_price || item.price || 0;
        heatData[cat][store].total++;

        const shelf = item.price || ep;
        const isSpecial = item.on_special || (item.target > 0 && ep <= item.target && !item.price_unavailable);
        if (isSpecial) {
            heatData[cat][store].specials++;
            const ref = item.was_price && item.was_price > shelf ? item.was_price : (item.target || shelf);
            heatData[cat][store].savings += Math.max(0, ref - shelf);
        }
    });

    // Find max specials for scale
    let maxSpecials = 1;
    cats.forEach(cat => {
        ['woolworths', 'coles'].forEach(s => {
            maxSpecials = Math.max(maxSpecials, heatData[cat]?.[s]?.specials || 0);
        });
    });

    const sortedCats = cats.sort((a, b) => {
        const aTotal = (heatData[a]?.woolworths?.specials || 0) + (heatData[a]?.coles?.specials || 0);
        const bTotal = (heatData[b]?.woolworths?.specials || 0) + (heatData[b]?.coles?.specials || 0);
        return bTotal - aTotal;
    });

    container.innerHTML = `
        <div class="heatmap-grid">
            <div class="heatmap-header-col"></div>
            <div class="heatmap-store-header woolies-head">🟢 Woolworths</div>
            <div class="heatmap-store-header coles-head">🔴 Coles</div>
            ${sortedCats.map(cat => {
                const w = heatData[cat]?.woolworths || { specials: 0, total: 0, savings: 0 };
                const c = heatData[cat]?.coles || { specials: 0, total: 0, savings: 0 };
                const wIntensity = maxSpecials > 0 ? (w.specials / maxSpecials) : 0;
                const cIntensity = maxSpecials > 0 ? (c.specials / maxSpecials) : 0;
                const wWinner = w.specials >= c.specials;

                return `
                    <div class="heatmap-label">${CAT_EMOJI[cat] || '📦'} ${cat.replace('_', ' ')}</div>
                    <div class="heatmap-cell ${wWinner && w.specials > 0 ? 'woolies-winner' : ''}" style="--intensity: ${wIntensity}">
                        <div class="heatmap-cell-count">${w.specials}</div>
                        <div class="heatmap-cell-sub">of ${w.total} on special</div>
                        ${w.savings > 0.05 ? `<div class="heatmap-savings">$${w.savings.toFixed(2)} off</div>` : ''}
                    </div>
                    <div class="heatmap-cell ${!wWinner && c.specials > 0 ? 'coles-winner' : ''}" style="--intensity: ${cIntensity}">
                        <div class="heatmap-cell-count">${c.specials}</div>
                        <div class="heatmap-cell-sub">of ${c.total} on special</div>
                        ${c.savings > 0.05 ? `<div class="heatmap-savings">$${c.savings.toFixed(2)} off</div>` : ''}
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

// ─── 5. VOLATILITY LEADERBOARD ───────────────────────────────────────────────
function renderVolatilityLeaderboard() {
    const container = document.getElementById('volatility-leaderboard-container');
    if (!container) return;

    // Re-compute volatility from price_history (richer source than scrape_history)
    const volScores = [];

    _data.forEach(item => {
        const ph = item.price_history || [];
        if (ph.length < 3) return;

        const prices = ph.map(h => parseFloat(h.price)).filter(p => p > 0 && p < 500);
        if (prices.length < 3) return;

        const avg = prices.reduce((a, b) => a + b, 0) / prices.length;
        const variance = prices.reduce((a, b) => a + Math.pow(b - avg, 2), 0) / prices.length;
        const stdDev = Math.sqrt(variance);
        const vol = (stdDev / avg) * 100;

        const ep = item.eff_price || item.price || 0;
        const isOnSpecial = item.on_special || (item.target > 0 && ep <= item.target);
        const minPrice = Math.min(...prices);
        const maxPrice = Math.max(...prices);

        volScores.push({
            name: item.name,
            vol,
            avg: avg.toFixed(2),
            min: minPrice.toFixed(2),
            max: maxPrice.toFixed(2),
            store: item.store,
            isOnSpecial,
            ep
        });
    });

    // Fill with items that have at least some history if not enough
    if (volScores.length < 5) {
        _data.forEach(item => {
            if (volScores.find(v => v.name === item.name)) return;
            const ph = item.price_history || [];
            if (ph.length < 2) return;
            const prices = ph.map(h => parseFloat(h.price)).filter(p => p > 0);
            if (prices.length < 2) return;
            const avg = prices.reduce((a, b) => a + b, 0) / prices.length;
            const vol = Math.abs(prices[0] - prices[prices.length - 1]) / avg * 100;
            const ep = item.eff_price || item.price || 0;
            volScores.push({
                name: item.name, vol, avg: avg.toFixed(2),
                min: Math.min(...prices).toFixed(2), max: Math.max(...prices).toFixed(2),
                store: item.store, isOnSpecial: false, ep
            });
        });
    }

    volScores.sort((a, b) => b.vol - a.vol);
    const top = volScores.slice(0, 12);
    const maxVol = top[0]?.vol || 1;

    if (top.length === 0) {
        container.innerHTML = '<p style="color:var(--text-muted);font-size:13px;">Build up price history (3+ data points per item) to see volatility rankings.</p>';
        return;
    }

    container.innerHTML = `
        <div class="vol-table">
            ${top.map((item, i) => {
                const barW = (item.vol / maxVol) * 100;
                const storeColor = item.store === 'woolworths' ? '#10b981' : '#ef4444';
                const volClass = item.vol > 15 ? 'high' : item.vol > 8 ? 'med' : 'low';
                return `
                    <div class="vol-row" onclick="openItemDeepdive('${item.name.replace(/'/g, "\\'")}')">
                        <div class="vol-rank">#${i + 1}</div>
                        <div class="vol-info">
                            <div class="vol-name">
                                ${(() => { const dn = displayName(item.name); return dn.length > 32 ? dn.slice(0, 32) + '…' : dn; })()}
                                ${item.isOnSpecial ? '<span class="vol-special-badge">🔥 ON SPECIAL</span>' : ''}
                            </div>
                            <div class="vol-bar-wrap">
                                <div class="vol-bar ${volClass}" style="width:${barW}%"></div>
                            </div>
                        </div>
                        <div class="vol-meta">
                            <div class="vol-score ${volClass}">${item.vol.toFixed(0)}%</div>
                            <div class="vol-range">$${item.min}–$${item.max}</div>
                        </div>
                    </div>
                `;
            }).join('')}
        </div>
        <p class="insight-tip">Click any item to see its full price history chart.</p>
    `;
}

// ─── 6. BEST TIME TO BUY ─────────────────────────────────────────────────────
function renderBestTimeToBuy() {
    const container = document.getElementById('best-time-container');
    if (!container) return;

    // Month-bucket all price_history entries by category
    const catMonthPrices = {}; // cat -> { month(0-11) -> [prices] }

    _data.forEach(item => {
        const cat = item.type || 'other';
        const ph = item.price_history || [];
        ph.forEach(h => {
            const d = new Date(h.date);
            const p = parseFloat(h.price);
            if (!p || p <= 0 || p > 500) return;
            const m = d.getMonth(); // 0-11
            if (!catMonthPrices[cat]) catMonthPrices[cat] = {};
            if (!catMonthPrices[cat][m]) catMonthPrices[cat][m] = [];
            catMonthPrices[cat][m].push(p);
        });
    });

    const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const CAT_EMOJI = {
        produce:'🥬', meat:'🥩', dairy:'🧀', beverages:'🥤', snacks:'🍫',
        pantry:'🫙', bakery:'🍞', frozen:'🧊', household:'🧹',
        personal_care:'🪥', pet:'🐾', other:'📦'
    };

    const results = [];
    Object.entries(catMonthPrices).forEach(([cat, byMonth]) => {
        const monthAvgs = Object.entries(byMonth)
            .filter(([, prices]) => prices.length >= 2)
            .map(([m, prices]) => ({
                month: parseInt(m),
                avg: prices.reduce((a, b) => a + b, 0) / prices.length
            }));
        if (monthAvgs.length < 2) return;
        monthAvgs.sort((a, b) => a.avg - b.avg);
        const cheapest = monthAvgs[0];
        const mostExpensive = monthAvgs[monthAvgs.length - 1];
        const saving = ((mostExpensive.avg - cheapest.avg) / mostExpensive.avg * 100).toFixed(0);
        results.push({ cat, cheapestMonth: cheapest.month, saving, avg: cheapest.avg });
    });

    if (results.length === 0) {
        container.innerHTML = `
            <div class="best-time-empty">
                <div style="font-size:32px;margin-bottom:0.5rem;">📅</div>
                <p>Price history across multiple months is building up. Check back after a few weeks of data collection.</p>
            </div>
        `;
        return;
    }

    results.sort((a, b) => parseInt(b.saving) - parseInt(a.saving));

    container.innerHTML = `
        <div class="best-time-list">
            ${results.slice(0, 8).map(r => `
                <div class="best-time-row">
                    <span class="bt-cat">${CAT_EMOJI[r.cat] || '📦'} ${r.cat.replace('_', ' ')}</span>
                    <span class="bt-month">${MONTHS[r.cheapestMonth]}</span>
                    <span class="bt-saving">saves ~${r.saving}%</span>
                </div>
            `).join('')}
        </div>
    `;
}

// ─── 7. PANTRY HEALTH SCORE ──────────────────────────────────────────────────
function renderPantryHealthScore() {
    const container = document.getElementById('pantry-health-container');
    if (!container) return;

    const total = _data.length;
    if (total === 0) return;

    // Metrics
    const lowStockCount = _data.filter(i => i.stock === 'low').length;
    const medStockCount = _data.filter(i => i.stock === 'medium').length;
    const withTarget = _data.filter(i => (i.target || 0) > 0).length;
    const highConf = _data.filter(i => i.target_confidence === 'high').length;
    const specials = _data.filter(i => {
        const ep = i.eff_price || i.price || 0;
        return i.on_special || (i.target > 0 && ep <= i.target && !i.price_unavailable);
    }).length;

    // Score components (0-100 each, weighted)
    const stockScore    = Math.max(0, 100 - (lowStockCount / total) * 200 - (medStockCount / total) * 50);
    const targetCovScore = (withTarget / total) * 100;
    const confScore     = (highConf / total) * 100;
    const dealScore     = Math.min((specials / Math.max(total * 0.15, 1)) * 100, 100);

    const overallScore = Math.round(stockScore * 0.35 + targetCovScore * 0.25 + confScore * 0.20 + dealScore * 0.20);
    const clampedScore = Math.min(Math.max(overallScore, 0), 100);

    const grade = clampedScore >= 80 ? { label: 'Excellent', color: '#10b981', icon: '🏆' }
                : clampedScore >= 60 ? { label: 'Good', color: '#6366f1', icon: '✅' }
                : clampedScore >= 40 ? { label: 'Fair', color: '#f59e0b', icon: '⚡' }
                : { label: 'Needs Attention', color: '#ef4444', icon: '⚠️' };

    const metrics = [
        { label: 'Stock Status', score: Math.round(stockScore), icon: '📦',
          hint: `${lowStockCount} items low, ${medStockCount} medium` },
        { label: 'Target Coverage', score: Math.round(targetCovScore), icon: '🎯',
          hint: `${withTarget} of ${total} items have targets` },
        { label: 'Target Confidence', score: Math.round(confScore), icon: '🔬',
          hint: `${highConf} high-confidence targets` },
        { label: 'Deal Capture', score: Math.round(dealScore), icon: '🔥',
          hint: `${specials} active deals right now` },
    ];

    container.innerHTML = `
        <div class="health-score-layout">
            <div class="health-score-main">
                <div class="health-ring-wrap">
                    <svg viewBox="0 0 120 120" class="health-ring-svg">
                        <circle cx="60" cy="60" r="50" fill="none" stroke="rgba(255,255,255,0.05)" stroke-width="10"/>
                        <circle cx="60" cy="60" r="50" fill="none" stroke="${grade.color}"
                            stroke-width="10" stroke-linecap="round"
                            stroke-dasharray="${(clampedScore / 100) * 314} 314"
                            stroke-dashoffset="78.5"
                            style="filter:drop-shadow(0 0 10px ${grade.color}); transition: stroke-dasharray 1.2s ease;">
                        </circle>
                        <text x="60" y="54" text-anchor="middle" fill="white" font-size="26" font-weight="800" font-family="Outfit,sans-serif">${clampedScore}</text>
                        <text x="60" y="70" text-anchor="middle" fill="#9ca3af" font-size="9" font-family="Inter,sans-serif">/ 100</text>
                    </svg>
                </div>
                <div class="health-grade">
                    <span class="health-grade-icon">${grade.icon}</span>
                    <span class="health-grade-label" style="color:${grade.color}">${grade.label}</span>
                </div>
            </div>
            <div class="health-metrics">
                ${metrics.map(m => {
                    const mColor = m.score >= 70 ? '#10b981' : m.score >= 45 ? '#f59e0b' : '#ef4444';
                    return `
                        <div class="health-metric-row">
                            <div class="health-metric-icon">${m.icon}</div>
                            <div class="health-metric-info">
                                <div class="health-metric-label">${m.label}</div>
                                <div class="health-metric-hint">${m.hint}</div>
                                <div class="health-metric-bar">
                                    <div class="health-metric-fill" style="width:${m.score}%;background:${mColor};box-shadow:0 0 8px ${mColor}40"></div>
                                </div>
                            </div>
                            <div class="health-metric-score" style="color:${mColor}">${m.score}</div>
                        </div>
                    `;
                }).join('')}
            </div>
        </div>
    `;
}

// ─── 8. ITEM DEEP-DIVE MODAL ─────────────────────────────────────────────────
let _deepdiveChart = null;

function openItemDeepdive(itemName) {
    const item = _data.find(i => i.name === itemName);
    if (!item) return;

    // Remove existing modal
    document.getElementById('deepdive-modal')?.remove();

    const ph = item.price_history || [];
    const sorted = [...ph].sort((a, b) => new Date(a.date) - new Date(b.date));
    const vol = _volatility[itemName] || 0;
    const ep = item.eff_price || item.price || 0;
    const isOnSpecial = item.on_special || (item.target > 0 && ep <= item.target);
    const storeColor = item.store === 'woolworths' ? '#10b981' : '#ef4444';

    const modal = document.createElement('div');
    modal.id = 'deepdive-modal';
    modal.className = 'deepdive-overlay';
    modal.onclick = (e) => { if (e.target === modal) closeItemDeepdive(); };

    modal.innerHTML = `
        <div class="deepdive-panel">
            <div class="deepdive-header">
                <div>
                    <h3 class="deepdive-title">${displayName(item.name)}</h3>
                    <div class="deepdive-meta">
                        <span class="store-badge ${item.store}" style="margin-top:0;">${item.store === 'woolworths' ? 'Woolies' : 'Coles'}</span>
                        ${isOnSpecial ? '<span class="save-badge">ON SPECIAL</span>' : ''}
                        ${item.target_confidence ? `<span class="confidence-badge ${item.target_confidence}">
                            ${item.target_confidence === 'high' ? '🟢' : item.target_confidence === 'medium' ? '🟡' : '🔴'} ${item.target_confidence} conf.
                        </span>` : ''}
                    </div>
                </div>
                <button onclick="closeItemDeepdive()" class="deepdive-close">
                    <i data-feather="x"></i>
                </button>
            </div>
            <div class="deepdive-stats">
                <div class="dd-stat">
                    <div class="dd-stat-val" style="color:${storeColor}">$${ep.toFixed(2)}</div>
                    <div class="dd-stat-label">Current</div>
                </div>
                ${item.target > 0 ? `<div class="dd-stat">
                    <div class="dd-stat-val">$${item.target.toFixed(2)}</div>
                    <div class="dd-stat-label">Target</div>
                </div>` : ''}
                ${item.was_price ? `<div class="dd-stat">
                    <div class="dd-stat-val" style="color:#f87171;text-decoration:line-through">$${item.was_price.toFixed(2)}</div>
                    <div class="dd-stat-label">Was Price</div>
                </div>` : ''}
                <div class="dd-stat">
                    <div class="dd-stat-val ${vol > 15 ? 'vol-high' : vol > 8 ? 'vol-med' : ''}">${vol.toFixed(0)}%</div>
                    <div class="dd-stat-label">Volatility</div>
                </div>
                <div class="dd-stat">
                    <div class="dd-stat-val">${ph.length}</div>
                    <div class="dd-stat-label">Data Points</div>
                </div>
            </div>
            <div class="deepdive-chart-wrap">
                ${sorted.length > 1 ? '<canvas id="deepdive-canvas"></canvas>' :
                  '<p style="color:var(--text-muted);text-align:center;padding:3rem;font-size:13px;">Not enough price history to chart.<br>At least 2 data points needed.</p>'}
            </div>
            <div class="deepdive-footer">
                <div class="dd-footer-info">
                    <span>${item.type || 'uncategorised'} · ${item.brand || 'unknown brand'}</span>
                    ${item.size ? `<span>Size: ${item.size}</span>` : ''}
                </div>
                <button class="sync-btn" style="padding:10px 20px;width:auto;" onclick="addToList('${item.name.replace(/'/g, "\\'")}'); closeItemDeepdive();">
                    <i data-feather="plus"></i> Add to List
                </button>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
    feather.replace();

    if (sorted.length > 1) {
        const canvas = document.getElementById('deepdive-canvas');
        if (canvas) {
            const ctx = canvas.getContext('2d');
            if (_deepdiveChart) _deepdiveChart.destroy();
            _deepdiveChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: sorted.map(h => h.date),
                    datasets: [{
                        label: 'Price',
                        data: sorted.map(h => h.price),
                        borderColor: storeColor,
                        backgroundColor: storeColor + '20',
                        fill: true,
                        tension: 0.4,
                        borderWidth: 3,
                        pointRadius: 5,
                        pointBackgroundColor: storeColor,
                        pointHoverRadius: 8,
                    },
                    ...(item.target > 0 ? [{
                        label: 'Target',
                        data: sorted.map(() => item.target),
                        borderColor: '#6366f1',
                        borderDash: [6, 4],
                        borderWidth: 2,
                        pointRadius: 0,
                        fill: false,
                    }] : [])
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { labels: { color: '#9ca3af' } },
                        tooltip: { callbacks: { label: ctx => `$${ctx.parsed.y.toFixed(2)}` } }
                    },
                    scales: {
                        x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#9ca3af', maxTicksLimit: 8 } },
                        y: { beginAtZero: false, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#9ca3af', callback: v => '$' + v.toFixed(2) } }
                    }
                }
            });
        }
    }
}

function closeItemDeepdive() {
    document.getElementById('deepdive-modal')?.remove();
    if (_deepdiveChart) { _deepdiveChart.destroy(); _deepdiveChart = null; }
}
