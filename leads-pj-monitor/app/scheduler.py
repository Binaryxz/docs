"""Agendador (APScheduler, em processo — sem precisar de Celery/Redis
separado, mais simples de instalar numa VPS pequena). Dois ritmos:

- Diário (horário configurável em Configurações): coleta DataJud dos
  tribunais ativos -> matching -> qualificação. É o que o botão
  "Rodar agora" do Painel também dispara, na hora, fora do cron.
- Mensal/trimestral: importação da Receita Federal (pesada, ~dezenas de
  GB) e da PGFN (dívida ativa). Isso NÃO é disparado pelo "Rodar agora"
  do dia a dia — tem botões próprios na tela de Configurações, porque
  rodar isso por engano custa tempo e banda consideráveis.
"""
import logging
import os
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_config
from app.db import execute

logging.getLogger("apscheduler").setLevel(logging.WARNING)

_scheduler: BackgroundScheduler | None = None
_lock_execucao_diaria = threading.Lock()


def rodar_pipeline_diario() -> dict:
    """DataJud -> matching -> qualificação. Protegido por lock: se o
    cron disparar enquanto uma execução manual ("Rodar agora") ainda
    estiver rodando, essa segunda chamada é ignorada em vez de rodar
    em paralelo e duplicar trabalho."""
    if not _lock_execucao_diaria.acquire(blocking=False):
        return {"ignorado": True, "motivo": "já existe uma execução em andamento"}

    try:
        from app.datajud_client import executar_coleta_completa
        from app.matching import processar_matching_pendentes
        from app.qualification import qualificar_leads_pendentes

        resultado_coleta = executar_coleta_completa()
        resultado_matching = processar_matching_pendentes()
        resultado_qualificacao = qualificar_leads_pendentes()

        return {
            "coleta": [vars(r) for r in resultado_coleta],
            "matching": resultado_matching,
            "qualificacao": resultado_qualificacao,
        }
    finally:
        _lock_execucao_diaria.release()


def rodar_pipeline_diario_em_thread() -> None:
    """Pro botão "Rodar agora": dispara numa thread separada pra não
    travar a interface esperando a coleta terminar."""
    threading.Thread(target=rodar_pipeline_diario, daemon=True).start()


def _rodar_import_receita() -> None:
    from app.receita_import import executar_importacao_completa

    executar_importacao_completa()


def _rodar_import_pgfn() -> None:
    from app.pgfn_import import executar_importacao_pgfn

    executar_importacao_pgfn()


def execucao_esta_em_andamento() -> bool:
    return _lock_execucao_diaria.locked()


def obter_agendador() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    fuso = os.environ.get("APP_TIMEZONE", "America/Sao_Paulo")
    horario = get_config("horario_execucao_diaria") or "06:00"
    hora, minuto = horario.split(":")

    _scheduler = BackgroundScheduler(timezone=fuso)
    _scheduler.add_job(
        rodar_pipeline_diario,
        CronTrigger(hour=int(hora), minute=int(minuto), timezone=fuso),
        id="coleta_diaria",
        replace_existing=True,
    )
    _scheduler.add_job(
        _rodar_import_receita,
        CronTrigger(day=1, hour=3, minute=0, timezone=fuso),
        id="import_receita_mensal",
        replace_existing=True,
    )
    _scheduler.add_job(
        _rodar_import_pgfn,
        CronTrigger(day=1, month="1,4,7,10", hour=4, minute=0, timezone=fuso),
        id="import_pgfn_trimestral",
        replace_existing=True,
    )
    _scheduler.start()
    return _scheduler


def reagendar_coleta_diaria(novo_horario: str) -> None:
    """Chamado pela tela de Configurações quando o usuário troca o
    horário — atualiza o job já em execução sem precisar reiniciar o
    container."""
    agendador = obter_agendador()
    hora, minuto = novo_horario.split(":")
    fuso = os.environ.get("APP_TIMEZONE", "America/Sao_Paulo")
    agendador.reschedule_job(
        "coleta_diaria", trigger=CronTrigger(hour=int(hora), minute=int(minuto), timezone=fuso)
    )
