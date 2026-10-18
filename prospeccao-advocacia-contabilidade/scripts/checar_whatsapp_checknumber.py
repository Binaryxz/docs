"""
Etapa 5 — confirma se o número tem WhatsApp via CheckNumber.AI (API paga, roda
na infraestrutura deles: você não conecta nenhum número seu, sem QR code, sem
risco de bloqueio).

Chave da API em CHECKNUMBER_API_KEY (ou --api-key).

Subcomandos:
  saldo
      GET /v1/balance. O valor é em DÓLAR (US$), não "créditos".

  checar <numeros.txt> <resultado.csv> [--task-id ID]
      Sobe o .txt (um E.164 com "+" por linha — gerado por
      preparar_telefones.py --txt-checknumber) com task_type=ws, faz polling
      em POST /v1/gettasks até status=exported, baixa o zip de result_url e
      grava <resultado.csv> com colunas telefone_e164,tem_whatsapp (sim/nao).
      O task_id é gravado em <resultado.csv>.task_id assim que a task é criada:
      se o polling estourar o timeout ou cair, rode de novo com --task-id
      (ou só rode de novo: o task_id salvo é reaproveitado) — NÃO resubmeta,
      senão paga duas vezes.

  mesclar <leads_tel.csv> <resultado.csv> <saida.csv>
      Junta tem_whatsapp no CSV de leads (por telefone_e164). Leads sem
      resultado ficam com tem_whatsapp vazio.

Calibre antes de rodar em volume: monte um .txt com 1-2 números que você
sabe que têm WhatsApp (e 1 que não tem) e rode `checar` nele.

Atenção ao cache da API (campo cached_count): número já checado antes, por
você ou por qualquer outro cliente, volta do cache. Reconsultar um "nao"
esperando que vire "sim" não funciona — volta idêntico.

Uso:
    python checar_whatsapp_checknumber.py saldo
    python checar_whatsapp_checknumber.py checar numeros_advocacia.txt whatsapp_advocacia.csv
    python checar_whatsapp_checknumber.py mesclar leads_advocacia_tel.csv whatsapp_advocacia.csv leads_advocacia_wa.csv
"""

import argparse
import csv
import io
import os
import re
import sys
import time
import zipfile

import requests

BASE_URL = "https://api.checknumber.ai"
csv.field_size_limit(sys.maxsize)


def digitos(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


class CheckNumber:
    def __init__(self, api_key: str, timeout_http: int = 60):
        self.s = requests.Session()
        self.s.headers["X-API-Key"] = api_key
        self.timeout_http = timeout_http

    def _json(self, resp: requests.Response) -> dict:
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code} em {resp.request.method} {resp.url}: {resp.text[:500]}")
        return resp.json()

    def saldo(self) -> float:
        return float(self._json(self.s.get(f"{BASE_URL}/v1/balance", timeout=self.timeout_http))["balance"])

    def submeter(self, caminho_txt: str) -> dict:
        with open(caminho_txt, "rb") as f:
            resp = self.s.post(
                f"{BASE_URL}/v1/tasks",
                files={"file": (os.path.basename(caminho_txt), f, "text/plain")},
                data={"task_type": "ws"},
                timeout=self.timeout_http,
            )
        return self._json(resp)

    def consultar(self, task_id: str) -> dict:
        return self._json(self.s.post(f"{BASE_URL}/v1/gettasks", data={"task_id": task_id}, timeout=self.timeout_http))

    def aguardar(self, task_id: str, intervalo: float, timeout: float) -> dict:
        inicio = time.monotonic()
        while True:
            info = self.consultar(task_id)
            status = info.get("status")
            decorrido = time.monotonic() - inicio
            extras = {k: info[k] for k in ("total", "success", "failure", "cached_count") if k in info}
            print(f"  [{decorrido:>5.0f}s] status={status} {extras}", file=sys.stderr)
            if status == "exported":
                return info
            if status in ("failed", "error", "canceled", "cancelled"):
                raise RuntimeError(f"task {task_id} terminou com status={status}: {info}")
            if decorrido >= timeout:
                raise TimeoutError(
                    f"task {task_id} ainda em status={status} após {timeout:.0f}s — rode de novo com "
                    f"--task-id {task_id} pra continuar o polling (NÃO resubmeta)"
                )
            time.sleep(intervalo)

    def baixar_resultado(self, result_url: str) -> dict[str, str]:
        resp = requests.get(result_url, timeout=max(self.timeout_http, 300))
        resp.raise_for_status()
        resultado: dict[str, str] = {}
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            nome = next((n for n in z.namelist() if n.rsplit("/", 1)[-1] == "all.csv"), None)
            if nome is None:
                raise RuntimeError(f"zip do resultado sem all.csv: {z.namelist()}")
            with z.open(nome) as f:
                for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
                    num = digitos(row.get("number", ""))
                    if num:
                        resultado[num] = "sim" if (row.get("activated") or "").strip().lower() == "yes" else "nao"
        return resultado


def cmd_saldo(cn: CheckNumber, _args) -> None:
    print(f"saldo: US$ {cn.saldo():.2f}")


def cmd_checar(cn: CheckNumber, args) -> None:
    with open(args.numeros, encoding="utf-8") as f:
        numeros = [l.strip() for l in f if l.strip()]
    if not numeros:
        print("arquivo de números vazio", file=sys.stderr)
        sys.exit(1)
    invalidos = [n for n in numeros if not re.fullmatch(r"\+\d{10,15}", n)]
    if invalidos:
        print(f"[erro] {len(invalidos)} linha(s) fora do formato E.164 com '+', ex.: {invalidos[:3]}", file=sys.stderr)
        sys.exit(1)

    arquivo_task = args.resultado + ".task_id"
    task_id = args.task_id
    if not task_id and os.path.exists(arquivo_task):
        task_id = open(arquivo_task).read().strip()
        print(f"reaproveitando task_id salvo em {arquivo_task}: {task_id}", file=sys.stderr)

    if not task_id:
        saldo = cn.saldo()
        print(f"saldo antes: US$ {saldo:.2f} | {len(numeros)} números", file=sys.stderr)
        task = cn.submeter(args.numeros)
        task_id = task["task_id"]
        with open(arquivo_task, "w") as f:
            f.write(task_id + "\n")
        print(f"task criada: {task_id} (estimated_amount={task.get('estimated_amount')}) — salvo em {arquivo_task}", file=sys.stderr)

    info = cn.aguardar(task_id, args.intervalo, args.timeout)
    if info.get("cached_count"):
        print(f"  {info['cached_count']} número(s) vieram do cache da API (resultado não muda se reconsultar)", file=sys.stderr)
    resultado = cn.baixar_resultado(info["result_url"])

    sim = nao = sem_resultado = 0
    with open(args.resultado, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["telefone_e164", "tem_whatsapp"])
        for n in numeros:
            r = resultado.get(digitos(n), "")
            sim += r == "sim"
            nao += r == "nao"
            sem_resultado += r == ""
            writer.writerow([n, r])
    print(f"sim={sim} nao={nao} sem_resultado={sem_resultado} -> {args.resultado}", file=sys.stderr)
    try:
        print(f"saldo depois: US$ {cn.saldo():.2f}", file=sys.stderr)
    except Exception as e:  # saldo é só informativo
        print(f"[aviso] não consegui ler o saldo: {e}", file=sys.stderr)
    os.remove(arquivo_task)


def cmd_mesclar(_cn, args) -> None:
    with open(args.resultado, newline="", encoding="utf-8") as f:
        wa = {digitos(r["telefone_e164"]): r["tem_whatsapp"] for r in csv.DictReader(f)}
    with open(args.leads, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        colunas = list(reader.fieldnames or [])
        leads = list(reader)
    if "tem_whatsapp" not in colunas:
        colunas.append("tem_whatsapp")
    with open(args.saida, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=colunas)
        writer.writeheader()
        for lead in leads:
            lead["tem_whatsapp"] = wa.get(digitos(lead.get("telefone_e164", "")), "") if lead.get("telefone_e164") else ""
            writer.writerow(lead)
    qtd_sim = sum(1 for l in leads if l["tem_whatsapp"] == "sim")
    print(f"{qtd_sim}/{len(leads)} leads com WhatsApp -> {args.saida}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-key", default=os.environ.get("CHECKNUMBER_API_KEY"), help="Padrão: $CHECKNUMBER_API_KEY")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("saldo", help="Mostra o saldo (US$)")

    p = sub.add_parser("checar", help="Submete números e aguarda o resultado")
    p.add_argument("numeros", help=".txt com um E.164 (+55...) por linha")
    p.add_argument("resultado", help="CSV de saída: telefone_e164,tem_whatsapp")
    p.add_argument("--task-id", default=None, help="Retoma o polling de uma task já criada (não resubmete)")
    p.add_argument("--intervalo", type=float, default=20, help="Segundos entre consultas (padrão 20)")
    p.add_argument("--timeout", type=float, default=1800, help="Timeout total do polling em segundos (padrão 1800; nunca use < 600 em lote grande)")

    p = sub.add_parser("mesclar", help="Junta o resultado no CSV de leads")
    p.add_argument("leads")
    p.add_argument("resultado")
    p.add_argument("saida")

    args = parser.parse_args()
    if args.cmd == "mesclar":
        cmd_mesclar(None, args)
        return
    if not args.api_key:
        print("defina CHECKNUMBER_API_KEY (ou --api-key)", file=sys.stderr)
        sys.exit(1)
    cn = CheckNumber(args.api_key)
    {"saldo": cmd_saldo, "checar": cmd_checar}[args.cmd](cn, args)


if __name__ == "__main__":
    main()
