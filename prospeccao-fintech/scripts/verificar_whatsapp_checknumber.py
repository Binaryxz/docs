"""
Verifica se os leads têm WhatsApp usando a API paga da CheckNumber.AI —
a checagem roda na infraestrutura deles, você NÃO conecta nenhum número
seu, sem QR code, sem risco de bloqueio. Calibrado e testado nesta sessão:
bateu certo contra dois números de referência conhecidos (um com WhatsApp,
um sem).

Preço: ~$0,30 a cada 10.000 checagens (varia por conta/volume — confira o
saldo antes de rodar em lote grande). Sem mensalidade.

Como funciona (assíncrono, baseado em arquivo):
  1. Envia um .txt com os números (um por linha, formato E.164 com "+").
  2. A API processa em background e devolve um task_id.
  3. Faz polling em /v1/gettasks até status="exported".
  4. Baixa o .zip do result_url e lê all.csv (colunas: number, activated).

Requisitos: export CHECKNUMBER_API_KEY="sua-chave" (https://checknumber.ai)

Uso:
    python verificar_whatsapp_checknumber.py leads.csv leads_com_whatsapp.csv \
      --coluna-telefone telefone_e164 [--somente-com-whatsapp]

Confere o saldo antes de rodar: GET /v1/balance (a função `consulta_saldo`
abaixo, ou direto: curl -H "X-API-Key: $CHECKNUMBER_API_KEY" https://api.checknumber.ai/v1/balance)
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
SEGUNDOS_ENTRE_POLLS = 5
TIMEOUT_POLL_SEGUNDOS = 300


def consulta_saldo(api_key: str) -> float:
    resp = requests.get(f"{BASE_URL}/v1/balance", headers={"X-API-Key": api_key}, timeout=20)
    resp.raise_for_status()
    return resp.json().get("balance", 0)


def normaliza_e164(numero_bruto: str) -> str:
    digitos = re.sub(r"\D", "", numero_bruto or "")
    if not digitos:
        return ""
    if not digitos.startswith("55") and len(digitos) in (10, 11):
        digitos = "55" + digitos
    return "+" + digitos


def submete_task(numeros: list[str], api_key: str) -> str:
    conteudo = "\n".join(numeros).encode("utf-8")
    files = {"file": ("numeros.txt", conteudo, "text/plain")}
    data = {"task_type": "ws"}
    resp = requests.post(f"{BASE_URL}/v1/tasks", headers={"X-API-Key": api_key}, files=files, data=data, timeout=30)
    resp.raise_for_status()
    corpo = resp.json()
    print(f"[info] task criada: {corpo.get('task_id')} — custo estimado: {corpo.get('estimated_amount')}", file=sys.stderr)
    return corpo["task_id"]


def aguarda_resultado(task_id: str, api_key: str) -> dict:
    inicio = time.time()
    while time.time() - inicio < TIMEOUT_POLL_SEGUNDOS:
        resp = requests.post(f"{BASE_URL}/v1/gettasks", headers={"X-API-Key": api_key}, data={"task_id": task_id}, timeout=20)
        resp.raise_for_status()
        corpo = resp.json()
        if corpo.get("status") == "exported":
            return corpo
        time.sleep(SEGUNDOS_ENTRE_POLLS)
    raise TimeoutError(f"task {task_id} não terminou em {TIMEOUT_POLL_SEGUNDOS}s")


def baixa_resultados(result_url: str) -> dict[str, str]:
    """Devolve {numero_e164: 'yes'|'no'} a partir do all.csv dentro do zip."""
    resp = requests.get(result_url, timeout=60)
    resp.raise_for_status()
    resultados = {}
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        nome_csv = next(n for n in z.namelist() if n.endswith("all.csv"))
        with z.open(nome_csv) as f:
            texto = io.TextIOWrapper(f, encoding="utf-8")
            for row in csv.DictReader(texto):
                resultados[row["number"]] = row["activated"]
    return resultados


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("entrada", help="CSV de entrada")
    parser.add_argument("saida", help="CSV de saída")
    parser.add_argument("--coluna-telefone", default="telefone_e164", help="Coluna com o telefone (padrão telefone_e164)")
    parser.add_argument("--somente-com-whatsapp", action="store_true", help="Grava só quem tem WhatsApp confirmado")
    parser.add_argument("--limite", type=int, default=None, help="Processa só as N primeiras linhas (pra não estourar o saldo)")
    args = parser.parse_args()

    api_key = os.environ.get("CHECKNUMBER_API_KEY")
    if not api_key:
        print("[erro] defina CHECKNUMBER_API_KEY", file=sys.stderr)
        sys.exit(1)

    saldo = consulta_saldo(api_key)
    print(f"[info] saldo atual: {saldo}", file=sys.stderr)

    with open(args.entrada, newline="", encoding="utf-8") as f_in:
        leads = list(csv.DictReader(f_in))
    if args.limite:
        leads = leads[: args.limite]

    if saldo < len(leads):
        print(f"[aviso] saldo ({saldo}) é menor que o número de leads ({len(leads)}) — pode faltar crédito no meio. Use --limite pra testar com menos.", file=sys.stderr)

    numero_por_lead = {}
    numeros_unicos = []
    for i, lead in enumerate(leads):
        numero = normaliza_e164(lead.get(args.coluna_telefone, ""))
        numero_por_lead[i] = numero
        if numero and numero not in numeros_unicos:
            numeros_unicos.append(numero)

    if not numeros_unicos:
        print("Nenhum telefone válido encontrado.", file=sys.stderr)
        return

    task_id = submete_task(numeros_unicos, api_key)
    print("[info] aguardando processamento...", file=sys.stderr)
    resultado_task = aguarda_resultado(task_id, api_key)
    print(f"[info] concluído: {resultado_task.get('success')} ok, {resultado_task.get('failure')} falhas, custo real: {resultado_task.get('actual_amount')}", file=sys.stderr)

    ativados = baixa_resultados(resultado_task["result_url"])

    campos_saida = list(leads[0].keys()) + ["tem_whatsapp"]
    contagem = {"sim": 0, "nao": 0, "sem_numero": 0}
    with open(args.saida, "w", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=campos_saida)
        writer.writeheader()
        for i, lead in enumerate(leads):
            numero = numero_por_lead[i]
            if not numero:
                tem_whatsapp = "sem_numero"
            else:
                resposta = ativados.get(numero.lstrip("+")) or ativados.get(numero, "")
                tem_whatsapp = "sim" if resposta == "yes" else "nao"
            contagem[tem_whatsapp] += 1
            if args.somente_com_whatsapp and tem_whatsapp != "sim":
                continue
            writer.writerow({**lead, "tem_whatsapp": tem_whatsapp})

    print(f"\nResumo: {contagem}", file=sys.stderr)
    print(f"Gravado em {args.saida}", file=sys.stderr)


if __name__ == "__main__":
    main()
