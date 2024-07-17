"""Fetch installed modules from runbot log"""

import argparse
import requests
import re

# call the script like this:
# python installed_modules.py hr_work_entry_holidays http://runbot211.odoo.com/runbot/static/build/64980297-master/logs/all_at_install.txt
parser = argparse.ArgumentParser()
parser.add_argument("module", help="Name of the module to stop at", type=str)
parser.add_argument("url", help="URL of the install log", type=str)
args = parser.parse_args()
url = args.url
print(f"Fetching {url}")
response = requests.request("GET", url, timeout=3)

# example line:
# 2024-07-03 10:24:55,167 13 INFO 64935245-master-all odoo.modules.loading: Loading module hr_work_entry_holidays (282/768)
results = re.findall("Loading module (\w*)", str(response.content))
filtered = []
for result in results:
    filtered.append(result)
    if result == args.module:
        break
print(",".join(filtered))
