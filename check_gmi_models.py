#!/usr/bin/env python3
"""Query the authenticated GMI inventory. No third-party packages. No key printed."""
import json
import os
import urllib.error
import urllib.request
import sys

url = 'https://api.gmi-serving.com/v1/models'
key = os.environ.get('GMI_API_KEY', '')
if not key:
    sys.exit('Set GMI_API_KEY in the environment first (do not paste it into chat).')
req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + key})
try:
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.load(response)
except urllib.error.HTTPError as e:
    sys.exit(f'GMI endpoint returned HTTP {e.code}; check your key and account entitlements.')
items = payload.get('data', [])
print(f'GMI /v1/models returned {len(items)} entries. Matches containing neon/hydrogen/carbon/argon:')
for model in items:
    id_ = str(model.get('id', ''))
    if any(w in id_.lower() for w in ('neon', 'hydrogen', 'carbon', 'argon')):
        print('  ' + id_)
print('\nOther tool-capable coding candidates (inspect the catalog details for tool support):')
for model in items:
    id_ = str(model.get('id', ''))
    if any(w in id_.lower() for w in ('minimax', 'glm', 'qwen', 'kimi', 'deepseek')):
        print('  ' + id_)
