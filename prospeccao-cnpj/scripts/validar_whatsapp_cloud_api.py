"""
Valida se um lead tem WhatsApp usando a API OFICIAL da Meta (WhatsApp
Business Platform / Cloud API) — não é automação de sessão do WhatsApp
Web, é a mesma API que ferramentas grandes de CRM/marketing usam.

Como funciona: em vez de "checar" e "disparar" como duas etapas
separadas, a validação É o disparo da primeira mensagem (template
pré-aprovado). A resposta da API já diz se o número existe no WhatsApp:
  - envio aceito (retorna "messages" com um id)            -> tem WhatsApp
  - erro de destinatário inválido/não é usuário WhatsApp     -> não tem
  - outros erros (token inválido, template não aprovado etc) -> "erro",
    não confunda com "não tem WhatsApp"

Isso significa: sem etapa de validação separada gastando orçamento à toa
— quem tem WhatsApp já recebeu a primeira mensagem da campanha.

Pré-requisitos (configuração única, não é "rodar e pronto" — mas depois
de configurado não tem custo por consulta dentro dos limites do plano):
  1. Conta Meta Business verificada (business.facebook.com).
  2. Um número de telefone registrado no WhatsApp Business Platform
     (developers.facebook.com/docs/whatsapp/cloud-api/get-started).
  3. Um template de mensagem aprovado pela Meta (categoria "Marketing"
     ou "Utility", conforme o conteúdo da primeira mensagem).
  4. WHATSAPP_ACCESS_TOKEN e WHATSAPP_PHONE_NUMBER_ID (do painel do App
     Meta) como variáveis de ambiente.

Limites de envio (não fixos — dependem do tier de qualidade/verificação
da conta, mudam com o tempo): confira o limite atual em
developers.facebook.com/docs/whatsapp/cloud-api/guides/send-messages
antes de rodar em volume — contas novas/não verificadas começam com um
teto baixo de destinatários únicos por 24h. Esse script não tenta burlar
isso; ajuste --lotes-por-dia conforme o limite real da sua conta.

Uso:
    export WHATSAPP_ACCESS_TOKEN="..."
    export WHATSAPP_PHONE_NUMBER_ID="..."
    python validar_whatsapp_cloud_api.py leads_porte_ok.csv leads_com_whatsapp.csv \
      --template nome_do_template_aprovado --idioma pt_BR

Retomável: pula CNPJ que já está no CSV de saída.
"""

import argparse
import csv
import os
import re
import sys
import time

import requests

GRAPH_API_VERSION = "v21.0"

# Códigos de erro documentados pela Meta que indicam "não é usuário do
# WhatsApp" (não confundir com falha de token/template/rede, que são
# erros de configuração, não do número). Confira a lista atual em
# developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes
# antes de confiar cegamente — a Meta pode adicionar/mudar códigos.
CODIGOS_NUMERO_INVALIDO = {131026, 131030}


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


def envia_template(numero_e164: str, template: str, idioma: str, access_token: str, phone_number_id: str) -> dict:
    """Retorna {'tem_whatsapp': 'sim'|'nao'|'erro', 'detalhe': str}."""
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": numero_e164,
        "type": "template",
        "template": {"name": template, "language": {"code": idioma}},
    }
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            json=payload,
            timeout=20,
        )
    except requests.RequestException as exc:
        return {"tem_whatsapp": "erro", "detalhe": f"falha_de_rede: {exc}"}

    data = resp.json()
    if resp.status_code == 200 and data.get("messages"):
        return {"tem_whatsapp": "sim", "detalhe": data["messages"][0].get("id", "")}

    erro = (data.get("error") or {})
    codigo = erro.get("code")
    if codigo in CODIGOS_NUMERO_INVALIDO:
        return {"tem_whatsapp": "nao", "detalhe": erro.get("message", "")}

    return {"tem_whatsapp": "erro", "detalhe": f"codigo={codigo} msg={erro.get('message', '')}"}


def main(input_path: str, output_path: str, template: str, idioma: str, delay_segundos: float, limite: int | None) -> None:
    access_token = os.environ.get("WHATSAPP_ACCESS_TOKEN")
    phone_number_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
    if not access_token or not phone_number_id:
        raise SystemExit(
            "Defina WHATSAPP_ACCESS_TOKEN e WHATSAPP_PHONE_NUMBER_ID (painel do App Meta) antes de rodar."
        )

    with open(input_path, newline="", encoding="utf-8") as f_in:
        leads = list(csv.DictReader(f_in))
    if not leads:
        print("Nenhum lead no CSV de entrada.", file=sys.stderr)
        return
    if limite:
        leads = leads[:limite]

    campos_saida = list(leads[0].keys()) + ["telefone_validado", "tem_whatsapp", "detalhe_envio"]

    done_cnpjs = set()
    if os.path.exists(output_path):
        with open(output_path, newline="", encoding="utf-8") as f_prev:
            for row in csv.DictReader(f_prev):
                done_cnpjs.add(row.get("cnpj", ""))

    write_header = not os.path.exists(output_path)
    contagem = {"sim": 0, "nao": 0, "erro": 0, "sem_numero": 0}

    with open(output_path, "a", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=campos_saida)
        if write_header:
            writer.writeheader()

        for i, lead in enumerate(leads, start=1):
            cnpj = lead.get("cnpj", "")
            if cnpj and cnpj in done_cnpjs:
                continue

            numero = normaliza_e164(lead.get("ddd_1", "") + lead.get("telefone_1", ""))
            print(f"[{i}/{len(leads)}] {lead.get('razao_social', '')} ({numero or 'sem numero'})...", file=sys.stderr)

            if not numero:
                resultado = {"tem_whatsapp": "sem_numero", "detalhe": ""}
            else:
                resultado = envia_template(numero, template, idioma, access_token, phone_number_id)

            contagem[resultado["tem_whatsapp"]] = contagem.get(resultado["tem_whatsapp"], 0) + 1
            writer.writerow({
                **lead,
                "telefone_validado": numero,
                "tem_whatsapp": resultado["tem_whatsapp"],
                "detalhe_envio": resultado["detalhe"],
            })
            f_out.flush()
            time.sleep(delay_segundos)

    print(f"\nResumo: {contagem}", file=sys.stderr)
    print(f"Gravado em {output_path}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("entrada", help="CSV de entrada (precisa de cnpj, ddd_1, telefone_1)")
    parser.add_argument("saida", help="CSV de saída (retomável)")
    parser.add_argument("--template", required=True, help="Nome do template aprovado pela Meta")
    parser.add_argument("--idioma", default="pt_BR", help="Código de idioma do template (padrão pt_BR)")
    parser.add_argument("--delay-segundos", type=float, default=1.0, help="Segundos entre envios (padrão 1.0)")
    parser.add_argument("--limite", type=int, default=None, help="Processa só os N primeiros leads (pra testar antes de rodar tudo)")
    args = parser.parse_args()
    main(args.entrada, args.saida, args.template, args.idioma, args.delay_segundos, args.limite)
