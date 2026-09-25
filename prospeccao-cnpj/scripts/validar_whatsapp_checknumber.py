"""
Valida se um lead tem WhatsApp usando a API paga da CheckNumber.AI — a
checagem roda na infraestrutura deles, você NÃO conecta nenhum número seu
(sem QR code, sem sessão do WhatsApp Web pra manter viva, sem risco de
bloqueio de conta própria).

Diferença para validar_whatsapp_cloud_api.py: aquele exige conta Meta
Business verificada + template aprovado, mas não tem custo por consulta
dentro do limite do plano. Este não exige nenhum cadastro na Meta, mas
cobra por número consultado (~$0,0225/número — confira `/v1/balance`
antes de rodar em lote grande, valor é dólar, não "créditos"). Use o que
fizer sentido para o cliente/momento.

Como funciona (assíncrono, baseado em arquivo):
  1. Envia um .txt com os números únicos (um por linha, E.164 com "+").
  2. A API processa em background e devolve um task_id.
  3. Faz polling em /v1/gettasks até status="exported".
  4. Baixa o .zip do result_url e lê all.csv (colunas: number, activated).

Pré-requisito: conta em checknumber.ai + CHECKNUMBER_API_KEY como
variável de ambiente. Antes de rodar em volume, calibre contra 1-2
números que você já sabe a resposta certa (um com WhatsApp, um sem).

Uso:
    export CHECKNUMBER_API_KEY="..."
    python validar_whatsapp_checknumber.py leads_porte_ok.csv leads_com_whatsapp.csv

Retomável: pula CNPJ que já está no CSV de saída.
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


def normaliza_e164(numero_bruto: str) -> str:
    digitos = re.sub(r"\D", "", numero_bruto or "")
    if not digitos:
        return ""
    if digitos.startswith("55") and len(digitos) in (12, 13):
        pass
    elif len(digitos) in (10, 11):
        digitos = "55" + digitos
    else:
        return ""
    # celular pré-2012 sem o 9º dígito
    if len(digitos) == 12 and digitos[4] in "6789":
        digitos = digitos[:4] + "9" + digitos[4:]
    return digitos


def consulta_saldo(api_key: str) -> float:
    resp = requests.get(f"{BASE_URL}/v1/balance", headers={"X-API-Key": api_key}, timeout=20)
    resp.raise_for_status()
    return resp.json().get("balance", 0)


def submete_task(numeros: list[str], api_key: str) -> str:
    conteudo = "\n".join("+" + n for n in numeros).encode("utf-8")
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
    """Devolve {numero_e164_sem_+: 'yes'|'no'} a partir do all.csv dentro do zip."""
    resp = requests.get(result_url, timeout=60)
    resp.raise_for_status()
    resultados = {}
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        nome_csv = next(n for n in z.namelist() if n.endswith("all.csv"))
        with z.open(nome_csv) as f:
            texto = io.TextIOWrapper(f, encoding="utf-8")
            for row in csv.DictReader(texto):
                resultados[row["number"].lstrip("+")] = row["activated"]
    return resultados


def main(input_path: str, output_path: str, limite: int | None) -> None:
    api_key = os.environ.get("CHECKNUMBER_API_KEY")
    if not api_key:
        raise SystemExit("Defina CHECKNUMBER_API_KEY (checknumber.ai) antes de rodar.")

    with open(input_path, newline="", encoding="utf-8") as f_in:
        leads = list(csv.DictReader(f_in))
    if not leads:
        print("Nenhum lead no CSV de entrada.", file=sys.stderr)
        return
    if limite:
        leads = leads[:limite]

    done_cnpjs = set()
    if os.path.exists(output_path):
        with open(output_path, newline="", encoding="utf-8") as f_prev:
            for row in csv.DictReader(f_prev):
                done_cnpjs.add(row.get("cnpj", ""))

    pendentes = [lead for lead in leads if lead.get("cnpj", "") not in done_cnpjs]
    if not pendentes:
        print("Nada pendente — todos os leads já estão no CSV de saída.", file=sys.stderr)
        return

    numero_por_cnpj: dict[str, str] = {}
    numeros_unicos: list[str] = []
    for lead in pendentes:
        numero = normaliza_e164(lead.get("ddd_1", "") + lead.get("telefone_1", ""))
        numero_por_cnpj[lead.get("cnpj", "")] = numero
        if numero and numero not in numeros_unicos:
            numeros_unicos.append(numero)

    if not numeros_unicos:
        print("Nenhum telefone válido entre os leads pendentes — nada a consultar.", file=sys.stderr)
        ativados: dict[str, str] = {}
    else:
        saldo = consulta_saldo(api_key)
        custo_estimado = len(numeros_unicos) * 0.0225
        print(f"[info] saldo atual: {saldo} — {len(numeros_unicos)} números únicos a consultar (~${custo_estimado:.2f})", file=sys.stderr)
        if saldo < custo_estimado:
            print(f"[aviso] saldo pode não ser suficiente para os {len(numeros_unicos)} números — considere --limite para testar antes.", file=sys.stderr)

        task_id = submete_task(numeros_unicos, api_key)
        print("[info] aguardando processamento...", file=sys.stderr)
        resultado_task = aguarda_resultado(task_id, api_key)
        print(f"[info] concluído: {resultado_task.get('success')} ok, {resultado_task.get('failure')} falhas, custo real: {resultado_task.get('actual_amount')}", file=sys.stderr)
        ativados = baixa_resultados(resultado_task["result_url"])

    campos_saida = list(leads[0].keys()) + ["telefone_validado", "tem_whatsapp"]
    write_header = not os.path.exists(output_path)
    contagem = {"sim": 0, "nao": 0, "sem_numero": 0}

    with open(output_path, "a", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=campos_saida)
        if write_header:
            writer.writeheader()

        for lead in pendentes:
            numero = numero_por_cnpj.get(lead.get("cnpj", ""), "")
            if not numero:
                tem_whatsapp = "sem_numero"
            else:
                tem_whatsapp = "sim" if ativados.get(numero) == "yes" else "nao"
            contagem[tem_whatsapp] += 1
            writer.writerow({**lead, "telefone_validado": numero, "tem_whatsapp": tem_whatsapp})

    print(f"\nResumo: {contagem}", file=sys.stderr)
    print(f"Gravado em {output_path}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("entrada", help="CSV de entrada (precisa de cnpj, ddd_1, telefone_1)")
    parser.add_argument("saida", help="CSV de saída (retomável)")
    parser.add_argument("--limite", type=int, default=None, help="Processa só os N primeiros leads pendentes (pra testar antes de rodar tudo)")
    args = parser.parse_args()
    main(args.entrada, args.saida, args.limite)
