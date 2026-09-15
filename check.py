#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
宿舍出勤统计 · 提交前门禁检查
================================================================
用法：
    python check.py                 # 全部检查（退出码 0=通过 / 1=有失败项）
    python check.py --install-hook  # 安装为 git pre-commit 钩子，之后每次提交自动跑
退出码：
    0 = 无失败项（可能有警告）
    1 = 存在失败项（不应提交）

检查项
    [1] 隐私   —— 入库文件里不得出现个人真实数据（从私人备份动态提取，不硬编码）
    [2] 引用   —— HTML / manifest / sw.js 互相引用的文件都必须真实存在
    [3] PWA    —— manifest 合法性与必需字段、manifest/图标/SW 注册是否齐备
    [4] 处理器 —— 页面 onclick 引用的 App.xxx 是否都已定义（踩过：点下去没反应）
    [5] 语法   —— 内联 JS / sw.js 语法、manifest JSON 合法性
    [6] 泄漏   —— 疑似密钥、绝对路径、邮箱、手机号、异常大文件

设计约定
    · 本文件会被公开托管，因此它自身不得包含任何个人数据。
      「隐私」检查用的敏感词一律从 timetable-private.json 动态生成。
    · 私人备份 timetable-private.json 必须写在 .gitignore 里，永远不入库。
================================================================
"""

import io
import json
import os
import re
import subprocess
import sys

# Windows 下保证中文输出不乱码（重定向到文件时尤其重要）
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
PRIVATE = 'timetable-private.json'

# 是否把「成员姓名」纳入隐私扫描。
# 默认关闭：源码 seed 里的默认成员名与私人备份里的名字相同（都是占位名），
# 一定会误报。若你确实把真实姓名写进了源码，可改为 True；
# 那时建议先把源码 seed 的成员名改成与私人备份不同的占位名，避免噪音。
SCAN_MEMBER_NAMES = False

TEXT_EXT = ('.html', '.htm', '.js', '.mjs', '.json', '.webmanifest',
            '.css', '.py', '.txt', '.md', '.gitattributes', '.nojekyll')
MAX_FILE = 2 * 1024 * 1024          # 单个文件超过 2 MB 就警告

RESULTS = []


def add(level, name, detail=''):
    """level: PASS / FAIL / WARN"""
    RESULTS.append((level, name, detail))


def read(rel):
    p = os.path.join(ROOT, rel.replace('/', os.sep))
    if not os.path.exists(p):
        return None
    return io.open(p, encoding='utf-8', errors='replace').read()


def tracked_files():
    """返回 git 已跟踪的文件列表（未跟踪的私人文件自动被排除）。"""
    try:
        out = subprocess.run(['git', '-C', ROOT, 'ls-files'],
                             capture_output=True, timeout=30).stdout
        files = [f.strip() for f in out.decode('utf-8', 'replace').split('\n') if f.strip()]
        if files:
            return files
    except Exception:
        pass
    return ['index.html', 'sw.js', 'manifest.webmanifest', 'check.py']


def public_text_files():
    out = []
    for f in tracked_files():
        low = f.lower()
        if not low.endswith(TEXT_EXT):
            continue
        p = os.path.join(ROOT, f.replace('/', os.sep))
        if os.path.exists(p) and os.path.getsize(p) <= MAX_FILE:
            out.append(f)
    return out


# ---------------------------------------------------------------- [1] 隐私
def check_privacy():
    # 0) 私人文件必须被忽略，否则真实数据会被提交
    gi = read('.gitignore') or ''
    if PRIVATE not in gi:
        add('FAIL', '隐私', '.gitignore 未排除 %s —— 真实数据存在被提交的风险！' % PRIVATE)

    priv_path = os.path.join(ROOT, PRIVATE)
    words, frags = set(), set()

    if not os.path.exists(priv_path):
        add('WARN', '隐私', '未找到 %s，只能做通用扫描（无法逐项比对真实数据）' % PRIVATE)
    else:
        try:
            db = json.load(io.open(priv_path, encoding='utf-8'))
        except Exception as e:
            add('FAIL', '隐私', '%s 解析失败：%s' % (PRIVATE, e))
            return
        for c in db.get('courses', []):
            for k in ('name', 'room'):
                v = (c.get(k) or '').strip()
                if len(v) >= 2:
                    words.add(v)
                    # 由真实值派生前缀片段：取数字之前的部分
                    # 这样片段也来自真实数据，本脚本自身不含任何个人信息
                    pre = re.split(r'\d', v)[0].strip(' -—·')
                    if len(pre) >= 2:
                        frags.add(pre)
        if SCAN_MEMBER_NAMES:
            for m in db.get('members', []):
                v = (m.get('name') or '').strip()
                if len(v) >= 2:
                    words.add(v)

    hits = []
    for f in public_text_files():
        txt = read(f) or ''
        for w in sorted(words | frags):
            if w in txt:
                for i, line in enumerate(txt.split('\n'), 1):
                    if w in line:
                        hits.append('%s:%d  命中「%s」  %s' % (f, i, w, line.strip()[:66]))
                        break

    if hits:
        add('FAIL', '隐私', '入库文件里出现个人真实数据（%d 处）：\n    ' % len(hits)
            + '\n    '.join(hits[:14]))
    else:
        add('PASS', '隐私', '比对 %d 个实体 + %d 个前缀片段，无残留'
            % (len(words), len(frags)))


# ---------------------------------------------------------------- [2] 引用
def check_refs():
    problems = []
    html = read('index.html') or ''

    for m in re.finditer(r'(?:href|src)\s*=\s*"([^"#?]+)"', html):
        u = m.group(1).strip()
        if re.match(r'^(https?:|data:|mailto:|javascript:|#|//)', u):
            continue
        if not os.path.exists(os.path.join(ROOT, u.replace('/', os.sep))):
            problems.append('index.html 引用了不存在的文件：%s' % u)

    mf = load_manifest()
    mf_icons = set()
    if mf:
        for ic in mf.get('icons', []):
            src = ic.get('src', '')
            mf_icons.add(src)
            if not os.path.exists(os.path.join(ROOT, src.replace('/', os.sep))):
                problems.append('manifest 里的图标不存在：%s' % src)

    sw = read('sw.js') or ''
    am = re.search(r'var\s+ASSETS\s*=\s*\[(.*?)\]', sw, re.S)
    sw_assets = set()
    if am:
        for a in re.findall(r"['\"]\.?/?([^'\"]+)['\"]", am.group(1)):
            a = a.strip()
            sw_assets.add(a)
            if a in ('', '.'):
                continue
            if not os.path.exists(os.path.join(ROOT, a.replace('/', os.sep))):
                problems.append('sw.js 的 ASSETS 里文件不存在：%s' % a)
    else:
        problems.append('sw.js 里找不到 ASSETS 列表')

    for src in mf_icons:
        if src and src not in sw_assets:
            problems.append('manifest 用到 %s，但它不在 sw.js 的 ASSETS 里（离线时可能缺图标）' % src)

    if problems:
        add('FAIL', '引用', '发现 %d 处引用问题：\n    ' % len(problems) + '\n    '.join(problems[:10]))
    else:
        add('PASS', '引用', 'HTML/manifest/sw.js 三方引用一致，文件均存在')


def load_manifest():
    txt = read('manifest.webmanifest')
    if txt is None:
        return None
    try:
        return json.loads(txt)
    except Exception:
        return None


# ---------------------------------------------------------------- [3] PWA
def check_pwa():
    problems, warns = [], []
    html = read('index.html') or ''
    mf = load_manifest()

    if mf is None:
        problems.append('manifest.webmanifest 缺失或不是合法 JSON')
    else:
        for k in ('name', 'short_name', 'start_url', 'display', 'icons'):
            if k not in mf:
                problems.append('manifest 缺必需字段：%s' % k)
        if mf.get('display') != 'standalone':
            warns.append('manifest.display = %r，iOS 加到主屏后可能仍显示地址栏' % mf.get('display'))
        sizes = {str(i.get('sizes', '')) for i in mf.get('icons', [])}
        purposes = {str(i.get('purpose', '')) for i in mf.get('icons', [])}
        if '192x192' not in sizes:
            warns.append('缺 192x192 图标')
        if '512x512' not in sizes:
            warns.append('缺 512x512 图标')
        if 'maskable' not in purposes:
            warns.append('缺 maskable 图标（Android 自适应图标）')

    if 'rel="manifest"' not in html:
        problems.append('index.html 没有 <link rel="manifest">')
    if 'apple-touch-icon' not in html:
        problems.append('index.html 没有 apple-touch-icon（iOS 主屏图标会变成截图）')
    if 'serviceWorker' not in html or 'register(' not in html:
        problems.append('index.html 里没有注册 Service Worker')
    if not re.search(r'<meta[^>]+name=["\']viewport', html):
        problems.append('缺 viewport meta')
    if 'apple-mobile-web-app-capable' not in html:
        warns.append('缺 apple-mobile-web-app-capable（旧版 iOS 全屏需要）')

    sw = read('sw.js') or ''
    cm = re.search(r"var\s+CACHE\s*=\s*['\"]([^'\"]+)['\"]", sw)
    if not cm:
        problems.append('sw.js 里找不到 CACHE 版本常量')
    elif not re.search(r'v\d+', cm.group(1)):
        warns.append('CACHE 版本「%s」里没有 vN 形式，升级时容易漏改' % cm.group(1))

    if problems:
        add('FAIL', 'PWA', '发现 %d 处问题：\n    ' % len(problems) + '\n    '.join(problems))
    elif warns:
        add('WARN', 'PWA', '可用，但建议处理：\n    ' + '\n    '.join(warns))
    else:
        add('PASS', 'PWA', 'manifest / 图标 / SW 注册 / viewport 齐备')


# ---------------------------------------------------------------- [4] 处理器
def check_handlers():
    html = read('index.html') or ''
    used = set(re.findall(r'App\.([A-Za-z_]\w*)\s*\(', html))
    m = re.search(r'var\s+App\s*=\s*\{(.*?)\n\};', html, re.S)
    if not m:
        add('FAIL', '处理器', '在 index.html 里找不到 App 对象定义')
        return
    defined = set(re.findall(r'^ {2}([A-Za-z_]\w*)\s*:', m.group(1), re.M))
    missing = sorted(used - defined)
    if missing:
        add('FAIL', '处理器', 'onclick 引用了未定义的方法（点下去会没反应）：\n    ' + '\n    '.join(missing))
    else:
        add('PASS', '处理器', '%d 个被引用的方法均已定义' % len(used))


# ---------------------------------------------------------------- [5] 语法
def find_node():
    cands = ['node', 'node.exe']
    base = os.path.join(os.path.expanduser('~'), '.workbuddy', 'binaries', 'node', 'versions')
    if os.path.isdir(base):
        for v in sorted(os.listdir(base), reverse=True):
            for name in ('node.exe', 'bin', 'node'):
                p = os.path.join(base, v, name)
                if os.path.isfile(p):
                    cands.insert(0, p)
    for c in cands:
        try:
            r = subprocess.run([c, '--version'], capture_output=True, timeout=15)
            if r.returncode == 0:
                return c
        except Exception:
            continue
    return None


def check_syntax():
    fails, notes = [], []
    node = find_node()
    tmp = os.path.join(ROOT, '.gate-tmp')
    os.makedirs(tmp, exist_ok=True)

    html = read('index.html') or ''
    scripts = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, re.S)
    if not scripts:
        notes.append('index.html 里没有内联 script，跳过')
    elif node:
        for idx, js in enumerate(scripts):
            p = os.path.join(tmp, 'inline%d.js' % idx)
            io.open(p, 'w', encoding='utf-8').write(js)
            r = subprocess.run([node, '--check', p], capture_output=True, timeout=60)
            if r.returncode != 0:
                fails.append('index.html 内联脚本 #%d 语法错误：%s'
                             % (idx + 1, r.stderr.decode('utf-8', 'replace').strip()[:300]))
    else:
        notes.append('未找到 node，跳过 JS 语法检查')

    sw = read('sw.js')
    if sw and node:
        p = os.path.join(tmp, 'sw.js')
        io.open(p, 'w', encoding='utf-8').write(sw)
        r = subprocess.run([node, '--check', p], capture_output=True, timeout=60)
        if r.returncode != 0:
            fails.append('sw.js 语法错误：%s' % r.stderr.decode('utf-8', 'replace').strip()[:300])

    mf_txt = read('manifest.webmanifest')
    if mf_txt is not None:
        try:
            json.loads(mf_txt)
        except Exception as e:
            fails.append('manifest.webmanifest 不是合法 JSON：%s' % e)

    try:
        for f in os.listdir(tmp):
            os.remove(os.path.join(tmp, f))
        os.rmdir(tmp)
    except Exception:
        pass

    if fails:
        add('FAIL', '语法', '\n    '.join(fails))
    elif notes:
        add('WARN', '语法', '；'.join(notes))
    else:
        add('PASS', '语法', '内联 JS / sw.js / manifest 全部通过')


# ---------------------------------------------------------------- [6] 泄漏
SECRET_PATTERNS = [
    (r'sk-[A-Za-z0-9]{16,}', '疑似 API 密钥（sk- 开头）'),
    (r'ghp_[A-Za-z0-9]{20,}', '疑似 GitHub token'),
    (r'github_pat_[A-Za-z0-9_]{20,}', '疑似 GitHub PAT'),
    (r'AKIA[0-9A-Z]{16}', '疑似 AWS Access Key'),
    (r'(?i)\b(api[_-]?key|secret|passwd|password|access[_-]?token)\b\s*[:=]\s*["\'][^"\']{8,}',
     '疑似硬编码凭据'),
    (r'-----BEGIN [A-Z ]*PRIVATE KEY-----', '疑似私钥'),
    (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', '疑似邮箱地址'),
    (r'(?<!\d)1[3-9]\d{9}(?!\d)', '疑似手机号'),
    (r'\b[A-Za-z]:\\', 'Windows 绝对路径（部署后会失效）'),
    (r'(?<![\w/])/(?:Users|home)/[A-Za-z0-9._-]+/', '类 Unix 绝对路径'),
    (r'file:///', 'file:// 绝对地址'),
]

# 允许出现的例外（本脚本自身与合法用例），用文件名 -> 正则 的白名单
ALLOW = {
    'check.py': [r'\b[A-Za-z]:\\', r'file:///', r'(?<!\d)1[3-9]\d{9}(?!\d)', r'@'],
}


def check_secrets():
    hits = []
    for f in public_text_files():
        txt = read(f) or ''
        allow = '|'.join(ALLOW.get(os.path.basename(f), []))
        for pat, label in SECRET_PATTERNS:
            for m in re.finditer(pat, txt):
                if allow and re.search(allow, m.group(0)):
                    continue
                line_no = txt[:m.start()].count('\n') + 1
                hits.append('%s:%d  %s → %s' % (f, line_no, label, m.group(0)[:46]))
                break

    big = []
    for f in tracked_files():
        p = os.path.join(ROOT, f.replace('/', os.sep))
        if os.path.exists(p) and os.path.getsize(p) > MAX_FILE:
            big.append('%s（%.1f MB）' % (f, os.path.getsize(p) / 1048576.0))

    if hits:
        add('FAIL', '泄漏', '发现 %d 处疑似敏感内容：\n    ' % len(hits) + '\n    '.join(hits[:12]))
    elif big:
        add('WARN', '泄漏', '文件偏大，确认是否有必要入库：\n    ' + '\n    '.join(big))
    else:
        add('PASS', '泄漏', '无密钥 / 绝对路径 / 邮箱 / 手机号；文件体积正常')


# ------------------------------------------------------------ 安装 git 钩子
HOOK_SH = '''#!/bin/sh
# ============================================================
# 宿舍出勤统计 · 提交前门禁（由 check.py --install-hook 生成）
# 临时绕过（不推荐）：git commit --no-verify
# ============================================================

if [ ! -f check.py ]; then
    echo "[gate] 未找到 check.py，跳过门禁检查"
    exit 0
fi

PY=""
if command -v python >/dev/null 2>&1; then
    PY="python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
elif [ -x "$HOME/.workbuddy/binaries/python/versions/3.13.12/python.exe" ]; then
    PY="$HOME/.workbuddy/binaries/python/versions/3.13.12/python.exe"
fi

if [ -z "$PY" ]; then
    echo "[gate] 找不到 python 解释器，跳过门禁检查"
    exit 0
fi

echo "[gate] 正在执行提交前检查..."
if ! "$PY" check.py; then
    echo ""
    echo "[gate] 检查未通过，提交已被阻止。"
    echo "[gate] 修复后重新提交；确需绕过请用 git commit --no-verify"
    exit 1
fi
exit 0
'''


def install_hook():
    hooks = os.path.join(ROOT, '.git', 'hooks')
    if not os.path.isdir(hooks):
        print('未找到 .git/hooks 目录，可能不是 git 仓库，已跳过。')
        return 1
    p = os.path.join(hooks, 'pre-commit')
    existed = os.path.exists(p)
    io.open(p, 'w', encoding='utf-8', newline='\n').write(HOOK_SH)
    try:
        os.chmod(p, 0o755)
    except Exception:
        pass
    print('%s pre-commit 钩子：%s' % ('已覆盖' if existed else '已安装', p))
    print('之后每次 git commit 都会先跑本门禁；绕过用 git commit --no-verify')
    return 0


# ---------------------------------------------------------------- main
def main(argv=None):
    argv = argv or []
    if '--install-hook' in argv:
        return install_hook()
    print('=' * 64)
    print(' 宿舍出勤统计 · 提交前门禁检查')
    print(' 目标目录：%s' % ROOT)
    print('=' * 64)

    for fn in (check_privacy, check_refs, check_pwa,
               check_handlers, check_syntax, check_secrets):
        try:
            fn()
        except Exception as e:
            add('FAIL', fn.__name__, '检查器自身异常：%r' % e)

    order = {'FAIL': 0, 'WARN': 1, 'PASS': 2}
    for level, name, detail in sorted(RESULTS, key=lambda r: order[r[0]]):
        lines = detail.split('\n') if detail else ['']
        print('[%s] %-8s %s' % (level, name, lines[0]))
        for extra in lines[1:]:
            print('       %s' % extra)
        print('')

    n_pass = len([r for r in RESULTS if r[0] == 'PASS'])
    n_fail = len([r for r in RESULTS if r[0] == 'FAIL'])
    n_warn = len([r for r in RESULTS if r[0] == 'WARN'])
    print('-' * 64)
    print('结果：%d 通过 / %d 失败 / %d 警告' % (n_pass, n_fail, n_warn))
    if n_fail:
        print('>>> 存在失败项，请先修复再提交。')
    else:
        print('>>> 门禁通过。')
    return 1 if n_fail else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
