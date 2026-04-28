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

    /** Product cards: shelf price visually dominant next to unit price when both differ. */
    function cardPricePrimaryHtml(item) {
        const effPrice = item.eff_price ?? item.price;
        const pack = item.price;
        const pm = item.price_mode || 'each';
        if (
            pm === 'kg' &&
            Number.isFinite(pack) &&
            Number.isFinite(effPrice) &&
            Math.abs(pack - effPrice) > 0.02
        ) {
            return `<span class="item-price item-price--dual"><span class="price-pack">$${pack.toFixed(
                2,
            )}</span><span class="price-per-unit">$${effPrice.toFixed(2)}/kg</span></span>`;
        }
        if (
            pm === 'litre' &&
            Number.isFinite(pack) &&
            Number.isFinite(effPrice) &&
            Math.abs(pack - effPrice) > 0.02
        ) {
            return `<span class="item-price item-price--dual"><span class="price-pack">$${pack.toFixed(
                2,
            )}</span><span class="price-per-unit">$${effPrice.toFixed(2)}/L</span></span>`;
        }
        return `<span class="item-price">${formatPrice(item)}</span>`;
    }

    global.WooliesFormatPrice = {
        formatPrice,
        cardPricePrimaryHtml,
    };
})(typeof window !== 'undefined' ? window : globalThis);
