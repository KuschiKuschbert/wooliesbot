(function (global) {
    'use strict';

    function formatPrice(item) {
        const effPrice = item.eff_price || item.price;
        const pack = item.price;
        if (item.price_mode === 'kg') {
            if (
                Number.isFinite(pack) &&
                Number.isFinite(effPrice) &&
                Math.abs(pack - effPrice) > 0.02
            ) {
                return `$${pack.toFixed(2)} · $${effPrice.toFixed(2)}/kg`;
            }
            return `$${effPrice.toFixed(2)}/kg`;
        }
        if (item.price_mode === 'litre') {
            if (
                Number.isFinite(pack) &&
                Number.isFinite(effPrice) &&
                Math.abs(pack - effPrice) > 0.02
            ) {
                return `$${pack.toFixed(2)} · $${effPrice.toFixed(2)}/L`;
            }
            return `$${pack.toFixed(2)} ($${effPrice.toFixed(2)}/L)`;
        }
        return `$${item.price.toFixed(2)}`;
    }

    /**
     * Product cards: consistent shelf-first layout using existing price + eff_price only.
     * kg/litre: two columns (Shelf | unit rate) whenever both numbers exist.
     * each: labelled shelf ticket.
     */
    function cardPricePrimaryHtml(item) {
        const effPrice = item.eff_price ?? item.price;
        const pack = item.price;
        const pm = item.price_mode || 'each';

        if (pm === 'kg' && Number.isFinite(pack) && Number.isFinite(effPrice)) {
            return `<span class="item-price item-price--dual" aria-label="Shelf price ${pack.toFixed(
                2
            )} dollars, ${effPrice.toFixed(2)} dollars per kilogram">
                <span class="price-dual-col">
                    <span class="price-tier-caption">Shelf</span>
                    <span class="price-pack">$${pack.toFixed(2)}</span>
                </span>
                <span class="price-dual-col price-dual-col--unit">
                    <span class="price-tier-caption">$/kg</span>
                    <span class="price-per-unit">$${effPrice.toFixed(2)}</span>
                </span>
            </span>`;
        }
        if (pm === 'litre' && Number.isFinite(pack) && Number.isFinite(effPrice)) {
            return `<span class="item-price item-price--dual" aria-label="Shelf ${pack.toFixed(
                2
            )} dollars, ${effPrice.toFixed(2)} dollars per litre">
                <span class="price-dual-col">
                    <span class="price-tier-caption">Shelf</span>
                    <span class="price-pack">$${pack.toFixed(2)}</span>
                </span>
                <span class="price-dual-col price-dual-col--unit">
                    <span class="price-tier-caption">$/L</span>
                    <span class="price-per-unit">$${effPrice.toFixed(2)}</span>
                </span>
            </span>`;
        }
        if (pm === 'each' && Number.isFinite(pack)) {
            return `<span class="item-price item-price--simple">
                <span class="price-tier-caption">Shelf</span>
                <span class="price-pack price-pack--solo">$${pack.toFixed(2)}</span>
            </span>`;
        }
        return `<span class="item-price">${formatPrice(item)}</span>`;
    }

    global.WooliesFormatPrice = {
        formatPrice,
        cardPricePrimaryHtml,
    };
})(typeof window !== 'undefined' ? window : globalThis);
