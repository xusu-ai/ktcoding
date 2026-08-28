#!/usr/bin/env python3
"""NowCoding HTTP server - 默认端口 3000，生产 SHOWCODE_PORT=13000"""
import http.server, os, sys, json, re, random, threading, time, socket
import gzip, io, shutil
from concurrent.futures import ThreadPoolExecutor
import urllib.request, urllib.error
socket.setdefaulttimeout(None)  # 永不超时，AI 生成可不间断进行
from urllib.parse import urlparse
from datetime import datetime

PORT = int(os.environ.get('SHOWCODE_PORT', 3000))
DIR = os.path.dirname(os.path.abspath(__file__))
PROJECTS_DIR = os.path.join(DIR, 'projects')
PROJECTS_JSON = os.path.join(PROJECTS_DIR, 'projects.json')
UPSTREAM_LLM = 'http://127.0.0.1:8104'
MODEL_ROUTES = {
    'Qwen3.8-27B': 'http://127.0.0.1:8105',
}

def _upstream_for(model):
    return MODEL_ROUTES.get(model, UPSTREAM_LLM)

def _llm_call(prompt, temp=0.95, model='deepseek-v4-flash', max_tokens=8192):
    upstream = _upstream_for(model)
    body = json.dumps({'model': model, 'messages': [{'role': 'user', 'content': prompt}],
        'temperature': temp, 'max_tokens': max_tokens, 'stream': False}).encode('utf-8')
    req = urllib.request.Request(upstream + '/v1/chat/completions', data=body,
        headers={'Content-Type': 'application/json', 'Content-Length': str(len(body))}, method='POST')
    msg = json.loads(urllib.request.urlopen(req, timeout=120).read().decode())['choices'][0]['message']
    return msg.get('content') or msg.get('reasoning_content') or ''

SUGGESTION_FB = {
    'zh': ['做一个霓虹灯登录页面','生成一个瀑布流图片展示页','创建一个贪吃蛇游戏','画一个粒子动画背景'],
    'en': ['Make a neon glow login page','Create a waterfall image gallery','Build a Snake game','Make a particle animation background'],
}
_sug_cache = {'zh': None, 'en': None, 'lock': threading.Lock()}

def _gen_suggestions(lang):
    is_zh = lang.startswith('zh')
    prompt = ('Generate 4 short creative web project ideas for an online code playground (seed={}). '
        'Keep each under 60 chars. Use HTML+CSS+JS. Respond in {}. '
        'Return ONLY a JSON array of 4 strings: ["a","b","c","d"]. No markdown.'
    ).format(random.randint(1, 99999), 'Chinese' if is_zh else 'English')
    try:
        text = _llm_call(prompt, model='Qwen3.8-27B').strip()
        if text.startswith('```'): text = text.split('\n', 1)[1] if '\n' in text else text.replace('```', '').strip()
        if text.endswith('```'): text = text[:-3].strip()
        s = json.loads(text)
        if isinstance(s, list) and len(s) >= 4: return s[:4]
    except Exception:
        pass
    return SUGGESTION_FB['zh' if is_zh else 'en']

def _refresh_suggestions(lang):
    s = _gen_suggestions(lang)
    with _sug_cache['lock']:
        _sug_cache[lang] = s
    return s

def safe_name(s):
    s = re.sub(r'[\\/:*?"<>|\n\r\t]', '', s or '').strip()
    return s[:50] or 'untitled'

def load_projects():
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    try:
        with open(PROJECTS_JSON, 'r', encoding='utf-8') as f: return json.load(f)
    except Exception:
        return []

def save_projects(projects):
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    with open(PROJECTS_JSON, 'w', encoding='utf-8') as f:
        json.dump(projects, f, ensure_ascii=False, indent=2)

# ---------- 项目磁盘清理：删除作品时同步清除本地文件 ----------
def _project_folder_path(folder):
    """校验 folder 是否为安全的项目目录名，返回绝对路径；不安全返回 None。
    只接受项目目录命名格式(如 056_2026-08-15_标题)，并拒绝路径穿越。"""
    if not folder or folder != os.path.basename(folder):  # 拒绝含路径分隔符/上级目录
        return None
    if not re.match(r'^\d{1,4}_', folder):                # 只匹配项目目录命名格式
        return None
    path = os.path.join(PROJECTS_DIR, folder)
    try:
        if os.path.commonpath([os.path.realpath(path), os.path.realpath(PROJECTS_DIR)]) \
                != os.path.realpath(PROJECTS_DIR):
            return None
    except ValueError:
        return None
    return path

def _delete_project_folder(project):
    """删除项目对应的磁盘目录(html/css/js 一起清除)。返回目录是否被删除。"""
    path = _project_folder_path((project or {}).get('folder') or '')
    if not path or not os.path.isdir(path):
        return False
    shutil.rmtree(path, ignore_errors=True)
    return not os.path.exists(path)

def _cleanup_orphan_folders():
    """启动清理：删除 projects.json 中已不存在的孤儿目录(历史遗留)。
    仅当 projects.json 成功解析时才执行，文件缺失/损坏时跳过，防止误删。"""
    try:
        with open(PROJECTS_JSON, 'r', encoding='utf-8') as f:
            projects = json.load(f)
    except Exception:
        return 0
    if not isinstance(projects, list):   # JSON 可解析但不是列表(数据异常)时跳过
        return 0
    refs = {p.get('folder') for p in projects if p.get('folder')}
    try:
        names = os.listdir(PROJECTS_DIR)
    except OSError:
        return 0
    removed = 0
    for name in sorted(names):
        path = _project_folder_path(name)
        if path and name not in refs and os.path.isdir(path):
            try:
                shutil.rmtree(path, ignore_errors=True)
                if not os.path.exists(path):
                    removed += 1
                    print('orphan folder removed: ' + name, flush=True)
            except Exception:
                pass
    return removed

# ---------- 访问统计(独立 IP / 点击量 / 日周月 / 大陆海外) ----------
# 本站 nginx 关联域名清单(server_name,去 www 归一化)
KNOWN_DOMAINS = {
    'htmlshow.com', 'htmlshow.cn', 'htmlshow.cc', 'htmlshow.net', 'htmlshow.space',
    'htmlcode.cn', 'htmllab.cn', 'htmllab.net',
    'showcode.chat', 'showcode.ink', 'showcode.link', 'showcode.live', 'showcode.show',
    'showcode.site', 'showcode.space', 'showcode.tv', 'showcode.work', 'showcode.world',
    'showcode.zone', 'showcoding.cn', 'nowcoding.cn', 'showhtml.cn', 'showmycode.cn', 'showyourcode.cn',
    'upcoding.cn', 'kkcoding.cn', 'gpucode.cn', 'ttcoding.cn', 'sscoding.cn',
    'qqcoding.top', 'qqcoding.cn', 'qqcoding.com', 'codegpu.shop', 'codegpu.online',
    'codegpu.fun', 'codegpu.cn', 'qqcmd.fun', 'api.nowcoding.cn',
    # 2026-08-09 证书拆分新增（110 域名 → 55 裸域名，www 自动归并）
    'appleclaw.live', 'appleclaw.online', 'appleclaw.top', 'appleclaw.video', 'appleclaw.vip',
    'cmdbot.cn', 'cmdcode.cn', 'dnmclaw.cn', 'dnmclaw.com',
    'qqclaw.club', 'qqclaw.shop', 'qqclaw.site', 'qqclaw.space', 'qqclaw.vip',
    'qqcmd.online', 'qqcmd.shop', 'yyyclaw.net', 'yyyclaw.online',
}

def _norm_host(h):
    h = (h or '').lower().strip()
    if h.startswith('www.'):
        h = h[4:]
    return h

STATS_FILE = os.path.join(DIR, 'stats.json')
_stats_lock = threading.RLock()  # RLock:允许持锁期间调用 _save_stats(内部再次加锁)而不死锁
# IP 归属地缓存写入 stats.json 的 'geo' 字段(ip -> {region, ts}),持久化避免每次轮询重复查询
GEO_TTL_MS = 24 * 3600 * 1000   # unknown 结果 24h 后重查,限流恢复后可自愈
_IPAPI_INTERVAL = 1.4           # ip-api.com 免费版限额 45 req/min,请求间隔 >= 1.34s
_geo_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='geo')
_geo_pending = set()            # 去重:同一 IP 只入队一次
_geo_pending_lock = threading.Lock()
_ipapi_lock = threading.Lock()
_ipapi_last = 0.0

# ---- 访客成分分类规则(按 IP 段 + 归属组织,用于统计页"访客成分分析") ----
_BOT_IP_PREFIX = ('66.249.', '17.', '40.77.', '157.55.', '52.167.')  # Googlebot / Apple / Bing
_BOT_ORG = ('palo alto', 'fortinet', 'facebook', 'meta')              # 安全扫描 / Meta
_MONITOR_IPS = ('8.8.8.8',)                                           # Google 公共 DNS(探测)
_MONITOR_IP_PREFIX = ('146.112.',)                                    # Cisco OpenDNS
_MONITOR_ORG = ('opendns', 'umbrella', 'quad9')
_CLOUD_ORG = ('alibaba', 'aliyun', 'amazon', 'aws', 'microsoft', 'azure', 'google', 'tencent',
              'ovh', 'contabo', 'hetzner', 'm247', 'hostroyale', 'zenlayer', 'leaseweb', 'ip volume',
              'gsl networks', 'ryamer', 'oculus', 'cccs', 'skyway', 'digitalocean', 'vultr',
              'choopa', 'worldstream', 'datacamp', 'cogent', 'fdcservers', 'colocrossing',
              'oneprovider', 'multacom', 'hivelocity', 'psychz', 'gcore', 'uab code200')
_CLOUD_IP_PREFIX = ('47.77.', '47.88.', '47.251.', '47.254.', '34.72.', '34.122.', '34.123.', '104.197.',
                    '52.43.', '44.227.', '3.80.', '3.95.', '98.90.', '107.150.', '23.88.', '146.70.',
                    '45.61.', '103.196.', '45.157.', '45.159.', '45.142.', '92.43.', '31.40.',
                    '213.255.', '119.12.', '185.255.', '194.124.', '198.55.', '206.204.', '216.251.',
                    '192.197.', '152.39.', '209.50.', '23.82.', '51.158.', '79.127.', '89.248.',
                    '94.102.', '138.199.', '193.19.', '82.139.')
_USER_ORG = ('wave broadband', 'deutsche telekom', 'kddi', 'ml telecom', 'comcast', 'charter',
             'spectrum', 'virgin', 'vodafone', 'orange', 't-mobile', 'verizon', 'at&t', 'softbank',
             'ntt', 'docomo', 'private customer', 'china mobile', 'china telecom', 'china unicom',
             'telefonica', 'reliance', 'jio')

def _load_stats():
    try:
        with open(STATS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'visits': []}

def _save_stats(data):
    with _stats_lock:
        tmp = STATS_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        os.replace(tmp, STATS_FILE)

def _client_ip(headers, addr):
    ip = headers.get('X-Real-IP') or ''
    if not ip:
        ip = (headers.get('X-Forwarded-For') or '').split(',')[0].strip()
    return ip or addr[0]

def _geo_lookup(ip):
    """网络查询 IP 归属地+访客成分(无缓存):返回 (region, category)。
    region: cn / overseas / unknown;category: bot / cloud / monitor / user / unknown。
    ip-api.com 主用(请求间按免费版限额节流,带 org/isp),ipwho.is 兜底。"""
    global _ipapi_last
    region, cat = 'unknown', 'unknown'
    for idx, url in enumerate(('http://ip-api.com/json/{ip}?fields=status,countryCode,org,isp',
                               'https://ipwho.is/{ip}')):
        if idx == 0:  # 节流 ip-api.com,避免 45 req/min 限额被并发打爆
            with _ipapi_lock:
                wait = _IPAPI_INTERVAL - (time.time() - _ipapi_last)
                if wait > 0:
                    time.sleep(wait)
                _ipapi_last = time.time()
        try:
            req = urllib.request.Request(url.format(ip=ip),
                                         headers={'User-Agent': 'Mozilla/5.0 (showcode-stats)'})
            with urllib.request.urlopen(req, timeout=3) as r:
                d = json.loads(r.read().decode('utf-8', 'replace'))
            org = ''
            if 'countryCode' in d:
                cc = d.get('countryCode')
                # 兼容 fields 缺 status 的响应:有 countryCode 且非 CN 即视为海外
                region = 'cn' if cc == 'CN' else ('overseas' if d.get('status') in (None, 'success') else 'unknown')
                org = d.get('org') or d.get('isp') or ''
            elif 'country_code' in d:
                region = 'cn' if d.get('country_code') == 'CN' else ('overseas' if d.get('success') else 'unknown')
                conn = d.get('connection') or {}
                org = conn.get('org') or conn.get('isp') or ''
            cat = _ip_category(ip, org)
            if region != 'unknown' and cat != 'unknown':
                break
        except Exception:
            continue
    return region, cat

def _ip_category(ip, org):
    """按 IP 段 + 归属组织判访客成分:bot / cloud / monitor / user / unknown"""
    lo = (org or '').lower()
    if ip.startswith(_BOT_IP_PREFIX):
        return 'bot'
    if ip in _MONITOR_IPS or ip.startswith(_MONITOR_IP_PREFIX) or any(k in lo for k in _MONITOR_ORG):
        return 'monitor'
    if any(k in lo for k in _BOT_ORG):
        return 'bot'
    if any(k in lo for k in _CLOUD_ORG) or ip.startswith(_CLOUD_IP_PREFIX):
        return 'cloud'
    if any(k in lo for k in _USER_ORG):
        return 'user'
    return 'unknown'

def _cached_region(ip):
    """只读 geo 缓存(无网络)。返回 (region, category, needs_query)
    旧缓存缺 category 字段时触发补查(一次网络查询补齐成分)。"""
    data = _load_stats()
    ent = (data.get('geo') or {}).get(ip)
    now = int(time.time() * 1000)
    if not ent:
        return 'unknown', 'unknown', True
    stale = now - ent.get('ts', 0) >= GEO_TTL_MS
    # 旧缓存(仅 region 无 cat)补查一次,补齐成分字段
    if 'cat' not in ent:
        return ent.get('region', 'unknown'), 'unknown', True
    if ent.get('region') == 'unknown' and stale:
        return 'unknown', 'unknown', True
    return ent.get('region', 'unknown'), ent.get('cat', 'unknown'), False

def _geo_region(ip):
    """后台执行:网络查询 + 写回持久化缓存(原子写)"""
    region, cat = _geo_lookup(ip)
    with _stats_lock:
        data = _load_stats()
        data.setdefault('geo', {})[ip] = {'region': region, 'cat': cat, 'ts': int(time.time() * 1000)}
        _save_stats(data)
    return region, cat

def _geo_async(ip):
    """异步补查 IP 归属地(去重;查询分散在后台,避免打爆第三方限流)"""
    with _geo_pending_lock:
        if ip in _geo_pending:
            return
        _geo_pending.add(ip)
    def _run():
        try:
            _geo_region(ip)
        except Exception:
            pass
        finally:
            with _geo_pending_lock:
                _geo_pending.discard(ip)
    _geo_executor.submit(_run)

def _geo_backfill_loop():
    """后台守护线程:每 2 分钟扫描今日访问 IP,补齐缺失的 geo/cat 缓存。
    独立于页面请求,保证统计页随时打开,成分数据都是最新的。"""
    while True:
        time.sleep(120)
        try:
            data = _load_stats()
            visits = data.get('visits', [])
            st = datetime.now()
            today0 = int(datetime(st.year, st.month, st.day).timestamp() * 1000)
            geo = data.get('geo') or {}
            for v in visits:
                if v['ts'] < today0:
                    continue
                ent = geo.get(v['ip'])
                if not ent or 'cat' not in ent:
                    _geo_async(v['ip'])
        except Exception:
            continue

def _stats_get(headers, addr):
    visits = _load_stats().get('visits', [])
    now_ms = int(time.time() * 1000)
    DAY = 86400000
    st = datetime.now()
    today0 = int(datetime(st.year, st.month, st.day).timestamp() * 1000)
    today = sum(1 for v in visits if v['ts'] >= today0)
    week = sum(1 for v in visits if v['ts'] >= now_ms - 7 * DAY)
    month = sum(1 for v in visits if v['ts'] >= now_ms - 30 * DAY)
    ips = len(set(v['ip'] for v in visits))
    # 核心指标:单日/近7天/近30天 去重后的独立 IP 数
    ips_today = len(set(v['ip'] for v in visits if v['ts'] >= today0))
    ips_week = len(set(v['ip'] for v in visits if v['ts'] >= now_ms - 7 * DAY))
    ips_month = len(set(v['ip'] for v in visits if v['ts'] >= now_ms - 30 * DAY))
    today_visits = [v for v in visits if v['ts'] >= today0]
    geo = {}
    comp = {}
    # 地域统计:读持久化缓存(秒回);缓存缺失/过期的 IP 丢后台异步补查,
    # 本次显示 unknown,下轮轮询即有归属地,且不会阻塞页面聚合
    today_ips = set(v['ip'] for v in today_visits)
    for ip in today_ips:
        region, cat, need = _cached_region(ip)
        geo[region] = geo.get(region, 0) + 1
        comp[cat] = comp.get(cat, 0) + 1
        if need:
            _geo_async(ip)
    by_day = []
    by_day_ips = []
    for i in range(13, -1, -1):
        s = today0 - i * DAY
        e = s + DAY
        day_visits = [v for v in visits if s <= v['ts'] < e]
        by_day.append({'d': datetime.fromtimestamp(s / 1000).strftime('%m-%d'),
                       'n': len(day_visits)})
        by_day_ips.append({'d': datetime.fromtimestamp(s / 1000).strftime('%m-%d'),
                           'n': len(set(v['ip'] for v in day_visits))})
    # 域名统计:每个域名当日独立 IP 数(覆盖全部关联域名,0 访问也列出)
    # 2026-08-09:裸域名 / www 前缀拆分统计(bare=裸域名, www=www.前缀)
    today_visits = [v for v in visits if v['ts'] >= today0]
    host_ips = {d: set() for d in KNOWN_DOMAINS}
    host_bare_ips = {d: set() for d in KNOWN_DOMAINS}
    host_www_ips = {d: set() for d in KNOWN_DOMAINS}
    other_bare_ips = {}
    other_www_ips = {}
    for v in today_visits:
        raw = (v.get('host') or '').lower().strip()
        h = _norm_host(raw)
        if h in KNOWN_DOMAINS:
            host_ips[h].add(v['ip'])
            if raw.startswith('www.'):
                host_www_ips[h].add(v['ip'])
            else:
                host_bare_ips[h].add(v['ip'])
        else:
            if raw.startswith('www.'):
                other_www_ips.setdefault(h or 'unknown', set()).add(v['ip'])
            else:
                other_bare_ips.setdefault(h or 'unknown', set()).add(v['ip'])
    other_ips = {h: (other_bare_ips.get(h, set()) | other_www_ips.get(h, set()))
                 for h in set(other_bare_ips) | set(other_www_ips)}
    domains_today = ([{'host': d, 'ips': len(s), 'bare': len(host_bare_ips[d]),
                       'www': len(host_www_ips[d]), 'known': True} for d, s in host_ips.items()] +
                     [{'host': h, 'ips': len(s), 'bare': len(other_bare_ips.get(h, set())),
                       'www': len(other_www_ips.get(h, set())), 'known': False}
                      for h, s in other_ips.items()])
    domains_today.sort(key=lambda x: -x['ips'])
    # 当日独立 IP 列表(隐私脱敏:只显示前两段)
    def _mask(ip):
        parts = ip.split('.')
        return '.'.join(parts[:2]) + '.*.*' if len(parts) >= 2 else ip
    ip_count = {}
    for v in today_visits:
        ip_count[v['ip']] = ip_count.get(v['ip'], 0) + 1
    ips_today_list = [{'masked': _mask(ip), 'clicks': n}
                      for ip, n in sorted(ip_count.items(), key=lambda x: -x[1])]
    return {'ok': True, 'total': len(visits), 'today': today, 'week': week, 'month': month,
            'ips': ips, 'ipsToday': ips_today, 'ipsWeek': ips_week, 'ipsMonth': ips_month,
            'cn': geo.get('cn', 0), 'overseas': geo.get('overseas', 0),
            'unknown': geo.get('unknown', 0), 'byDay': by_day, 'byDayIps': by_day_ips,
            'composition': comp,
            'domainsToday': domains_today, 'ipsTodayList': ips_today_list}

def _stats_visit(headers, addr):
    ip = _client_ip(headers, addr)
    host = (headers.get('Host') or 'unknown').split(':')[0].lower()
    with _stats_lock:  # 读-改-写整体加锁,避免覆盖并发 geo 缓存的写入
        data = _load_stats()
        visits = data.setdefault('visits', [])
        visits.append({'ip': ip, 'ts': int(time.time() * 1000), 'host': host})
        if len(visits) > 100000:
            data['visits'] = visits[-100000:]
        _save_stats(data)
    _geo_async(ip)  # 新访客后台查一次归属地(分散在全天,不触发限流)
    return {'ok': True}

def _qs(query):
    return {k: v for k, v in (p.split('=', 1) for p in query.split('&') if '=' in p)}

class Handler(http.server.SimpleHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)
    def log_message(self, *a): pass

    def _send_json(self, code, payload, extra=None):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        gzipped = False
        # 客户端支持 gzip 且响应足够大时压缩，避免大 JSON 走慢链路
        if 'gzip' in self.headers.get('Accept-Encoding', '') and len(body) >= 1024:
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode='wb', compresslevel=6) as gz:
                gz.write(body)
            body = buf.getvalue()
            gzipped = True
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        if gzipped:
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(body)))
        if extra:
            for k, v in extra.items(): self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_prices(self):
        """模型价格实时同步：每次请求实时从 llmapi (/api/models) 拉取最新单次价格，后端改价前端即同步显示"""
        try:
            req = urllib.request.Request('http://127.0.0.1:6000/api/models',
                                         headers={'User-Agent': 'nowcoding-price-sync'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8', 'replace'))
            prices = {}
            for s in data.get('suppliers', []):
                for m in s.get('models', []):
                    prices[m['name']] = m.get('price_per_request') or 0
            self._send_json(200, {'prices': prices},
                            {'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
                             'Pragma': 'no-cache'})
        except Exception as e:
            self._send_json(502, {'error': 'prices unavailable: ' + str(e)})

    def _proxy_llm(self, method, upstream_path=None, upstream=None, body=None):
        parsed = urlparse(self.path)
        path = upstream_path or parsed.path
        base = upstream or UPSTREAM_LLM
        target = base + path + ('?' + parsed.query if parsed.query else '')
        if body is None:
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length) if length > 0 else None
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ('host', 'connection', 'transfer-encoding', 'content-length')}
        req = urllib.request.Request(target, data=body, headers=headers, method=method)
        sent_headers = False
        try:
            resp = urllib.request.urlopen(req, timeout=600)
            self.send_response(resp.status)
            self.send_header('Transfer-Encoding', 'chunked')
            for k, v in resp.headers.items():
                if k.lower() not in ('transfer-encoding', 'content-length', 'connection'):
                    self.send_header(k, v)
            self.end_headers()
            sent_headers = True
            while True:
                line = resp.readline()
                if not line:
                    self.wfile.write(b'0\r\n\r\n'); self.wfile.flush(); break
                self.wfile.write(format(len(line), 'x').encode() + b'\r\n' + line + b'\r\n')
                self.wfile.flush()
        except urllib.error.HTTPError as e:
            b = e.read()
            if sent_headers:
                self.wfile.write(b'0\r\n\r\n'); self.wfile.flush()
            else:
                self.send_response(e.code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(b)))
                self.end_headers()
                self.wfile.write(b)
        except Exception as e:
            if sent_headers:
                try: self.wfile.write(b'0\r\n\r\n'); self.wfile.flush()
                except Exception: pass
            else:
                self._send_json(502, {'error': 'Upstream unavailable: ' + str(e)})

    def do_OPTIONS(self):
        self._send_json(200, {}, {'Allow': 'GET, POST, DELETE, OPTIONS'}) if urlparse(self.path).path.startswith('/api/') else super().do_OPTIONS()

    def do_GET(self):
        p = urlparse(self.path).path
        if p.startswith('/v1/'): self._proxy_llm('GET')
        elif p == '/api/projects': self._send_json(200, load_projects(), {'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0', 'Pragma': 'no-cache'})
        elif p == '/api/suggestions': self._send_suggestions()
        elif p == '/api/fetch-url': self._fetch_url()
        elif p == '/api/stats/visit': self._send_json(200, _stats_visit(self.headers, self.client_address))
        elif p == '/api/prices': self._send_prices()
        elif p == '/api/stats': self._send_json(200, _stats_get(self.headers, self.client_address))
        else: super().do_GET()

    def do_DELETE(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/projects':
            try:
                pid = _qs(parsed.query).get('id', '')
                projects = load_projects()
                target = next((p for p in projects if p.get('id') == pid), None)
                if target is None:
                    return self._send_json(404, {'ok': False, 'error': 'project not found'})
                save_projects([p for p in projects if p.get('id') != pid])
                removed = _delete_project_folder(target)  # 同步删除本地文件/目录
                self._send_json(200, {'ok': True, 'removed': removed,
                                      'folder': target.get('folder') or ''})
            except Exception as e:
                self._send_json(500, {'ok': False, 'error': str(e)})
        else:
            self._send_json(404, {'ok': False, 'error': 'not found'})

    def do_POST(self):
        p = urlparse(self.path).path
        if p.startswith('/v1/'): self._proxy_llm('POST')
        elif p == '/api/chat':
            upstream = None
            try:
                length = int(self.headers.get('Content-Length', 0))
                raw = self.rfile.read(length) if length > 0 else b''
                body_obj = json.loads(raw) if raw else {}
                upstream = _upstream_for(body_obj.get('model', ''))
                self._proxy_llm('POST', '/v1/chat/completions', upstream=upstream, body=raw)
            except Exception:
                self._send_json(400, {'ok': False, 'error': 'bad chat body'})
        elif p == '/api/save': self._save_project()
        else: self._send_json(404, {'ok': False, 'error': 'not found'})

    def _send_suggestions(self):
        q = _qs(urlparse(self.path).query)
        lang = 'zh' if q.get('lang', 'zh').startswith('zh') else 'en'
        if q.get('refresh') == '1':
            s = _refresh_suggestions(lang)
        else:
            with _sug_cache['lock']:
                s = _sug_cache[lang]
            if not s:
                threading.Thread(target=_refresh_suggestions, args=(lang,), daemon=True).start()
                s = SUGGESTION_FB[lang]
        self._send_json(200, s, {'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0', 'Pragma': 'no-cache'})

    def _read_body(self):
        """读取请求体;若客户端以 gzip 压缩(Content-Encoding: gzip)则解压。"""
        length = int(self.headers.get('Content-Length', 0) or 0)
        raw = self.rfile.read(length) if length > 0 else b''
        if 'gzip' in self.headers.get('Content-Encoding', '').lower() and raw:
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass
        return raw

    def _save_project(self):
        try:
            data = json.loads(self._read_body().decode('utf-8'))
            html, css, js = (data.get('html') or '').strip(), (data.get('css') or '').strip(), (data.get('js') or '').strip()
            code, title = data.get('code') or '', safe_name(data.get('title') or 'untitled')

            os.makedirs(PROJECTS_DIR, exist_ok=True)
            num = max([int(m.group(1)) for name in os.listdir(PROJECTS_DIR) if name != 'projects.json'
                       and (m := re.match(r'^(\d+)_', name))] + [0]) + 1
            date_str = datetime.now().strftime('%Y-%m-%d')
            base_name = '{:03d}_{}_{}'.format(num, date_str, title)
            folder = os.path.join(PROJECTS_DIR, base_name)
            i = 1
            while os.path.exists(folder):
                folder = os.path.join(PROJECTS_DIR, '{}_{}'.format(base_name, i))
                base_name = os.path.basename(folder); i += 1
            os.makedirs(folder)

            saved = []
            for ext, content in [('html', html), ('css', css), ('js', js)]:
                if content:
                    with open(os.path.join(folder, base_name + '.' + ext), 'w', encoding='utf-8') as f: f.write(content)
                    saved.append(ext)

            projects = load_projects()
            project = {
                'id': data.get('id') or datetime.now().strftime('%y%m%d%H%M%S') + os.urandom(2).hex(),
                'code': code, 'title': title, 'views': int(data.get('views', 0)), 'runs': int(data.get('runs', 0)),
                'createdAt': data.get('createdAt') or int(datetime.now().timestamp() * 1000),
                'folder': base_name, 'saved': saved, 'num': num, 'date': date_str,
            }
            projects.insert(0, project)
            save_projects(projects)
            self._send_json(200, {'ok': True, 'projects': projects, 'project': project})
        except Exception as e:
            self._send_json(500, {'ok': False, 'error': str(e)})

    # ---------- 网址解析：抓取站点 HTML/CSS/JS 与素材，供前端「解析」功能 ----------
    _FETCH_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                 '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')

    def _fetch_url(self):
        from urllib.parse import parse_qs
        q = parse_qs(urlparse(self.path).query)
        url = (q.get('url') or [''])[0].strip()
        if not re.match(r'^https?://', url, re.I):
            return self._send_json(400, {'ok': False, 'error': 'url 必须以 http(s):// 开头'})
        host = urlparse(url).hostname or ''
        try:
            addrs = [a[4][0] for a in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]
        except Exception:
            return self._send_json(502, {'ok': False, 'error': '域名解析失败'})
        # 防 SSRF：拒绝内网/环回/保留地址
        import ipaddress
        for ip in addrs:
            try:
                if ipaddress.ip_address(ip).is_private or ipaddress.ip_address(ip).is_loopback \
                        or ipaddress.ip_address(ip).is_reserved or ipaddress.ip_address(ip).is_link_local:
                    return self._send_json(403, {'ok': False, 'error': '不允许解析内网地址'})
            except ValueError:
                pass
        try:
            req = urllib.request.Request(url, headers={'User-Agent': self._FETCH_UA,
                'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8'})
            resp = urllib.request.urlopen(req, timeout=20)
            ctype = resp.headers.get('Content-Type', '')
            raw = resp.read()
            if 'html' not in ctype:
                return self._send_json(200, {'ok': True, 'html': raw.decode('utf-8', 'replace'),
                    'css': '', 'js': '', 'title': url, 'assets': 0, 'single': True, 'url': url})
            html = raw.decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            return self._send_json(502, {'ok': False, 'error': '目标站返回 HTTP %s' % e.code})
        except Exception as e:
            return self._send_json(502, {'ok': False, 'error': '抓取失败：%s' % str(e)})
        try:
            out = self._parse_site(html, url)
            out['ok'] = True
            self._send_json(200, out)
        except Exception as e:
            self._send_json(500, {'ok': False, 'error': '解析失败：%s' % str(e)})

    def _parse_site(self, html, base_url):
        """抓取外链 CSS/JS 合并到各自代码区；相对路径素材(图片/字体)下载转 base64 内联。"""
        from urllib.parse import urljoin
        import base64 as b64
        assets = 0

        def full(u):
            return urljoin(base_url, u.strip()) if u.strip() and not u.strip().startswith(('data:', 'javascript:', '#')) else None

        def inline(u, mime_hint=''):
            """下载素材转 data URI；失败返回 None。"""
            nonlocal assets
            fu = full(u)
            if not fu or fu.startswith('http') is False:
                return None
            try:
                req = urllib.request.Request(fu, headers={'User-Agent': self._FETCH_UA})
                r = urllib.request.urlopen(req, timeout=10)
                d = r.read()
                if len(d) > 4 * 1024 * 1024:
                    return None
                ct = r.headers.get('Content-Type', mime_hint).split(';')[0].strip() or mime_hint
                if ct and not ct.startswith(('image/', 'font/', 'application/octet')):
                    return None
                assets += 1
                return 'data:' + (ct or 'application/octet-stream') + ';base64,' + b64.b64encode(d).decode('ascii')
            except Exception:
                return None

        title = ''
        m = re.search(r'<title[^>]*>([^<]*)</title>', html, re.I | re.S)
        if m:
            title = m.group(1).strip()

        # 1) 外链 CSS：抓内容合并，并从 HTML 移除 link 标签
        css_parts = []
        for st in re.findall(r'<style[^>]*>([\s\S]*?)</style>', html, re.I):
            css_parts.append(st)
        for lm in re.finditer(r'<link[^>]*rel=["\']stylesheet["\'][^>]*>', html, re.I):
            tag = lm.group(0)
            hm = re.search(r'href=["\']([^"\']+)["\']', tag, re.I)
            if not hm:
                continue
            fu = full(hm.group(1))
            if not fu or fu.startswith('http') is False:
                continue
            try:
                req = urllib.request.Request(fu, headers={'User-Agent': self._FETCH_UA})
                r = urllib.request.urlopen(req, timeout=10)
                ct = r.headers.get('Content-Type', '')
                d = r.read()
                if 'css' in ct or fu.endswith('.css') or 'text/plain' in ct:
                    css_parts.append(d.decode('utf-8', 'replace'))
                    html = html.replace(tag, '')
            except Exception:
                pass
        # CSS 内相对素材转 base64
        css = '\n'.join(css_parts)
        css = re.sub(r'url\(\s*["\']?([^"\')\s]+)["\']?\s*\)',
                     lambda mm: ('url("' + (inline(mm.group(1), 'image/*') or mm.group(0)[4:-1]) + '")'), css)

        # 2) 外链 JS：抓内容合并，并从 HTML 移除 script[src]
        js_parts = []
        for sm in re.finditer(r'<script[^>]*src=["\']([^"\']+)["\'][^>]*>\s*</script>', html, re.I):
            fu = full(sm.group(1))
            if not fu or fu.startswith('http') is False:
                continue
            try:
                req = urllib.request.Request(fu, headers={'User-Agent': self._FETCH_UA})
                r = urllib.request.urlopen(req, timeout=10)
                ct = r.headers.get('Content-Type', '')
                d = r.read()
                if 'javascript' in ct or 'ecmascript' in ct or fu.endswith('.js') or 'text/plain' in ct:
                    js_parts.append(d.decode('utf-8', 'replace'))
                    html = html.replace(sm.group(0), '')
            except Exception:
                pass
        js = '\n'.join(js_parts)

        # 3) HTML 内相对素材(<img src> 等)转 base64
        def img_inline(mm):
            u = mm.group(1)
            if u.startswith(('data:', 'http:', 'https:', '//', '#')):
                return mm.group(0)
            d = inline(u, 'image/*')
            return mm.group(0).replace(mm.group(1), d) if d else mm.group(0)

        html = re.sub(r'(<img[^>]*\ssrc=)["\']([^"\']+)["\']', lambda mm: img_inline(mm), html, flags=re.I)

        return {'html': html, 'css': css, 'js': js, 'title': title, 'assets': assets, 'url': base_url}


if __name__ == '__main__':
    threading.Thread(target=_geo_backfill_loop, daemon=True).start()  # 后台定期补查 geo/cat 缓存
    _cleanup_orphan_folders()  # 启动清理:删除与 projects.json 不同步的历史遗留目录
    BIND = os.environ.get('SHOWCODE_BIND', '127.0.0.1')
    httpd = http.server.ThreadingHTTPServer((BIND, PORT), Handler)
    print("NowCoding server running on http://{}:{}".format(BIND, PORT), flush=True)
    httpd.serve_forever()
