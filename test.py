import json, urllib.request
from camellia.config import FANTNEL_INFO_URL
from camellia.models.entities import FantnelInfo

print('url:', FANTNEL_INFO_URL)
req = urllib.request.Request(FANTNEL_INFO_URL, headers={'User-Agent':
'CamelliaTest/1.0'})
with urllib.request.urlopen(req, timeout=10) as resp:
  raw = resp.read().decode('utf-8')
data = json.loads(raw)
payload = data.get('data') if isinstance(data, dict) and 'data' in data else data
info = FantnelInfo.from_dict(payload or {})
print('crc_salt:', info.crc_salt)
print('game_version:', info.game_version)