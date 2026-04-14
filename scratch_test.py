import chef_os
import sys

# Monkey patch Tracking list to just 1 item
chef_os.TRACKING_LIST = [
    {"name": "Watermelon (Quarter)", "type": "fresh_veg", "price_mode": "each", "target": 3.50,
     "woolworths": "https://www.woolworths.com.au/shop/productdetails/120384/woolworths-red-watermelon-cut-quarter",
     "coles": "https://www.coles.com.au/product/coles-seedless-watermelon-cut-approx.-1.8kg-7508229"}
]

print("Running test report...")
# Monkey patch send_telegram to prevent spamming the channel during our quick test
chef_os.send_telegram = lambda msg: print("TELEGRAM:", msg)

# We want it to execute git push? Let's disable sync_to_github for this test test so we can do it manually, or let it run.
old_sync = chef_os.sync_to_github
chef_os.sync_to_github = lambda: print("Skipping github sync during first test")

chef_os.run_report(full_list=True)

print("Test complete.")
