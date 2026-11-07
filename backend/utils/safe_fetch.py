"""
安全的网页抓取（SSRF 防护）

用户提交的 URL 会由服务端直接请求，若不加限制，攻击者可借道服务器访问内网
（云厂商元数据 169.254.169.254、Redis、内部管理后台等）。这里统一收口：

- 仅允许 http / https
- 解析域名并校验**全部**解析结果：SaaS 模式拒绝私有 / 回环 / 链路本地 / 组播 /
  保留 / 未指定等非公网地址（IPv4 + IPv6，含 IPv4 映射等内嵌形式）；
  本地版是单用户抓取自己的网络，放行私有地址
- 连接时直接使用校验过的 IP（Host 头与 TLS SNI 仍为原域名），避免 DNS 重绑定绕过
- 手动跟随重定向（最多 MAX_REDIRECTS 跳），每一跳重新校验
- 超时（单次操作 + 总时长）与响应体大小上限

用法与 ``trafilatura.fetch_url`` 一致：返回 HTML 字符串，抓取失败返回 None；
URL 不合法或指向受限地址时抛出 ``UnsafeURLError``（ValueError 子类）。
拿到 HTML 后交给 ``trafilatura.extract`` 提取正文。
"""
from __future__ import annotations

import ipaddress
import socket
import time
from typing import Callable, List, Optional, Tuple

import httpx

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

MAX_REDIRECTS = 5
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_TOTAL_TIMEOUT_SECONDS = 45.0
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
_REDIRECT_STATUS = {301, 302, 303, 307, 308}
_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; UniNotebook/1.0)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

Resolver = Callable[[str, int], List[str]]


class UnsafeURLError(ValueError):
    """URL 不合法，或指向不允许访问的地址"""


def is_forbidden_address(ip) -> bool:
    """是否为不允许服务端访问的地址（非公网地址）。"""
    if isinstance(ip, str):
        ip = ipaddress.ip_address(ip.split("%", 1)[0])
    if ip.version == 6:
        # IPv4 映射 / 6to4 / Teredo 等形式内嵌了 IPv4 地址，按内嵌地址再判一次
        embedded = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if embedded is not None and is_forbidden_address(embedded):
            return True
        if ip.is_site_local:
            return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


def _resolve_host(host: str, port: int) -> List[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise UnsafeURLError(f"无法解析该网址的域名：{host}") from exc
    addresses: List[str] = []
    for _family, _type, _proto, _canonname, sockaddr in infos:
        address = str(sockaddr[0]).split("%", 1)[0]
        if address not in addresses:
            addresses.append(address)
    return addresses


def validate_url(
    url: str,
    *,
    allow_private: Optional[bool] = None,
    resolver: Optional[Resolver] = None,
) -> Tuple[httpx.URL, List[str]]:
    """
    校验 URL 并解析出允许连接的 IP 列表。

    Returns:
        (规范化后的 URL, 已通过校验的 IP 列表)
    Raises:
        UnsafeURLError: 协议不支持、无法解析，或（SaaS 模式下）指向非公网地址
    """
    if allow_private is None:
        allow_private = settings.is_local_mode

    try:
        parsed = httpx.URL(str(url or "").strip())
    except Exception as exc:
        raise UnsafeURLError("网址格式不正确") from exc

    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError("仅支持抓取 http / https 网址")
    if not parsed.host:
        raise UnsafeURLError("网址缺少主机名")

    host = parsed.raw_host.decode("ascii")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        addresses = (resolver or _resolve_host)(host, port)

    if not addresses:
        raise UnsafeURLError(f"无法解析该网址的域名：{host}")

    if not allow_private:
        for address in addresses:
            try:
                forbidden = is_forbidden_address(address)
            except ValueError:
                forbidden = True
            if forbidden:
                raise UnsafeURLError("出于安全考虑，不支持抓取内网、本机或保留地址的网页")

    return parsed.copy_with(fragment=None), addresses


def _decode_html(data: bytes) -> str:
    try:
        from trafilatura.utils import decode_file

        return decode_file(data)
    except Exception:
        return data.decode("utf-8", errors="replace")


def fetch_html(
    url: str,
    *,
    allow_private: Optional[bool] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    total_timeout: float = DEFAULT_TOTAL_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    transport: Optional[httpx.BaseTransport] = None,
    resolver: Optional[Resolver] = None,
) -> Optional[str]:
    """
    安全地抓取网页 HTML（同步，调用方应放到线程池中执行）。

    Returns:
        HTML 字符串；网络错误、非 2xx、内容过大、重定向过多时返回 None
    Raises:
        UnsafeURLError: 初始 URL 或任一跳重定向目标不合法 / 指向受限地址
    """
    deadline = time.monotonic() + total_timeout
    current = str(url or "").strip()

    # trust_env=False：不读取环境代理，确保实际连接的就是校验过的地址
    with httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        transport=transport,
        headers=_DEFAULT_HEADERS,
    ) as client:
        for _hop in range(MAX_REDIRECTS + 1):
            target, addresses = validate_url(current, allow_private=allow_private, resolver=resolver)
            if time.monotonic() > deadline:
                logger.warning(f"⚠️ 网页抓取超时，已放弃: {target}")
                return None
            response = _send_pinned(client, target, addresses)
            if response is None:
                return None
            try:
                if response.status_code in _REDIRECT_STATUS:
                    location = response.headers.get("location")
                    if not location:
                        return None
                    current = str(target.join(location))
                    continue

                if not 200 <= response.status_code < 300:
                    logger.warning(f"⚠️ 网页抓取返回非 2xx 状态: {response.status_code} {target}")
                    return None

                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    logger.warning(f"⚠️ 网页内容过大（{declared} 字节），已放弃: {target}")
                    return None

                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        logger.warning(f"⚠️ 网页内容超过 {max_bytes} 字节上限，已放弃: {target}")
                        return None
                    if time.monotonic() > deadline:
                        logger.warning(f"⚠️ 网页下载超时，已放弃: {target}")
                        return None
                if not body:
                    return None
                return _decode_html(bytes(body))
            except httpx.HTTPError as exc:
                logger.warning(f"⚠️ 网页下载失败: {target} {exc}")
                return None
            finally:
                response.close()

    logger.warning(f"⚠️ 网页重定向次数超过 {MAX_REDIRECTS} 次，已放弃: {url}")
    return None


def _send_pinned(client: httpx.Client, target: httpx.URL, addresses: List[str]) -> Optional[httpx.Response]:
    """按已校验的 IP 逐个尝试连接；Host 头与 TLS SNI 保持原域名。"""
    host = target.raw_host.decode("ascii")
    try:
        ipaddress.ip_address(host)
        is_ip_literal = True
    except ValueError:
        is_ip_literal = False

    last_error: Optional[Exception] = None
    for address in addresses if not is_ip_literal else [host]:
        request_url = target if is_ip_literal else target.copy_with(host=address)
        extensions = {}
        if target.scheme == "https" and not is_ip_literal:
            extensions["sni_hostname"] = host
        request = client.build_request(
            "GET",
            request_url,
            headers={"Host": target.netloc.decode("ascii")},
            extensions=extensions,
        )
        try:
            return client.send(request, stream=True)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            last_error = exc
            continue
        except httpx.HTTPError as exc:
            logger.warning(f"⚠️ 网页请求失败: {target} {exc}")
            return None

    logger.warning(f"⚠️ 无法连接网页: {target} {last_error}")
    return None
