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

  /**
   * Pick the single anchor item to display for a compare_group in the specials
   * grid. Priority: most-recently purchased > in shopping list > cheapest.
   * @param {object[]} members  - all items that are currently visible (on special) for the group
   * @param {Set|string[]} shoppingListNames - names currently in the shopping list
   * @param {Function} isReliableEffPriceFn
   * @returns {object} one item from members
   */
  function pickGroupAnchor(members, shoppingListNames, isReliableEffPriceFn) {
    if (!members || members.length === 0) return null;
    if (members.length === 1) return members[0];

    const listSet = shoppingListNames instanceof Set
      ? shoppingListNames
      : new Set(Array.isArray(shoppingListNames) ? shoppingListNames : []);

    // 1. Most recently purchased
    let bestPurchased = null;
    let bestDate = '';
    for (const item of members) {
      const d = item.last_purchased || '';
      if (d && d > bestDate) { bestDate = d; bestPurchased = item; }
    }
    if (bestPurchased) return bestPurchased;

    // 2. In shopping list
    for (const item of members) {
      if (listSet.has(item.name)) return item;
    }

    // 3. Cheapest by min eff_price across stores
    let cheapest = members[0];
    let cheapestEff = minEffPriceAcrossStores(members[0], isReliableEffPriceFn);
    for (let i = 1; i < members.length; i++) {
      const ep = minEffPriceAcrossStores(members[i], isReliableEffPriceFn);
      if (ep < cheapestEff) { cheapestEff = ep; cheapest = members[i]; }
    }
    return cheapest;
  }

  /**
   * Find a group member that beats the anchor by at least thresholdPct (default 5 %).
   * Returns { item, eff_price } or null if anchor is already best / within threshold.
   */
  function findCheaperVariant(members, anchor, isReliableEffPriceFn, thresholdPct) {
    const thr = typeof thresholdPct === 'number' ? thresholdPct : 0.05;
    const anchorEff = minEffPriceAcrossStores(anchor, isReliableEffPriceFn);
    if (!Number.isFinite(anchorEff)) return null;
    let best = null;
    let bestEff = Infinity;
    for (const item of members) {
      if (item === anchor) continue;
      const ep = minEffPriceAcrossStores(item, isReliableEffPriceFn);
      if (!Number.isFinite(ep)) continue;
      if (ep < bestEff) { bestEff = ep; best = item; }
    }
    if (best && bestEff < anchorEff * (1 - thr)) return { item: best, eff_price: bestEff };
    return null;
  }

  /**
   * Collapse a list of special items so that multi-member compare_groups are
   * represented by a single anchor card. Groups with only one visible member are
   * left through untouched (so a search that surfaces a non-anchor still shows it).
   * @param {object[]} items - already-filtered display items
   * @param {string[]|Set} shoppingListNames
   * @param {Function} isReliableEffPriceFn
   * @returns {object[]} collapsed list
   */
  function collapseGroupsToAnchors(items, shoppingListNames, isReliableEffPriceFn) {
    const listSet = shoppingListNames instanceof Set
      ? shoppingListNames
      : new Set(Array.isArray(shoppingListNames) ? shoppingListNames : []);
    const counts = new Map();
    for (const item of items) {
      if (item.compare_group) counts.set(item.compare_group, (counts.get(item.compare_group) || 0) + 1);
    }
    const seen = new Set();
    const out = [];
    for (const item of items) {
      const g = item.compare_group;
      if (!g || (counts.get(g) || 1) <= 1) { out.push(item); continue; }
      if (seen.has(g)) continue;
      seen.add(g);
      out.push(pickGroupAnchor(items.filter(i => i.compare_group === g), listSet, isReliableEffPriceFn));
    }
    return out;
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
    pickGroupAnchor,
    findCheaperVariant,
    collapseGroupsToAnchors,
  };
})(window);
