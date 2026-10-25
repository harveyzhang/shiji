"""来源链接检查：只有 404、410 或域名不存在才算打不开；反爬拦截和超时算「未核实」，不当成死链。

规则沿用 zhiyan-detective 的实测结论：本机检查常被证书不匹配或 412 反爬误判。
"""
import httpx

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36'
DEAD_HOST = ('nodename nor servname', 'Name or service not known')


def check_url(url, transport=None, timeout=4):
    """True = opens, False = gone, None = could not tell."""
    try:
        with httpx.Client(transport=transport, timeout=timeout, follow_redirects=True, headers={'User-Agent': UA}) as client:
            with client.stream('GET', url) as response:
                status = response.status_code
    except httpx.ConnectError as exc:
        text = str(exc)
        if 'CERTIFICATE' in text and url.startswith('https://'):
            return check_url('http://'+url[8:], transport=transport, timeout=timeout)
        return False if any(marker in text for marker in DEAD_HOST) else None
    except httpx.HTTPError:
        return None
    return False if status in (404, 410) else (True if status < 400 else None)
