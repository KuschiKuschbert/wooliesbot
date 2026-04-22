(function (global) {
  function formatCompareEffPrice(priceMode, eff, isReliableEffPrice) {
    const mode = priceMode || 'each';
    if (!isReliableEffPrice(eff)) return '—';
    if (mode === 'kg') return `$${eff.toFixed(2)}/kg`;
    if (mode === 'litre') return `$${eff.toFixed(2)}/L`;
    return `$${eff.toFixed(2)}`;
  }

  function minEffPriceAcrossStores(item, isReliableEffPrice) {
    const stores = item.all_stores || {};
    let m = Infinity;
    for (const sd of Object.values(stores)) {
      if (isReliableEffPrice(sd.eff_price)) m = Math.min(m, sd.eff_price);
    }
    if (Number.isFinite(m)) return m;
    const fallback = item.eff_price ?? item.price;
    return isReliableEffPrice(fallback) ? fallback : Infinity;
  }

  function candidateSortTuple(c) {
    const pl = c.item.pack_litres || 0;
    const storeRank = c.store === 'woolworths' ? 0 : 1;
    const cokeBias = /coke|coca/i.test(c.item.name || '') ? 0 : 1;
    return [c.eff_price, -pl, storeRank, cokeBias, c.item.name || ''];
  }

  function compareCandidates(a, b) {
    const ta = candidateSortTuple(a);
    const tb = candidateSortTuple(b);
    for (let i = 0; i < ta.length; i++) {
      if (ta[i] < tb[i]) return -1;
      if (ta[i] > tb[i]) return 1;
    }
    return 0;
  }

  function expandItemStoreCandidates(item, isReliableEffPrice) {
    const out = [];
    const stores = item.all_stores || {};
    for (const [storeKey, sd] of Object.entries(stores)) {
      const ep = sd.eff_price;
      if (!isReliableEffPrice(ep)) continue;
      out.push({
        item,
        store: storeKey,
        eff_price: ep,
        shelf_price: sd.price,
        unit_price: sd.unit_price,
      });
    }
    return out;
  }

  function latestMatchedNameForStore(item, storeKey) {
    const hist = Array.isArray(item.scrape_history) ? item.scrape_history : [];
    for (let i = hist.length - 1; i >= 0; i--) {
      const entry = hist[i] || {};
      const matched = (entry.matched_name || '').trim();
      if (matched && entry.store === storeKey) return matched;
    }
    return '';
  }

  function buildStoreSearchTerm(item, storeKey) {
    return (
      latestMatchedNameForStore(item, storeKey) ||
      (item.name_check || '').trim() ||
      (item.name || '').trim()
    );
  }

  function getStoreUrlForStore(item, storeKey, opts) {
    const hasStoreData = Object.keys(item.all_stores || {}).length > 0;
    const preferSearchForWoolworthsPdp = opts.preferSearchForWoolworthsPdp == null
      ? true
      : Boolean(opts.preferSearchForWoolworthsPdp);
    if (storeKey === 'coles') {
      if (item.coles) return item.coles;
      return `https://www.coles.com.au/search?q=${encodeURIComponent(buildStoreSearchTerm(item, 'coles'))}`;
    }
    if (item.woolworths) {
      const isWooliesPdp = item.woolworths.includes('/productdetails/');
      if (preferSearchForWoolworthsPdp && isWooliesPdp) {
        return `https://www.woolworths.com.au/shop/search/products?searchTerm=${encodeURIComponent(buildStoreSearchTerm(item, 'woolworths'))}`;
      }
      if (hasStoreData || !isWooliesPdp) {
        return item.woolworths;
      }
    }
    return `https://www.woolworths.com.au/shop/search/products?searchTerm=${encodeURIComponent(buildStoreSearchTerm(item, 'woolworths'))}`;
  }

  function classifyColaCandidate(item) {
    const name = (item.name || '').toLowerCase();
    if (/mango|vanilla|cherry|lime|raspberry|ginger|lemon|creaming soda|orange|grape|melon/i.test(name)) return null;
    const isNoSugar = name.includes('max') || name.includes('zero') || name.includes('no sugar');
    const isPepsi = name.includes('pepsi');
    const isCoke = name.includes('coke') || name.includes('coca');
    const category = isNoSugar ? 'noSugar' : 'classic';
    let brand = null;
    if (isPepsi) brand = 'pepsi';
    else if (isCoke) brand = 'coke';
    else return null;
    return { category, brand };
  }

  function colaCandidatePerLitre(c, isReliableEffPrice, priceUnreliable) {
    if (!c || !c.item) return null;
    const item = c.item;
    const packL = item.pack_litres;
    const shelf = c.shelf_price;
    if (
      typeof packL === 'number' &&
      Number.isFinite(packL) &&
      packL > 0 &&
      typeof shelf === 'number' &&
      Number.isFinite(shelf) &&
      shelf > 0
    ) {
      return shelf / packL;
    }

    if (item.price_mode === 'litre' && isReliableEffPrice(c.eff_price)) return c.eff_price;

    const itemUnit = (item.unit || '').toLowerCase();
    if (
      itemUnit === 'litre' &&
      typeof c.unit_price === 'number' &&
      Number.isFinite(c.unit_price) &&
      c.unit_price > 0 &&
      c.unit_price < priceUnreliable
    ) {
      return c.unit_price;
    }
    return null;
  }

  function compareColaCandidates(a, b, colaCandidatePerLitreFn, compareCandidatesFn) {
    const aPL = typeof a.per_litre === 'number' ? a.per_litre : colaCandidatePerLitreFn(a);
    const bPL = typeof b.per_litre === 'number' ? b.per_litre : colaCandidatePerLitreFn(b);
    const aOK = typeof aPL === 'number' && Number.isFinite(aPL) && aPL > 0;
    const bOK = typeof bPL === 'number' && Number.isFinite(bPL) && bPL > 0;
    if (aOK && !bOK) return -1;
    if (!aOK && bOK) return 1;
    if (aOK && bOK && Math.abs(aPL - bPL) > 0.0001) return aPL - bPL;
    return compareCandidatesFn(a, b);
  }

  function displayName(name, displayAbbrevs) {
    if (!name) return '';
    let n = name;
    for (const [expr, rep] of displayAbbrevs) n = n.replace(expr, rep);
    return n.replace(/\s{2,}/g, ' ').trim();
  }

  global.WooliesCompareHelpers = {
    formatCompareEffPrice,
    minEffPriceAcrossStores,
    candidateSortTuple,
    compareCandidates,
    expandItemStoreCandidates,
    latestMatchedNameForStore,
    buildStoreSearchTerm,
    getStoreUrlForStore,
    classifyColaCandidate,
    colaCandidatePerLitre,
    compareColaCandidates,
    displayName,
  };
})(window);
