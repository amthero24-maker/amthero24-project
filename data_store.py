import json, threading, time
from pathlib import Path
DATA_FILE=Path('data.json')
_lock=threading.Lock()
def _load():
    if not DATA_FILE.exists(): return {"users":{},"messages":{},"cases":{},"audit_log":[]}
    try: return json.loads(DATA_FILE.read_text(encoding='utf-8'))
    except: return {"users":{},"messages":{},"cases":{},"audit_log":[]}
def _save_atomic(data):
    with _lock:
        tmp=DATA_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(DATA_FILE)
def add_message(msg_id,sender,text):
    if not msg_id: return False
    store=_load()
    if msg_id in store['messages']: return False
    store['messages'][msg_id]={'sender':sender,'text':text,'ts':time.time()}
    _save_atomic(store)
    return True
