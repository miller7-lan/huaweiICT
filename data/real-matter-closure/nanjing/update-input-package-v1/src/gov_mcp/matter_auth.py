"""Local engineering or Nexent sign-in; neither claims a real business reviewer."""
import hashlib
import hmac
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from gov_mcp.matter_lifecycle import Principal, LifecycleError

ROLES = {"citizen", "staff", "maintainer", "professional"}


class LocalEngineeringAuth:
    mode = "local_engineering_accounts"

    def __init__(self, account_file):
        self.account_file = Path(account_file)

    def accounts(self):
        data = json.loads(self.account_file.read_text(encoding="utf-8"))
        if data.get("mode") != self.mode:
            raise LifecycleError("工程账户配置用途不匹配")
        return data["accounts"]

    async def login(self, username, password):
        account = self.accounts().get(username)
        salt = bytes.fromhex(account["salt"]) if account else b"\x00" * 16
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240000).hex()
        expected = account["password_hash"] if account else "0" * 64
        if not account or not account.get("enabled") or not hmac.compare_digest(actual, expected):
            raise LifecycleError("登录失败")
        return {"username": username, "id": account["id"]}

    async def resolve(self, credential):
        account = self.accounts().get(credential["username"])
        if not account or not account.get("enabled") or account["id"] != credential["id"] or account["role"] not in ROLES:
            raise LifecycleError("账户已停用或权限未配置")
        return Principal(account["id"], account["role"], "service")


class NexentAuth:
    mode = "nexent_accounts"

    def __init__(self, role_file, base_url="http://127.0.0.1:5010"):
        url = urlsplit(base_url)
        if (url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost")
                or url.username or url.password or url.path or url.query or url.fragment):
            raise ValueError("Nexent身份服务只允许已配置的本机HTTP入口")
        self.base_url, self.role_file = base_url, Path(role_file)

    async def login(self, username, password):
        async with httpx.AsyncClient(trust_env=False, timeout=15) as client:
            response = await client.post(self.base_url + "/user/signin",
                                         json={"email": username, "password": password})
            response.raise_for_status()
            token = response.json()["data"]["session"]["access_token"]
        if not isinstance(token, str) or not token:
            raise LifecycleError("登录失败")
        credential = {"token": token}
        await self.resolve(credential)
        return credential

    async def resolve(self, credential):
        async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
            response = await client.get(self.base_url + "/user/current_user_info",
                headers={"Authorization": "Bearer " + credential["token"]})
            response.raise_for_status()
            user = response.json()["data"]["user"]
        role_map = json.loads(self.role_file.read_text(encoding="utf-8"))
        tenant, user_id = user.get("tenant_id"), user.get("user_id")
        entry = role_map.get(tenant, {}).get(user_id, {})
        if not tenant or not user_id or entry.get("role") not in ROLES:
            raise LifecycleError("账户没有分配本事项角色")
        identity = hashlib.sha256((str(tenant) + "\0" + str(user_id)).encode()).hexdigest()
        return Principal(identity, entry["role"], "service")
