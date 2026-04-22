# WooliesBot – Skills & Quick Reference

Quick reference for common tasks on the WooliesBot project.

**Safety:** Cursor loads **[`.cursor/rules/wooliesbot-data-safety.mdc`](rules/wooliesbot-data-safety.mdc)** in every session (`alwaysApply`). For inventory merges, discovery, and verification habits, see the **wooliesbot-operations** skill: **[`SKILL.md`](skills/wooliesbot-operations/SKILL.md)**.

**Variant discovery:** **[`scripts/discover_variants.py`](../scripts/discover_variants.py)** — proposes draft SKUs (`--compare-group`, `--inventory-scan`). Does not edit `data.json`; merge by hand after review. **UI:** [`docs/discovery-review.html`](../docs/discovery-review.html). **Optional weekly snippet:** [`scripts/discovery_weekly_snippet.sh`](../scripts/discovery_weekly_snippet.sh) → see [`LAUNCHD_SETUP.md`](../LAUNCHD_SETUP.md). **Stack verify:** [`scripts/verify_wooliesbot_stack.sh`](../scripts/verify_wooliesbot_stack.sh) (`VERIFY_STRICT=1` fails on e2e layer FAIL lines).

---

## 🛒 Adding a New Product to the Watchlist

1. Find the product URL on **woolworths.com.au** and/or **coles.com.au**
2. Edit `docs/data.json` — add a new object to the `items` array:

```json
{
  "name": "Product Name",
  "woolworths": "https://www.woolworths.com.au/shop/productdetails/...",
  "coles": "https://www.coles.com.au/product/...",
  "price_mode": "each",
  "compare_group": null,
  "target": 3.50,
  "stock": "medium",
  "last_purchased": null,
  "price_history": []
}
```

3. `price_mode` options:
   - `"each"` — compare shelf price directly
   - `"kg"` — compare scraped unit_price ($/kg). Add nothing extra.
   - `"litre"` — always add `"pack_litres": 2.0` (the literal pack volume)

4. If multiple variants compete (e.g. Woolies vs Coles milk), set
   `"compare_group": "milk"` on all variants — cheapest wins each run.

---

## 💰 Running the Scraper Manually

```bash
cd ~/Woolies\ Script
source .venv/bin/activate
python chef_os.py --run-now   # scrape immediately, then exit
python chef_os.py             # run on schedule (daemon mode)
```

To test a single item without full scrape, use scratch files in `scratch/`.

---

## 🔄 Restarting Services

```bash
cd ~/Woolies\ Script
bash manage_services.sh restart   # restart automation service
bash manage_services.sh stop
```

---

## 📊 Working on the Dashboard

The dashboard is **pure static HTML/CSS/JS** — no build step needed.

Open `docs/index.html` in a browser (via file:// or a local server):
```bash
cd ~/Woolies\ Script/docs
python3 -m http.server 8080   # then visit http://localhost:8080
```

Key files:
| File | Purpose |
|------|---------|
| `docs/index.html` | Structure + CDN imports (Chart.js etc.) |
| `docs/style.css` | All styles — mobile-first, CSS custom properties |
| `docs/app.js` | All dashboard logic — tabs, charts, data rendering |
| `docs/data.json` | Live data — read directly by the dashboard |
| `docs/heartbeat.json` | Next scraper run time (UTC ISO string) |

**Golden rules:**
- Never add npm/node/bundlers.
- Always read data as `data.items` (handle both array and `{items:[]}` formats).
- Destroy Chart.js instances before re-creating: `myChart?.destroy()`.
- Pantry writes use the configured cloud Worker URL (`write_api_url`).

---

## 🛠️ Patching / Backfilling data.json

When patching prices or adding history entries manually:

```python
import json, datetime

with open("docs/data.json") as f:
    data = json.load(f)

items = data["items"] if isinstance(data, dict) else data

for item in items:
    if item["name"] == "Milk (Full Cream 2L)":
        item["price_history"].append({
            "date": "2026-04-10",
            "price": 2.50,
            "store": "woolworths"
        })
        break

if isinstance(data, dict):
    data["items"] = items
    data["last_updated"] = datetime.datetime.utcnow().isoformat() + "Z"

with open("docs/data.json", "w") as f:
    json.dump(data, f, indent=2)
```

---

## 📩 Sending a Telegram Message

```python
from chef_os import send_telegram
send_telegram("⚡ Test message from WooliesBot")
```

Escape dynamic text BEFORE embedding:
```python
from chef_os import _escape_md
msg = f"Best price: *{_escape_md(item_name)}* at ${price:.2f}"
send_telegram(msg)
```

---

## 🔑 Environment Variables

Copy `.env.example` → `.env` and fill in:

| Variable | Description |
|---|---|
| `TELEGRAM_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Channel/group ID (negative for group) |

The `.env` is gitignored. chef_os.py loads it automatically via `_load_dotenv()`.

---

## 📁 Key Directories

| Path | Purpose |
|---|---|
| `docs/` | Web root — all dashboard files |
| `docs/images/` | Local product image cache (auto-downloaded) |
| `logs/` | Runtime logs (gitignored) |
| `logs/screenshots/` | Scrape error screenshots |
| `backups/` | Auto data.json backups before each write |
| `scratch/` | Temporary test scripts (gitignored) |
| `chrome_profile/` | Persistent Chrome user data (gitignored) |

---

## ⚠️ Known Issues & Workarounds

| Issue | Workaround |
|---|---|
| Coles API returns 404 | buildId expired — `_refresh_coles_metadata()` auto-fixes on next run |
| Product shows $99999 price | Scraper couldn't determine unit price — check `price_mode` and `pack_litres` |
| Dashboard shows "Scrape status: unavailable" | Verify `docs/heartbeat.json` is updating and the latest scrape workflow succeeded |
| Chart.js blank canvas | Old chart not destroyed — call `chart.destroy()` first |
| Telegram message truncated | Over 4000 chars — `send_telegram()` auto-splits at newlines |
| Images not loading | `docs/images/` cache miss — run scraper to refresh |
