#!/usr/bin/env bash
# Prints a valid Microsoft Graph access token for the personal hotmail account
# (bokormark@hotmail.com), refreshing it via the stored refresh token when near expiry.
# The token bundle lives in store/.hotmail-graph-token.json (600, gitignored).
# NEVER send the output to any channel (Telegram/email) or log it.
set -euo pipefail
cd "$(dirname "$0")/.."
CID=08fb87d9-bed1-450f-8221-160bfd4a8892
TOKENFILE=store/.hotmail-graph-token.json
SCOPE="offline_access Mail.ReadWrite Mail.Send Calendars.ReadWrite Files.ReadWrite.All User.Read"

need_refresh=$(python3 - "$TOKENFILE" <<'PY'
import json,sys,time
d=json.load(open(sys.argv[1]))
exp=d.get('obtained_at',0)+d.get('expires_in',3600)-300  # 5 min buffer
print("1" if time.time()>=exp else "0")
PY
)

if [ "$need_refresh" = "1" ]; then
  RT=$(python3 -c "import json;print(json.load(open('$TOKENFILE'))['refresh_token'])")
  resp=$(curl -s -X POST "https://login.microsoftonline.com/consumers/oauth2/v2.0/token" \
    --data-urlencode "grant_type=refresh_token" \
    --data-urlencode "client_id=$CID" \
    --data-urlencode "refresh_token=$RT" \
    --data-urlencode "scope=$SCOPE")
  tmp=$(mktemp); printf '%s' "$resp" > "$tmp"
  # Note: pass the response via a file, not stdin — a heredoc script already owns stdin.
  if ! python3 - "$TOKENFILE" "$tmp" <<'PY'
import json,sys,time
respf, f = sys.argv[2], sys.argv[1]
new=json.load(open(respf))
if 'access_token' not in new:
    sys.stderr.write("REFRESH_FAILED: "+str(new.get('error_description','?'))[:200]+"\n"); sys.exit(1)
old=json.load(open(f))
# Microsoft rotates refresh tokens; keep the newest.
old.update({k:new[k] for k in ('access_token','expires_in','scope') if k in new})
if new.get('refresh_token'): old['refresh_token']=new['refresh_token']
old['obtained_at']=int(time.time())
json.dump(old,open(f,'w'))
PY
  then rm -f "$tmp"; exit 1; fi
  rm -f "$tmp"
  chmod 600 "$TOKENFILE"
fi

python3 -c "import json;print(json.load(open('$TOKENFILE'))['access_token'])"
