"""Cliente HTTP da API interna do automaton-runner."""

import httpx

from .config import settings

_client = httpx.AsyncClient(timeout=30)


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.runner_token}"} if settings.runner_token else {}


async def _call(method: str, path: str, **kwargs) -> httpx.Response:
    try:
        resp = await _client.request(method, f"{settings.runner_url}{path}", headers=_headers(), **kwargs)
    except httpx.HTTPError as err:
        raise RuntimeError(f"runner indisponível em {settings.runner_url}: {err}") from err
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("error") or resp.text
        except ValueError:
            detail = resp.text
        raise RuntimeError(str(detail)[:300])
    return resp


async def create(spec: dict) -> dict:
    return (await _call("POST", "/agents", json=spec)).json()


async def statuses() -> dict[str, dict]:
    try:
        return {s["id"]: s for s in (await _call("GET", "/agents")).json() if s}
    except RuntimeError:
        return {}


async def start(agent_id: str) -> dict:
    return (await _call("POST", f"/agents/{agent_id}/start")).json()


async def stop(agent_id: str) -> dict:
    return (await _call("POST", f"/agents/{agent_id}/stop")).json()


async def delete(agent_id: str) -> None:
    await _call("DELETE", f"/agents/{agent_id}")


async def logs(agent_id: str, lines: int = 200) -> str:
    return (await _call("GET", f"/agents/{agent_id}/logs", params={"lines": lines})).text


async def health() -> bool:
    try:
        await _call("GET", "/health")
        return True
    except RuntimeError:
        return False


# ─── Carteiras (as chaves ficam no runner) ───────────────────────────


async def wallets() -> dict:
    return (await _call("GET", "/wallets")).json()


async def create_wallet(label: str) -> dict:
    return (await _call("POST", "/wallets", json={"label": label})).json()


async def wallet_balance(wallet_id: str) -> dict:
    return (await _call("GET", f"/wallets/{wallet_id}/balance")).json()


async def wallet_sign(wallet_id: str, message: str) -> dict:
    return (await _call("POST", f"/wallets/{wallet_id}/sign", json={"message": message})).json()


async def wallet_transfer(wallet_id: str, to: str, amount: str, asset: str) -> dict:
    return (await _call("POST", f"/wallets/{wallet_id}/transfer", json={"to": to, "amount": amount, "asset": asset}, timeout=90)).json()


async def tx_status(tx_hash: str) -> dict:
    return (await _call("GET", f"/wallets/tx/{tx_hash}")).json()
