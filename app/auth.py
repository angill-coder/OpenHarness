"""
iOA (TAI/TOF) 智慧网关身份解析 —— OpenHarness 版。

做法参照 ai-strategy-hub/backend/app/middleware/tai_auth.py：
- 请求头 X-Tai-Identity 是一个 JWE(alg=dir, enc=A256GCM, 5 段: header.―.iv.ct.tag)。
- 密钥 = 应用 Token 的前 32 字节, 直接当 AES-256 key; AAD = JWE header 原文(parts[0])。
- 解出的 JSON 里取 LoginName 作为账号身份。

配置走环境变量(密钥不写死进 git):
  TAI_APP_TOKEN  应用 Token(必填, 否则一律解不出 -> 401)
  TAI_APP_ID     应用 ID(信息性, 解密用不到)

依赖 cryptography(本机已装; AES-GCM 非 stdlib)。
"""
import base64
import json
import os
from typing import Optional, Dict, Any

TAI_APP_ID = os.environ.get("TAI_APP_ID", "")
TAI_APP_TOKEN = os.environ.get("TAI_APP_TOKEN", "")

_IDENTITY_HEADER = "x-tai-identity"


def _b64url_decode(s: str) -> bytes:
    """Base64url 解码(补齐 padding), 与参考实现一致。"""
    s += "=" * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(s)


def decrypt_tai_identity(jwe_token: str) -> Optional[Dict[str, Any]]:
    """解密 X-Tai-Identity JWE(alg=dir, enc=A256GCM)。失败返回 None。"""
    if not jwe_token or not TAI_APP_TOKEN:
        return None
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        parts = jwe_token.split(".")
        if len(parts) != 5:
            print("[TAI] JWE 段数异常: 期望 5, 实得 %d" % len(parts))
            return None

        # parts: header, encrypted_key(dir 为空), iv, ciphertext, tag
        iv = _b64url_decode(parts[2])
        ciphertext = _b64url_decode(parts[3])
        tag = _b64url_decode(parts[4])

        key = TAI_APP_TOKEN.encode()[:32]          # AppToken 前 32 字节作 AES-256 key
        aad = parts[0].encode("ascii")             # AAD = JWE header 的 base64url 原文

        plaintext = AESGCM(key).decrypt(iv, ciphertext + tag, aad)
        return json.loads(plaintext.decode())
    except Exception as e:
        print("[TAI] 解密失败: %s" % e)
        return None


def current_user(headers) -> Optional[Dict[str, Any]]:
    """从请求头解析当前 iOA 登录用户。headers 支持 http.server 的 self.headers(大小写不敏感)。"""
    ident = ""
    try:
        ident = headers.get(_IDENTITY_HEADER, "") or headers.get("X-Tai-Identity", "")
    except Exception:
        ident = ""
    if not ident:
        return None
    return decrypt_tai_identity(ident)


def account_of(identity: Optional[Dict[str, Any]]) -> Optional[str]:
    """从身份 dict 取账号(登录名)。"""
    if not identity:
        return None
    return identity.get("LoginName") or None
