"""素材抽取（P1-11）—— URL / PDF / TXT / DOCX / 纯文本 统一入口。

依赖全部优雅降级：trafilatura/pymupdf 未安装时自动退回标准库解析。
"""
from __future__ import annotations
import os, re, ssl, urllib.error, urllib.request


def extract(source: str) -> tuple[str, str]:
    """返回 (纯文本, 来源类型 url|pdf|txt|docx|raw)。"""
    s = (source or "").strip()
    kind = detect_source(s)
    if kind == "url":
        return _from_url(s), "url"
    if kind == "pdf":
        return _from_pdf(s), "pdf"
    if kind == "docx":
        return _from_docx(s), "docx"
    if kind == "txt":
        with open(s, encoding="utf-8", errors="ignore") as f:
            return f.read(), "txt"
    return s, "raw"


def detect_source(s: str) -> str:
    """纯路由检测（不产生网络/磁盘 IO，可安全单测）。"""
    if re.match(r"^https?://", s):
        return "url"
    if os.path.exists(s) and s.lower().endswith(".pdf"):
        return "pdf"
    if os.path.exists(s) and s.lower().endswith(".docx"):
        return "docx"
    if os.path.exists(s):
        return "txt"
    return "raw"


def _from_url(url: str) -> str:
    try:  # 首选 trafilatura（正文抽取质量最好）
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded)
            if text:
                return text
    except ImportError:
        pass
    # 退回标准库：抓 HTML 剥标签（效果一般但零依赖）
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                html = r.read().decode("utf-8", errors="ignore")
        except urllib.error.URLError as e:      # Windows 常见：证书链不全
            if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                ctx = ssl._create_unverified_context()
                with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
                    html = r.read().decode("utf-8", errors="ignore")
            else:
                raise
    except Exception as e:
        raise RuntimeError(f"URL 抓取失败: {e}") from e
    html = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()[:20000]


def _from_pdf(path: str) -> str:
    try:
        import fitz  # pymupdf
        doc = fitz.open(path)
        return "\n".join(page.get_text() for page in doc)
    except ImportError as e:
        raise RuntimeError("需要 pymupdf：pip install pymupdf") from e


def _from_docx(path: str) -> str:
    try:
        import docx
        d = docx.Document(path)
        return "\n".join(p.text for p in d.paragraphs)
    except ImportError as e:
        raise RuntimeError("需要 python-docx：pip install python-docx") from e


# ---------------- RSS / Atom（P1-14 批量输入） ----------------

def _fetch(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return r.read()
        raise RuntimeError(f"抓取失败: {e}") from e


def rss_items(url: str, limit: int = 10) -> dict:
    """解析 RSS 2.0 / Atom，返回 {feed_title, items:[{title,link,summary,date}]}。
    url 可为 http(s) 地址或本地 .xml 文件（便于离线测试）。"""
    import xml.etree.ElementTree as ET
    if os.path.exists(url) and url.lower().endswith((".xml", ".rss")):
        raw = open(url, "rb").read()
    else:
        raw = _fetch(url)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise RuntimeError(f"不是合法的 RSS/Atom：{e}") from e

    def _text(el):
        return (el.text or "").strip() if el is not None else ""

    ns_atom = "{http://www.w3.org/2005/Atom}"
    items = []
    if root.tag.endswith("feed"):                              # Atom
        feed_title = _text(root.find(f"{ns_atom}title"))
        for entry in root.findall(f"{ns_atom}entry")[:limit]:
            link_el = entry.find(f"{ns_atom}link")
            href = link_el.get("href", "") if link_el is not None else ""
            items.append({
                "title": _text(entry.find(f"{ns_atom}title")),
                "link": href,
                "summary": _text(entry.find(f"{ns_atom}summary"))
                           or _text(entry.find(f"{ns_atom}content")),
                "date": _text(entry.find(f"{ns_atom}updated")),
            })
    else:                                                       # RSS 2.0
        ch = root.find("channel")
        feed_title = _text(ch.find("title")) if ch is not None else ""
        for it in (ch.findall("item") if ch is not None else [])[:limit]:
            items.append({
                "title": _text(it.find("title")),
                "link": _text(it.find("link")),
                "summary": _text(it.find("description")),
                "date": _text(it.find("pubDate")),
            })
    return {"feed_title": feed_title, "items": [i for i in items if i["title"]]}
