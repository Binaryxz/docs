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
        raise RuntimeError(f"runner retornou {resp.status_code}: {resp.text[:300]}")
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
