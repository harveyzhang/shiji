"""HTTP 入口：/api/investigate 收一张图或一句文字线索，返回契约 JSON。上传的图片只在内存里用，不落盘。"""
from collections import defaultdict, deque
import json
import os
import queue
import re
import secrets
import threading
import time
from typing import Optional

from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .contract import INPUT_TYPES
from .investigate import investigate
from .search import YuanbaoSearch

STATIC = Path(__file__).resolve().parent/'static'  # 预览页和本地化的 Three.js
DEV_ORIGINS = ('http://localhost:5173', 'http://127.0.0.1:5173')
SHARE_ID = re.compile(r'^[A-Za-z0-9_-]{8,32}$')
PLANS = ('review', 'storywall', 'culture')  # 人工复核报告 / 老物件故事墙 / 文旅专题，价格写在页面上
ORDERS_PER_HOUR = 5
IMAGE_TYPES = ('image/jpeg', 'image/png', 'image/webp')
DEFAULT = object()


def default_search():
    if os.environ.get('SHIJI_SEARCH_DISABLED') == '1':
        return None
    caps = {name: int(os.environ[variable]) for name, variable in (('month_cap', 'SHIJI_MONTH_CAP'), ('day_cap', 'SHIJI_DAY_CAP'))
            if os.environ.get(variable)}  # A server keeps its own ledger, so it runs with lower caps.
    return YuanbaoSearch(cache_dir=os.environ.get('SHIJI_CACHE_DIR'), **caps)


def create_app(search=DEFAULT, observe=None, write=None, check_link=None, hourly_cases=30, max_image_bytes=8*1024*1024,
               origins=None, trusted_proxies=('127.0.0.1', '::1'), archive_dir=None, order_dir=None):
    search = default_search() if search is DEFAULT else search
    origins = origins or [o for o in os.environ.get('SHIJI_CORS_ORIGINS', '').split(',') if o] or list(DEV_ORIGINS)
    app = FastAPI(title='拾迹')
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=['GET', 'POST'], allow_headers=['*'])
    app.mount('/static', StaticFiles(directory=STATIC), name='static')

    @app.get('/', include_in_schema=False)
    def home():
        return FileResponse(STATIC/'index.html')
    visits, lock = defaultdict(deque), threading.Lock()

    def visitor(request):
        # Behind the site's reverse proxy every request comes from loopback; only then is X-Real-IP believed.
        host = request.client.host if request.client else 'unknown'
        return (request.headers.get('x-real-ip') or host) if host in trusted_proxies else host

    def allowed(client):
        now = time.time()
        with lock:
            recent = visits[client]
            while recent and now-recent[0] > 3600:
                recent.popleft()
            if len(recent) >= hourly_cases:
                return False
            recent.append(now)
            return True

    orders, order_visits = (Path(order_dir) if order_dir else None), defaultdict(deque)

    def order_allowed(client):
        now = time.time()
        with lock:
            recent = order_visits[client]
            while recent and now-recent[0] > 3600:
                recent.popleft()
            if len(recent) >= ORDERS_PER_HOUR:
                return False
            recent.append(now)
            return True

    @app.post('/api/orders')
    def order(request: Request, plan: str = Form(''), contact: str = Form(''), name: str = Form(''), note: str = Form(''),
              shareId: str = Form('')):
        """Take an order for a paid service; only what the operator needs to follow up is kept."""
        if orders is None:
            return JSONResponse({'detail': '暂时不接单。'}, status_code=503)
        contact, share_id = contact.strip(), shareId.strip()
        if plan not in PLANS or not contact or (share_id and not SHARE_ID.match(share_id)):
            return JSONResponse({'detail': '请选一项服务，并留下联系方式。'}, status_code=422)
        if not order_allowed(visitor(request)):
            return JSONResponse({'detail': '下单太频繁了，请一小时后再试。'}, status_code=429)
        order_id = f'{time.strftime("%Y%m%d-%H%M%S")}-{secrets.token_hex(3)}'
        record = {'orderId': order_id, 'createdAt': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'plan': plan, 'name': name.strip()[:20],
                  'contact': contact[:60], 'note': note.strip()[:300], 'shareId': share_id}
        orders.mkdir(parents=True, exist_ok=True)
        tmp = orders/f'.{order_id}.tmp'
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
        tmp.replace(orders/f'{order_id}.json')
        return {'ok': True, 'orderId': order_id}

    @app.get('/api/health')
    def health():
        return {'ok': True, 'search': search is not None, 'vision': observe is not None, 'writer': write is not None}

    archive = Path(archive_dir) if archive_dir else None

    def keep(case):
        """Archive the finished case (it never contains the uploaded image) under an unguessable share id."""
        if archive is None:
            return case
        share_id = secrets.token_urlsafe(9)
        archive.mkdir(parents=True, exist_ok=True)
        tmp = archive/f'.{share_id}.tmp'
        tmp.write_text(json.dumps(case, ensure_ascii=False), encoding='utf-8')
        tmp.replace(archive/f'{share_id}.json')
        return {**case, 'shareId': share_id}

    @app.get('/api/cases/{share_id}')
    def shared(share_id: str):
        path = archive/f'{share_id}.json' if archive and SHARE_ID.match(share_id) else None
        if path is None or not path.is_file():
            return JSONResponse({'detail': '没有找到这份调查记录。'}, status_code=404)
        return {**json.loads(path.read_text(encoding='utf-8')), 'shareId': share_id}

    @app.get('/c/{share_id}', include_in_schema=False)
    def shared_page(share_id: str):
        return FileResponse(STATIC/'index.html')

    def checked(request, input_type, image):
        """Validate before any work starts; returns (image bytes or None, error response or None)."""
        if input_type not in INPUT_TYPES:
            return None, JSONResponse({'detail': 'inputType 只能是 '+' 或 '.join(INPUT_TYPES)}, status_code=422)
        data = None
        if image is not None and image.filename:
            if image.content_type not in IMAGE_TYPES:
                return None, JSONResponse({'detail': '只接受 JPG、PNG、WebP 图片。'}, status_code=415)
            data = image.file.read(max_image_bytes+1)
            if len(data) > max_image_bytes:
                return None, JSONResponse({'detail': '图片太大，请压缩到 8 MB 以内。'}, status_code=413)
        if not allowed(visitor(request)):
            return None, JSONResponse({'detail': '这一小时调查次数太多了，稍后再试。'}, status_code=429)
        return data, None

    @app.post('/api/investigate')
    def run(request: Request, inputType: str = Form(...), userHint: str = Form(''),
            image: Optional[UploadFile] = File(None)):
        data, error = checked(request, inputType, image)
        if error:
            return error
        return keep(investigate(inputType, userHint, search, image=data, observe=observe, write=write, check_link=check_link))

    @app.post('/api/investigate/stream')
    def stream(request: Request, inputType: str = Form(...), userHint: str = Form(''),
               image: Optional[UploadFile] = File(None)):
        """Same investigation, but every stage is pushed as a server-sent event while it happens."""
        data, error = checked(request, inputType, image)
        if error:
            return error
        events = queue.Queue()

        def work():
            try:
                case = investigate(inputType, userHint, search, image=data, observe=observe, write=write,
                                   check_link=check_link, progress=lambda event, payload: events.put((event, payload)))
                events.put(('result', keep(case)))
            except Exception:
                events.put(('error', {'detail': '调查没有完成，请稍后再试。'}))
            finally:
                events.put(None)

        threading.Thread(target=work, daemon=True).start()

        def emit():
            while (item := events.get()) is not None:
                yield f'event: {item[0]}\ndata: {json.dumps(item[1], ensure_ascii=False)}\n\n'

        return StreamingResponse(emit(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    return app


def main():
    import uvicorn
    from .links import check_url
    from .minimax import default_observe, default_write
    uvicorn.run(create_app(observe=default_observe(), write=default_write(), check_link=check_url,
                           archive_dir=os.environ.get('SHIJI_ARCHIVE_DIR'), order_dir=os.environ.get('SHIJI_ORDER_DIR')),
                host=os.environ.get('SHIJI_HOST', '127.0.0.1'), proxy_headers=False,
                port=int(os.environ.get('SHIJI_PORT', '8025')))
