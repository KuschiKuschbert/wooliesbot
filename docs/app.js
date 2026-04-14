document.addEventListener('DOMContentLoaded', () => {
    feather.replace();
    initDashboard();
});

let _data = [];
let _history = {};
let _currentFilter = 'all';
let _currentCatFilter = 'all';

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
            e.target.classList.add('active');
            
            _currentCatFilter = e.target.dataset.cat;
            renderDashboard();
        });
    });
}

function renderDashboard() {
    renderStats();
    renderSpecials();
    renderAllItems();
}

function formatPrice(item) {
    const effPrice = item.eff_price || item.price;
    if (item.price_mode === 'kg') return `$${effPrice.toFixed(2)}/kg`;
    if (item.price_mode === 'litre') return `$${item.price.toFixed(2)} ($${effPrice.toFixed(2)}/L)`;
    return `$${item.price.toFixed(2)}`;
}

function renderStats() {
    document.getElementById('total-items').textContent = _data.length;
    
    // Count specials (price <= target)
    let specialsCount = 0;
    let estimatedCart = 0;
    
    _data.forEach(item => {
        const effPrice = item.eff_price || item.price;
        if (effPrice <= item.target && !item.price_unavailable) {
            specialsCount++;
            estimatedCart += item.price; // We buy 1 of each special for baseline estimate
        }
    });

    document.getElementById('total-specials').textContent = specialsCount;
    document.getElementById('cart-total').textContent = `$${estimatedCart.toFixed(2)}`;
}

function renderSpecials() {
    const grid = document.getElementById('specials-grid');
    grid.innerHTML = '';

    const displayItems = _data.filter(item => {
        const matchesStore = _currentFilter === 'all' || item.store === _currentFilter;
        const matchesCat = _currentCatFilter === 'all' || item.type === _currentCatFilter;
        return matchesStore && matchesCat;
    });

    if (displayItems.length === 0) {
        grid.innerHTML = '<p style="color: var(--text-muted); grid-column: 1/-1;">No items matched the filters.</p>';
        return;
    }

    displayItems.forEach((item, index) => {
        const isSpecial = (item.eff_price || item.price) <= item.target && !item.price_unavailable;
        const card = document.createElement('div');
        const storeClass = item.store || 'woolworths';
        card.className = `item-card store-${storeClass}`;
        card.style.animationDelay = `${(index % 20) * 0.05}s`;
        
        let imgHtml = '';
        if (item.image_url) {
            imgHtml = `<img src="${item.image_url}" class="item-image" alt="${item.name}" loading="lazy">`;
        } else {
            imgHtml = `<div class="product-img-placeholder"><i data-feather="image"></i></div>`;
        }

        let targetHtml = `<span class="item-target">Target: $${item.target.toFixed(2)}</span>`;
        if (isSpecial) {
             const diff = item.target - (item.eff_price || item.price);
             targetHtml += `<span class="deal-badge">🔥 -$${diff.toFixed(2)} vs target</span>`;
        }

        card.innerHTML = `
            ${imgHtml}
            <div class="item-content">
                <div class="store-badge ${storeClass}">${storeClass === 'woolworths' ? 'Woolies' : 'Coles'}</div>
                <h3 class="item-title" style="margin-top: 8px;">${item.name}</h3>
                <div class="item-price-row">
                    <span class="item-price" style="color: ${storeClass === 'woolworths' ? 'var(--woolies-green)' : 'var(--coles-red)'}">${item.price_unavailable ? '❓' : formatPrice(item)}</span>
                    ${targetHtml}
                </div>
                <div class="chart-container-sm" id="chart-card-${index}">
                    <canvas></canvas>
                </div>
            </div>
        `;
        grid.appendChild(card);
        
        // Render chart if history exists
        if (_history[item.name] && _history[item.name].history.length > 0) {
            renderSparkline(`chart-card-${index}`, _history[item.name].history, storeClass);
        }
    });

    // Re-init icons for dynamic content
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
