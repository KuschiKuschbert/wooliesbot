/**
 * Shared “open store in new tab” anchor markup. Loaded after compare_helpers.js.
 * @see docs/app.js for dashboard usage; discovery-review.html uses the same module.
 */
(function (global) {
    function escapeAttr(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function storePdpAnchorHtml(href, storeKey, opts) {
        const sk = storeKey === 'coles' ? 'coles' : 'woolworths';
        const label = sk === 'coles' ? 'Open at Coles' : 'Open at Woolworths';
        const aria = `${label}, new tab`;
        const extra = (opts && opts.className && String(opts.className).trim()) || '';
        const safe = escapeAttr(href);
        const cls = extra ? `store-pdp-link ${extra}` : 'store-pdp-link';
        return (
            `<a class="${cls}" href="${safe}" target="_blank" rel="noopener noreferrer" ` +
            `title="${escapeAttr(label)}" aria-label="${escapeAttr(aria)}">` +
            '<i data-feather="external-link" class="store-pdp-link-icon" aria-hidden="true"></i></a>'
        );
    }

    function storePdpLinkForItem(item, storeKey, urlOpts, anchorOpts) {
        const H = global.WooliesCompareHelpers;
        if (!H || !item) return '';
        const href = H.getStoreUrlForStore(item, storeKey, urlOpts || {});
        return storePdpAnchorHtml(href, storeKey, anchorOpts || {});
    }

    global.WBStorePdp = { storePdpAnchorHtml, storePdpLinkForItem };
})(window);
