"""生成合成检测基准（cases + holdout）。禁止包含任何真实个人信息。"""

from __future__ import annotations

import base64
import json
import random
import string
import urllib.parse
from pathlib import Path


SEED = 20260806
OUT_DIR = Path(__file__).resolve().parents[1] / "tests" / "benchmark"


def _alnum(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(n))


def _b64url(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(string.ascii_letters + string.digits + "-_") for _ in range(n))


def _cases() -> list[dict]:
    rng = random.Random(SEED)
    out: list[dict] = []

    def add(category: str, text: str, sensitive: bool = True) -> None:
        out.append({"text": text, "expected_sensitive": sensitive, "expected_category": category})

    # email
    for i in range(7):
        add("email", f"user{i}@example.com")
        add("email", f"first.last{i}@mail.test")
    # phone
    for i in range(7):
        add("phone", f"138{ rng.randint(0, 99999999):08d}")
        add("phone", f"+86 139{ rng.randint(0, 99999999):08d}")
    # id_card
    for i in range(7):
        add("id_card", f"11010519900307{ rng.randint(0, 999):03d}{rng.choice('0123456789X')}")
    # credit_card（测试号）
    for i in range(7):
        add("credit_card", "4111 1111 1111 1111")
        add("credit_card", "5500 0000 0000 0004")
    # ip（文档保留段）
    for i in range(7):
        add("ip", f"203.0.113.{i + 1}")
        add("ip", f"198.51.100.{i + 1}")
    # api_key
    for i in range(7):
        add("api_key", f"sk-SYNTH{_alnum(rng, 24)}")
    # jwt
    for i in range(7):
        add("api_key", f"eyJ{_b64url(rng, 12)}.{_b64url(rng, 12)}.{_b64url(rng, 12)}")
    # pem
    for i in range(7):
        add(
            "private_key",
            "-----BEGIN RSA PRIVATE KEY-----\n"
            + _alnum(rng, 64)
            + "\n-----END RSA PRIVATE KEY-----",
        )
    # password_assign
    for i in range(7):
        add("secret", f"password=P@ssw0rd{i}")
        add("secret", f"api_key = sk-SYNTH{_alnum(rng, 20)}")
        add("secret", f"密码：abc12345{i}")
    # password_context
    for i in range(7):
        add("secret", f"我的密码是 hunter2{i}")
        add("secret", f"the password is P@ssw0rd{i}")
        add("secret", f"账户密码为 secret{i}")
    # vault_path
    for i in range(7):
        add("vault_path", f"C:\\Users\\alice\\my-vault\\note{i}.md")
        add("vault_path", f"/home/alice/my-vault/credentials{i}.md")
    # base64
    for i in range(7):
        add("email", base64.b64encode(f"alice{i}@example.com".encode()).decode())
        add("secret", base64.b64encode(f"password=hunter2{i}".encode()).decode())
    # hex
    for i in range(7):
        add("api_key", f"sk-SYNTH{_alnum(rng, 20)}".encode().hex())
    # urlencoded
    for i in range(7):
        add("secret", urllib.parse.quote(f"password=hunter2{i}"))
        add("email", urllib.parse.quote(f"marie{i}@acme.com"))
    # clean code
    for i in range(7):
        add("clean", "def call(endpoint, token):\n    return endpoint + token", False)
        add("clean", "configure the token endpoint and api key management", False)
        add("clean", "SELECT * FROM users WHERE id = %s", False)
        add("clean", "const x = { auth: 'bearer', path: '/v1/models' };", False)
        add("clean", "python -m unittest discover -s tests -v", False)
        add("clean", "Content-Type: application/json", False)
        add("clean", "请检查 token endpoint 的认证逻辑", False)
    # clean Chinese
    for i in range(7):
        add("clean", "请帮我配置密码策略", False)
        add("clean", "今天天气不错，我们去公园散步吧", False)
        add("clean", "这个项目的密钥管理方案需要评审", False)
        add("clean", "服务器配置检查完成", False)
        add("clean", "账号体系设计文档已更新", False)
    # clean encoded（holdout 专用增强）
    return out


def _holdout() -> list[dict]:
    rng = random.Random(SEED + 1)
    out: list[dict] = []

    def add(category: str, text: str, sensitive: bool = True) -> None:
        out.append({"text": text, "expected_sensitive": sensitive, "expected_category": category})

    for i in range(2):
        add("email", f"agent{i}@example.org")
        add("phone", f"137{ rng.randint(0, 99999999):08d}")
        add("id_card", f"31010119921212{ rng.randint(0, 999):03d}{rng.choice('0123456789X')}")
        add("credit_card", "6011 0000 0000 0004")
        add("ip", f"203.0.113.{20 + i}")
        add("api_key", f"sk-SYNTH{_alnum(rng, 28)}")
        add("secret", f"password=P@ssw0rd-holdout{i}")
        add("secret", f"我的密码是 hunter2-holdout{i}")
        add("vault_path", f"C:\\Users\\alice\\my-vault\\holdout{i}.md")
        add("email", base64.b64encode(f"holdout{i}@example.com".encode()).decode())
        add("api_key", f"sk-SYNTH{_alnum(rng, 24)}".encode().hex())
        add("secret", urllib.parse.quote(f"password=holdout{i}"))
        add("clean", "def retry(endpoint, attempts):\n    return attempts", False)
        add("clean", "api key management should follow the policy", False)
        add("clean", "请帮我检查密码策略的配置", False)
        add("clean", base64.b64encode(b"hello world").decode(), False)
        add("clean", "hello world".encode().hex(), False)
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cases = _cases()
    holdout = _holdout()
    (OUT_DIR / "cases.jsonl").write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8"
    )
    (OUT_DIR / "holdout.jsonl").write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in holdout), encoding="utf-8"
    )
    print(f"cases={len(cases)} holdout={len(holdout)} -> {OUT_DIR}")


if __name__ == "__main__":
    main()
