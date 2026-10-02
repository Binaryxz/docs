"""Mapeamento de alias DataJud -> UF, usado só para Tribunais de Justiça
estaduais (TJs) — cada um atende exatamente um estado. Tribunais Regionais
Federais (TRFs) cobrem vários estados cada, então ficam sem UF aqui (None);
o matching trata UF ausente mandando pra revisão manual em vez de arriscar
comparar contra a base nacional inteira.
"""

TRIBUNAL_PARA_UF = {
    "api_publica_tjac": "AC", "api_publica_tjal": "AL", "api_publica_tjam": "AM",
    "api_publica_tjap": "AP", "api_publica_tjba": "BA", "api_publica_tjce": "CE",
    "api_publica_tjdft": "DF", "api_publica_tjes": "ES", "api_publica_tjgo": "GO",
    "api_publica_tjma": "MA", "api_publica_tjmg": "MG", "api_publica_tjms": "MS",
    "api_publica_tjmt": "MT", "api_publica_tjpa": "PA", "api_publica_tjpb": "PB",
    "api_publica_tjpe": "PE", "api_publica_tjpi": "PI", "api_publica_tjpr": "PR",
    "api_publica_tjrj": "RJ", "api_publica_tjrn": "RN", "api_publica_tjro": "RO",
    "api_publica_tjrr": "RR", "api_publica_tjrs": "RS", "api_publica_tjsc": "SC",
    "api_publica_tjse": "SE", "api_publica_tjsp": "SP", "api_publica_tjto": "TO",
}


def uf_do_tribunal(alias: str) -> str | None:
    return TRIBUNAL_PARA_UF.get(alias)
