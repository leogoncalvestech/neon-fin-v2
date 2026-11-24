"""NEON//FIN — finanças pessoais com foco em previsibilidade.

Uso:
    python main.py              # seus dados em dados/financas.db
    python main.py --exemplo    # abre com dados de exemplo (dados/exemplo.db), para testar sem mexer nos seus
    python main.py --porta 8090
"""
import argparse
from datetime import date
from pathlib import Path

from nicegui import app, ui

BASE = Path(__file__).resolve().parent
_p = argparse.ArgumentParser()
_p.add_argument("--exemplo", action="store_true")
_p.add_argument("--porta", type=int, default=8080)
_p.add_argument("--sem-navegador", action="store_true")
ARGS, _ = _p.parse_known_args()

PASTA_DADOS = BASE / "dados"
PASTA_DADOS.mkdir(exist_ok=True)
CAMINHO_DB = PASTA_DADOS / ("exemplo.db" if ARGS.exemplo else "financas.db")


def iniciar():
    from sqlmodel import select

    from app.dados import banco
    from app.dados.modelos import Lancamento
    from app.servicos.recorrencias import garantir_previstos

    banco.fazer_backup(CAMINHO_DB, PASTA_DADOS / "backups")
    banco.configurar(CAMINHO_DB)
    with banco.sessao() as s:
        if ARGS.exemplo and not s.exec(select(Lancamento)).first():
            from app.exemplo import popular_exemplo
            popular_exemplo(s, date.today())
        garantir_previstos(s, date.today())  # mantém 12 meses de previstos à frente


if __name__ in {"__main__", "__mp_main__"}:
    iniciar()
    from app.ui.telas import anual, cartoes, config, lancamentos, painel, recorrencias, relatorios  # noqa: F401  (registram as páginas)

    app.add_static_files("/static", BASE / "static")
    ui.add_css((BASE / "app/ui/tema.css").read_text(encoding="utf-8"), shared=True)
    ui.run(title="NEON//FIN", dark=True, port=ARGS.porta, reload=False, language="pt-BR", favicon="💠",
           show=not ARGS.sem_navegador, show_welcome_message=False)
