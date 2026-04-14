document.addEventListener('DOMContentLoaded', () => {
    feather.replace();
    initDashboard();
});

let _data = [];
let _history = {};
let _currentFilter = 'all';
let _currentCatFilter = 'all';
let _shopMode = localStorage.getItem('shopMode') || 'weekly'; // 'weekly' or 'big'
let _shoppingList = JSON.parse(localStorage.getItem('shoppingList') || '[]');

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
                    luEl.textContent = parsed.last_updated;
                }
            }
        }
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
    const storeButtons = document.querySelectorAll('.filter-btn');
    storeButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            storeButtons.forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            
            _currentFilter = e.target.dataset.filter;
            renderDashboard();
        });
    });

    const catButtons = document.querySelectorAll('.filter-btn-cat');
    catButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            catButtons.forEach(b => b.classList.remove('active'));
            const target = e.target.closest('.filter-btn-cat');
            if (target) {
                target.classList.add('active');
                _currentCatFilter = target.dataset.cat;
                renderDashboard();
            }
        });
    });

    // Shop Mode Toggle
    const modeLabels = document.querySelectorAll('.mode-label');
    modeLabels.forEach(label => {
        if (label.dataset.mode === _shopMode) label.classList.add('active');
        else label.classList.remove('active');

        label.addEventListener('click', () => {
            _shopMode = label.dataset.mode;
            localStorage.setItem('shopMode', _shopMode);
            modeLabels.forEach(l => l.classList.remove('active'));
            label.classList.add('active');
            renderDashboard();
        });
    });

    // Shopping List Drawer Toggle
    const toggleBtn = document.getElementById('toggle-list-btn');
    const closeBtn = document.getElementById('close-drawer');
    const overlay = document.getElementById('drawer-overlay');
    const drawer = document.getElementById('list-drawer');

    const toggleDrawer = () => {
        drawer.classList.toggle('open');
        overlay.classList.toggle('open');
        if (drawer.classList.contains('open')) renderShoppingList();
    };

    toggleBtn?.addEventListener('click', toggleDrawer);
    closeBtn?.addEventListener('click', toggleDrawer);
    overlay?.addEventListener('click', toggleDrawer);

    // Sync to Keep
    document.getElementById('sync-keep-btn')?.addEventListener('click', async () => {
        const btn = document.getElementById('sync-keep-btn');
        const originalHtml = btn.innerHTML;
        
        try {
            btn.innerHTML = '<i data-feather="loader" class="spin"></i> Starting Sync...';
            feather.replace();
            
            const response = await fetch('http://localhost:5000/sync');
            if (response.ok) {
                btn.innerHTML = '<i data-feather="check"></i> Syncing in Background';
                btn.style.background = 'var(--woolies-green)';
                setTimeout(() => {
                    btn.innerHTML = originalHtml;
                    btn.style.background = '';
                    feather.replace();
                }, 4000);
            } else {
                throw new Error('Server error');
            }
        } catch (e) {
            btn.innerHTML = originalHtml;
            feather.replace();
            alert("Local API not running.\n\nTo enable one-click sync, run 'python3 api.py' in your terminal.");
        }
    });
}

function renderDashboard() {
    renderCountdown();
    renderStats();
    renderEssentials();
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
    document.getElementById('total-items').textContent = _data.length;
    
    let specialsCount = 0;
    let estimatedCart = 0;
    
    _data.forEach(item => {
        const effPrice = item.eff_price || item.price;
        if (effPrice <= item.target && !item.price_unavailable) {
            specialsCount++;
            
            // Apply Shop Mode Multipliers
            let qty = 1;
            if (_shopMode === 'big') {
                if (item.type === 'fresh_protein') qty = 4;
                else if (['pet', 'pantry', 'household', 'freezer'].includes(item.type)) qty = 2;
            }
            
            estimatedCart += (item.price * qty);
        }
    });

    document.getElementById('total-specials').textContent = specialsCount;
    document.getElementById('cart-total').textContent = `$${estimatedCart.toFixed(2)}`;

    // Update Discount Tracker ($500 goal)
    const progressBar = document.getElementById('discount-progress');
    const statusText = document.getElementById('discount-status');
    const goal = 500;
    const progress = Math.min((estimatedCart / goal) * 100, 100);
    
    progressBar.style.width = `${progress}%`;
    if (estimatedCart >= goal) {
        statusText.textContent = `🚀 Target met! Saving ~$${(estimatedCart * 0.1).toFixed(2)}`;
        statusText.style.color = 'var(--woolies-green)';
    } else {
        statusText.textContent = `Add $${(goal - estimatedCart).toFixed(2)} to save 10%`;
        statusText.style.color = 'var(--text-muted)';
    }

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

function createItemCard(item, index, isNearMiss = false) {
    const effPrice = item.eff_price || item.price;
    const isSpecial = !isNearMiss && effPrice <= item.target && !item.price_unavailable;
    const card = document.createElement('div');
    const storeClass = item.store || 'woolworths';
    
    card.className = `item-card store-${storeClass} ${isNearMiss ? 'near-miss-card' : ''}`;
    card.style.animationDelay = `${(index % 20) * 0.05}s`;
    
    let imgHtml = item.image_url 
        ? `<img src="${item.image_url}" class="item-image" alt="${item.name}" loading="lazy">`
        : `<div class="product-img-placeholder"><i data-feather="image"></i></div>`;

    let targetHtml = `<span class="item-target">Target: $${item.target.toFixed(2)}</span>`;
    if (item.avg_price && item.avg_price > 0) {
        targetHtml += `<span class="item-avg">Paid Avg: $${item.avg_price.toFixed(2)}</span>`;
    }
    
    if (isSpecial) {
         const diff = item.target - effPrice;
         targetHtml += `<span class="deal-badge">🔥 -$${diff.toFixed(2)} vs target</span>`;
    } else if (isNearMiss) {
        targetHtml += `<span class="deal-badge" style="background: rgba(234, 179, 8, 0.1); color: #fde047;">🤏 Almost</span>`;
    }

    // W vs C Battle Row
    let battleRow = '';
    if (item.all_stores && Object.keys(item.all_stores).length > 1) {
        const wPrice = item.all_stores['woolworths']?.price || '—';
        const cPrice = item.all_stores['coles']?.price || '—';
        const wCheaper = typeof wPrice === 'number' && typeof cPrice === 'number' && wPrice < cPrice;
        const cCheaper = typeof wPrice === 'number' && typeof cPrice === 'number' && cPrice < wPrice;
        
        battleRow = `
            <div class="battle-row">
                <div class="battle-tag ${wCheaper ? 'winner' : ''}">W: ${typeof wPrice === 'number' ? '$' + wPrice.toFixed(2) : wPrice}</div>
                <div class="battle-tag ${cCheaper ? 'winner' : ''}">C: ${typeof cPrice === 'number' ? '$' + cPrice.toFixed(2) : cPrice}</div>
            </div>
        `;
    }

    const wPrice = (item.all_stores && item.all_stores['woolworths']?.price) || item.price;
    const qty = (_shopMode === 'big' && ['fresh_protein', 'pet', 'pantry', 'household', 'freezer'].includes(item.type)) ? (item.type === 'fresh_protein' ? 4 : 2) : 1;
    const itemTotal = (item.price * qty).toFixed(2);

    card.innerHTML = `
        ${imgHtml}
        <div class="item-content">
            <div class="store-badge ${storeClass}">${storeClass === 'woolworths' ? 'Woolies' : 'Coles'}</div>
            <h3 class="item-title" style="margin-top: 8px;">${item.name}</h3>
            <div class="item-price-row">
                <span class="item-price" style="color: ${storeClass === 'woolworths' ? 'var(--woolies-green)' : 'var(--coles-red)'}">${item.price_unavailable ? '❓' : formatPrice(item)}</span>
                ${targetHtml}
            </div>
            ${battleRow}
            <button class="add-to-list-btn" onclick="addToList('${item.name.replace(/'/g, "\\'")}')">
                <i data-feather="plus"></i> Add to List (${qty}×)
            </button>
            <div class="chart-container-sm" id="chart-card-${index}-${isNearMiss ? 'near' : 'special'}">
                <canvas></canvas>
            </div>
        </div>
    `;
    
    // Sparkline will be rendered after append
    setTimeout(() => {
        if (_history[item.name] && _history[item.name].history.length > 0) {
            renderSparkline(`chart-card-${index}-${isNearMiss ? 'near' : 'special'}`, _history[item.name].history, storeClass);
        }
        if (typeof feather !== 'undefined') feather.replace();
    }, 0);
    
    return card;
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

function renderColaBattle() {
    const colaItems = _data.filter(i => i.compare_group === 'cola' && !i.price_unavailable);
    const winnerEl = document.getElementById('cola-winner');
    const detailsEl = document.getElementById('cola-details');
    
    if (colaItems.length === 0) {
        if (winnerEl) winnerEl.textContent = '—';
        if (detailsEl) detailsEl.textContent = 'No cola data yet';
        return;
    }

    // Find cheapest Pepsi and cheapest Coke by eff_price (per litre)
    const pepsis = colaItems.filter(i => i.name.toLowerCase().includes('pepsi'));
    const cokes = colaItems.filter(i => i.name.toLowerCase().includes('coke') || i.name.toLowerCase().includes('coca'));
    
    const cheapestPepsi = pepsis.length ? pepsis.reduce((a, b) => (a.eff_price || a.price) < (b.eff_price || b.price) ? a : b) : null;
    const cheapestCoke = cokes.length ? cokes.reduce((a, b) => (a.eff_price || a.price) < (b.eff_price || b.price) ? a : b) : null;

    if (!cheapestPepsi && !cheapestCoke) {
        if (winnerEl) winnerEl.textContent = '—';
        if (detailsEl) detailsEl.textContent = 'No cola data';
        return;
    }

    const pepsiPrice = cheapestPepsi ? (cheapestPepsi.eff_price || cheapestPepsi.price) : Infinity;
    const cokePrice = cheapestCoke ? (cheapestCoke.eff_price || cheapestCoke.price) : Infinity;
    
    const pepsiStore = cheapestPepsi ? (cheapestPepsi.store === 'woolworths' ? '🟢W' : '🔴C') : '';
    const cokeStore = cheapestCoke ? (cheapestCoke.store === 'woolworths' ? '🟢W' : '🔴C') : '';

    if (winnerEl) {
        if (pepsiPrice < cokePrice) {
            winnerEl.innerHTML = `<span style="color: #3b82f6;">Pepsi Max</span> wins!`;
        } else if (cokePrice < pepsiPrice) {
            winnerEl.innerHTML = `<span style="color: #ef4444;">Coke Zero</span> wins!`;
        } else {
            winnerEl.textContent = 'Tied!';
        }
    }
    
    if (detailsEl) {
        let details = '';
        if (cheapestPepsi) details += `Pepsi $${pepsiPrice.toFixed(2)}/L ${pepsiStore}`;
        if (cheapestPepsi && cheapestCoke) details += ' vs ';
        if (cheapestCoke) details += `Coke $${cokePrice.toFixed(2)}/L ${cokeStore}`;
        detailsEl.textContent = details;
    }
}

function renderSpecials() {
    const grid = document.getElementById('specials-grid');
    grid.innerHTML = '';

    const displayItems = _data.filter(item => {
        const matchesStore = _currentFilter === 'all' || item.store === _currentFilter;
        const matchesCat = _currentCatFilter === 'all' || item.type === _currentCatFilter;
        const isSpecial = (item.eff_price || item.price) <= item.target && !item.price_unavailable;
        return matchesStore && matchesCat && isSpecial;
    });

    if (displayItems.length === 0) {
        grid.innerHTML = '<p style="color: var(--text-muted); grid-column: 1/-1;">No deep deals today.</p>';
        return;
    }

    displayItems.forEach((item, index) => {
        const card = createItemCard(item, index);
        grid.appendChild(card);
    });

    if (typeof feather !== 'undefined') feather.replace();
}

function renderAllItems() {
    const tbody = document.getElementById('all-items-tbody');
    tbody.innerHTML = '';

    // Filter by store if needed, though usually master list shows all
    const filteredData = _data.filter(item => {
        return _currentFilter === 'all' || item.store === _currentFilter;
    });

    filteredData.forEach((item, index) => {
        const tr = document.createElement('tr');
        const storeName = item.store === 'woolworths' ? 'Woolies' : 'Coles';
        const isSpecial = (item.eff_price || item.price) <= item.target && !item.price_unavailable;
        
        tr.innerHTML = `
            <td style="font-weight: 500;">
                ${item.name}
                ${isSpecial ? ' 🔥' : ''}
            </td>
            <td>
                <span class="store-badge ${item.store}">${storeName}</span>
            </td>
            <td style="font-weight: 600;">${item.price_unavailable ? '❓' : formatPrice(item)}</td>
            <td style="color: var(--text-muted);">$${item.target.toFixed(2)}</td>
            <td style="color: var(--accent-purple); font-weight: 500;">${item.avg_price ? '$' + item.avg_price.toFixed(2) : '-'}</td>
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
