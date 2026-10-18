"""Testes com dados sintéticos no formato bruto da Receita (zip, sem cabeçalho,
';', Latin-1). Rodar: python -m pytest prospeccao-advocacia-contabilidade/scripts/tests"""

import csv
import io
import os
import subprocess
import sys
import zipfile
from unittest import mock

import pytest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

import checar_whatsapp_checknumber as cn  # noqa: E402
import filtrar_cnpjs_segmento as f  # noqa: E402


def zip_receita(caminho, nome, linhas):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerows(linhas)
    with zipfile.ZipFile(caminho, "w") as z:
        z.writestr(nome, buf.getvalue().encode("latin-1"))


def estab(cnpj, fantasia, cnae, sec="", uf="SP", ddd="11", tel="33334444", matriz="1", situacao="02", ddd2="", tel2=""):
    return [cnpj, "0001", "00", matriz, fantasia, situacao, "20200101", "00", "", "", "20200101", cnae, sec,
            "RUA", "X", "1", "", "CENTRO", "01000000", uf, "7107", ddd, tel, ddd2, tel2, "", "", "a@b.com.br", "", ""]


def empresa(cnpj, razao, capital="10000,00"):
    return [cnpj, razao, "2232", "49", capital, "05", ""]


def socio(cnpj, nome, qual, data):
    return [cnpj, "2", nome, "***123456**", qual, data, "", "", "", "00", "4"]


@pytest.fixture
def base(tmp_path):
    e = [
        estab("10000001", "", "6911701"),                                        # advocacia pura
        estab("10000002", "SILVA TRIBUTARIO", "6911701", sec="7020400"),        # keyword + sinal
        estab("10000003", "", "6911701"),                                        # negativa na razão
        estab("10000004", "DIREITO DE FAMILIA", "6911701"),                      # negativa no fantasia
        estab("10000005", "", "6912501", sec="6911701"),                         # cartório
        estab("10000006", "", "6911702"),                                        # só auxiliar: não entra
        estab("10000007", "", "4711302"),                                        # keyword sem CNAE
        estab("10000008", "", "6911701", situacao="08"),                         # baixada
        estab("10000009", "", "6911701"),                                        # Meireles: não é MEI/negativa
        estab("20000001", "", "6920601"),                                        # contábil
        estab("20000002", "", "6920602"),                                        # contábil consultoria
        estab("20000003", "CONTABILIDADE PARA MEI", "6920601"),                  # negativa contábil
        estab("20000004", "", "6920601", sec="7020400"),
    ]
    emp = [
        empresa("10000001", "SOUZA ADVOGADOS ASSOCIADOS"),
        empresa("10000002", "SILVA SOCIEDADE DE ADVOGADOS", "500000,00"),
        empresa("10000003", "COSTA ADVOCACIA CRIMINAL"),
        empresa("10000004", "LIMA ADVOGADOS"),
        empresa("10000005", "CARTORIO DO 1 OFICIO"),
        empresa("10000006", "PERITOS JUDICIAIS LTDA"),
        empresa("10000007", "MERCADO EMPRESARIAL LTDA"),
        empresa("10000008", "BAIXADA ADVOGADOS"),
        empresa("10000009", "MEIRELES & PENALVA ADVOGADOS"),
        empresa("20000001", "ALFA CONTABILIDADE"),
        empresa("20000002", "BETA AUDITORES INDEPENDENTES"),
        empresa("20000003", "GAMA CONTABIL"),
        empresa("20000004", "DELTA CONTROLADORIA E BPO FINANCEIRO", "9000000,00"),
    ]
    soc = [
        socio("10000001", "ANA MARIA SOUZA", "49", "20150101"),
        socio("10000001", "JOAO SOUZA", "22", "20100101"),
        socio("10000002", "CARLOS SILVA", "05", "20000101"),
        socio("10000002", "PEDRO SILVA", "49", "20180101"),
    ]
    zip_receita(tmp_path / "Estabelecimentos0.zip", "K3241.K03200Y0.D60913.ESTABELE", e)
    zip_receita(tmp_path / "Empresas0.zip", "K3241.K03200Y0.D60913.EMPRECSV", emp)
    zip_receita(tmp_path / "Socios0.zip", "K3241.K03200Y0.D60913.SOCIOCSV", soc)
    zip_receita(tmp_path / "Municipios.zip", "F.K03200$Z.D60913.MUNICCSV", [["7107", "SAO PAULO"]])
    return tmp_path


def rodar_filtro(base, segmento, *extra):
    saida = base / f"leads_{segmento}.csv"
    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "filtrar_cnpjs_segmento.py"), "--segmento", segmento,
         "--estabelecimentos", str(base / "Estabelecimentos*.zip"), "--empresas", str(base / "Empresas*.zip"),
         "--socios", str(base / "Socios*.zip"), "--municipios", str(base / "Municipios.zip"),
         "--saida", str(saida), *extra],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    with open(saida, encoding="utf-8") as fh:
        return {row["cnpj"][:8]: row for row in csv.DictReader(fh)}, r.stderr


def test_termos_palavra_inteira_e_prefixo():
    neg = f.compila_termos(["MEI", "PENAL", "AUXILIO-DOENCA", "DANOS MORAIS"])
    assert f.termos_encontrados("Meireles & Penalva", neg) == []
    assert f.termos_encontrados("Contabilidade p/ MEI", neg) == ["MEI"]
    assert f.termos_encontrados("Direito Penal Ltda", neg) == ["PENAL"]
    assert f.termos_encontrados("AUXÍLIO DOENÇA ADV", neg) == ["AUXILIO-DOENCA"]
    assert f.termos_encontrados("danos-morais", neg) == ["DANOS MORAIS"]
    pos = f.compila_termos(["CORPORAT*", "AUDIT*", "SOCIETARIO"])
    assert f.termos_encontrados("Jurídico Corporativo", pos) == ["CORPORAT*"]
    assert f.termos_encontrados("Auditores Independentes", pos) == ["AUDIT*"]
    assert f.termos_encontrados("Direito Societário", pos) == ["SOCIETARIO"]
    assert f.termos_encontrados("Societarios", pos) == []


def test_advocacia(base):
    leads, _ = rodar_filtro(base, "advocacia")
    assert set(leads) == {"10000001", "10000002", "10000009"}
    assert leads["10000001"]["nome_decisor"] == "JOAO SOUZA"  # decisor mais antigo
    assert leads["10000002"]["nome_decisor"] == "PEDRO SILVA"  # administrador antes de sócio comum
    assert leads["10000002"]["cnaes_sinal_encontrados"] == "7020400"
    assert "TRIBUTARIO" in leads["10000002"]["keywords_positivas"]
    assert leads["10000001"]["nome_municipio"] == "SAO PAULO"
    # ordenação por relevância: keyword + sinal primeiro
    assert list(leads)[0] == "10000002"


def test_advocacia_exigir_e_incluir_keyword(base):
    leads, _ = rodar_filtro(base, "advocacia", "--exigir-keyword")
    assert set(leads) == {"10000001", "10000002"}
    leads, _ = rodar_filtro(base, "advocacia", "--incluir-so-keyword")
    assert "10000007" in leads and leads["10000007"]["motivo_match"] == "razao_social"


def test_contabil_e_capital(base):
    leads, stderr = rodar_filtro(base, "contabil")
    assert set(leads) == {"20000001", "20000002", "20000004"}
    assert "mediana" in stderr
    leads, _ = rodar_filtro(base, "contabil", "--capital-maximo", "5000000")
    assert set(leads) == {"20000001", "20000002"}


def test_preparar_telefones_e_saidas(tmp_path):
    cols = ["cnpj", "razao_social", "nome_fantasia", "relevancia", "uf", "nome_municipio", "ddd_1", "telefone_1",
            "ddd_2", "telefone_2", "correio_eletronico", "nome_decisor", "documento_decisor", "eh_decisor"]
    linhas = [
        ["1", "A ADV", "", "3", "SP", "SAO PAULO", "11", "988887777", "", "", "A@X.COM", "MARIA DA SILVA", "***1**", "1"],
        ["2", "B ADV", "", "1", "SP", "SAO PAULO", "11", "988887777", "", "", "", "", "", ""],          # dup tel
        ["3", "C ADV", "", "1", "RJ", "", "21", "999999999", "21", "987654321", "", "", "", ""],      # tel2
        ["4", "D ADV", "", "1", "EX", "", "11", "40090000", "", "", "", "", "", ""],                  # EX
        ["5", "E ADV", "", "1", "PR", "", "44", "33462208", "", "", "", "", "", ""],                  # repetido x4
        ["6", "F ADV", "", "1", "PR", "", "44", "33462208", "", "", "", "", "", ""],
        ["7", "G ADV", "", "1", "PR", "", "44", "33462208", "", "", "", "", "", ""],
        ["8", "H ADV", "", "1", "PR", "", "44", "33462208", "", "", "", "", "", ""],
        ["9", "I ADV", "", "1", "SP", "", "00", "12345", "", "", "", "", "", ""],                     # inválido
        ["10", "J ADV", "", "1", "SP", "", "11", "977776666", "", "", "", "", "", ""],                # já contatado
        ["11", "K ADV", "", "1", "MG", "", "31", "988989898", "", "", "", "", "", ""],                # <=2 dígitos
    ]
    entrada = tmp_path / "leads.csv"
    with open(entrada, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerows(linhas)
    hist = tmp_path / "wati.csv"
    hist.write_text("Name,CountryCode,Phone\nJ,55,11977776666\n", encoding="utf-8")
    saida, txt = tmp_path / "tel.csv", tmp_path / "num.txt"
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "preparar_telefones.py"), str(entrada), str(saida),
                        "--txt-checknumber", str(txt), "--historico", str(hist)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    with open(saida, encoding="utf-8") as fh:
        st = {row["cnpj"]: row["telefone_status"] for row in csv.DictReader(fh)}
    assert st == {"1": "ok", "2": "duplicado", "3": "ok", "4": "uf_ex", "5": "suspeito_repetido",
                  "6": "suspeito_repetido", "7": "suspeito_repetido", "8": "suspeito_repetido", "9": "nao",
                  "10": "ja_contatado", "11": "suspeito_repetido"}
    assert txt.read_text().split() == ["+5511988887777", "+5521987654321"]

    # checagem simulada: só o 1 tem WhatsApp
    wa = tmp_path / "wa.csv"
    wa.write_text("telefone_e164,tem_whatsapp\n+5511988887777,sim\n+5521987654321,nao\n", encoding="utf-8")
    mesclado = tmp_path / "mesclado.csv"
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "checar_whatsapp_checknumber.py"), "mesclar",
                        str(saida), str(wa), str(mesclado)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    disparo, wati = tmp_path / "disparo.csv", tmp_path / "wati.csv"
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "gerar_saidas.py"), str(mesclado),
                        "--disparo", str(disparo), "--wati", str(wati)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert disparo.read_text().splitlines() == [
        "nome,primeiro_nome,telefone,email,regiao,valor",
        "A ADV,Maria,5511988887777,a@x.com,Sao Paulo/SP,",
    ]
    assert wati.read_text().splitlines() == [
        "Name,CountryCode,Phone,AllowCampaign,AllowSMS,Attribute 1,Attribute 2",
        "A ADV,55,11988887777,TRUE,FALSE,Maria,SP",
    ]


def test_checknumber_fluxo(tmp_path):
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("all.csv", "number,activated\n5511988887777,yes\n5521987654321,no\n")

    def resp(status=200, json=None, content=b""):
        m = mock.Mock(status_code=status, content=content, text="")
        m.json.return_value = json
        m.raise_for_status = mock.Mock()
        return m

    numeros = tmp_path / "n.txt"
    numeros.write_text("+5511988887777\n+5521987654321\n")
    saida = tmp_path / "r.csv"
    cliente = cn.CheckNumber("k")
    cliente.s = mock.Mock()
    cliente.s.get.return_value = resp(json={"balance": 12.82})
    cliente.s.post.side_effect = [
        resp(json={"task_id": "t1", "status": "pending", "estimated_amount": 0.01}),
        resp(json={"status": "processing"}),
        resp(json={"status": "exported", "result_url": "https://x/r.zip", "cached_count": 1}),
    ]
    args = mock.Mock(numeros=str(numeros), resultado=str(saida), task_id=None, intervalo=0, timeout=60)
    with mock.patch.object(cn.requests, "get", return_value=resp(content=zbuf.getvalue())):
        cn.cmd_checar(cliente, args)
    assert saida.read_text().splitlines() == ["telefone_e164,tem_whatsapp", "+5511988887777,sim", "+5521987654321,nao"]
    assert not os.path.exists(str(saida) + ".task_id")
    assert cliente.s.post.call_args_list[0].kwargs["data"] == {"task_type": "ws"}
