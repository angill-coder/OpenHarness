"""本地测试工具: 用本应用的 TAI_APP_TOKEN 造一个合法的 X-Tai-Identity(JWE),
在 iOA 网关接好之前, 本地验证鉴权/按账号隔离能否跑通。**仅限本地测试, 勿对外暴露。**

用法:
  python3 app/gen_test_identity.py <登录名> [显示名]
token 优先取环境变量 TAI_APP_TOKEN, 缺省则从同目录 start_real.sh 读。
"""
import os, sys, json, base64, re


def _token():
    t = os.environ.get("TAI_APP_TOKEN")
    if t:
        return t
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        m = re.search(r'TAI_APP_TOKEN="([^"]+)"', open(os.path.join(here, "start_real.sh")).read())
        if m:
            return m.group(1)
    except Exception:
        pass
    sys.exit("找不到 TAI_APP_TOKEN(设环境变量, 或写在 app/start_real.sh)")


def _b64u(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def main():
    login = sys.argv[1] if len(sys.argv) > 1 else "testuser"
    disp = sys.argv[2] if len(sys.argv) > 2 else login
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    ident = {"LoginName": login, "DisplayName": disp, "Email": login + "@tencent.com"}
    header = _b64u(json.dumps({"alg": "dir", "enc": "A256GCM"}).encode())
    key = _token().encode()[:32]
    nonce = os.urandom(12)
    out = AESGCM(key).encrypt(nonce, json.dumps(ident).encode(), header.encode("ascii"))
    print(".".join([header, "", _b64u(nonce), _b64u(out[:-16]), _b64u(out[-16:])]))


if __name__ == "__main__":
    main()
