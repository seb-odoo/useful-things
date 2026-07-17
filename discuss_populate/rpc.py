import json
import aiohttp

BASE_URL = "http://localhost:8069"


def build_rpc_payload(params=None):
    return json.dumps({
        "jsonrpc": "2.0",
        "method": "call",
        "params": params or {},
    })


async def make_rpc(route, params=None, headers=None, cookies=None):
    h = {"Content-type": "application/json", **(headers or {})}
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        async with session.post(route, data=build_rpc_payload(params), headers=h, cookies=cookies) as resp:
            return json.loads(await resp.text())


async def make_orm_call(server_url, model, method, parameters, cookies=None):
    return await make_rpc(f"{server_url}/web/dataset/call_kw/{model}/{method}", {
        "method": method,
        "model": model,
        "args": parameters,
        "kwargs": {}
    }, cookies=cookies)


async def authenticate(url, login, password):
    result = await make_rpc(f"{url}/web/database/list")
    db_name = result["result"][0]
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        async with session.post(
            f"{url}/web/session/authenticate",
            data=build_rpc_payload({"login": login, "password": password, "db": db_name}),
            headers={"Content-type": "application/json"},
        ) as resp:
            return resp.cookies["session_id"].value,
