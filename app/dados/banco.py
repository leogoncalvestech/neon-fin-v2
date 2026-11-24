"""Conexão com o SQLite, criação das tabelas, dados iniciais e backup."""
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from app.dados.modelos import Categoria, Configuracao, Conta

_engine = None
caminho_atual: Path | None = None

CATEGORIAS_PADRAO = [
    ("Salário", "entrada", "#39FF14"), ("Renda extra", "entrada", "#7CFF5B"), ("Outras entradas", "entrada", "#B6FF9E"),
    ("Moradia", "saida", "#FF2E88"), ("Alimentação", "saida", "#00F0FF"), ("Transporte", "saida", "#FFE600"),
    ("Saúde", "saida", "#39FF14"), ("Lazer", "saida", "#FF6FB0"), ("Compras", "saida", "#9D4EDD"),
    ("Educação", "saida", "#4DA3FF"), ("Assinaturas", "saida", "#FF9F1C"), ("Cartão de crédito", "saida", "#C18BFF"),
    ("Outros", "saida", "#7C8299"),
]


def criar_engine(caminho: str | Path):
    """Cria o engine do SQLite (use ':memory:' nos testes)."""
    url = "sqlite://" if str(caminho) == ":memory:" else f"sqlite:///{caminho}"
    eng = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _pragmas(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        if str(caminho) != ":memory:":
            cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    return eng


def iniciar_banco(eng) -> None:
    """Cria as tabelas e, se o banco estiver vazio, as categorias e contas padrão."""
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        if not s.exec(select(Categoria)).first():
            for nome, tipo, cor in CATEGORIAS_PADRAO:
                s.add(Categoria(nome=nome, tipo=tipo, cor=cor))
        if not s.exec(select(Conta)).first():
            s.add(Conta(nome="Conta corrente", tipo="corrente", cor="#00F0FF"))
            s.add(Conta(nome="Dinheiro / Pix", tipo="dinheiro", cor="#39FF14"))
        padroes = {"reserva_mensal_centavos": "0", "saldo_inicial_centavos": "0",
                   "saldo_inicial_mes": datetime.now().strftime("%Y-%m"), "estimativa_padrao_centavos": "0"}
        for chave, valor in padroes.items():
            if not s.get(Configuracao, chave):
                s.add(Configuracao(chave=chave, valor=valor))
        s.commit()


def configurar(caminho: str | Path):
    global _engine, caminho_atual
    caminho_atual = Path(caminho)
    _engine = criar_engine(caminho)
    iniciar_banco(_engine)
    return _engine


def sessao() -> Session:
    if _engine is None:
        raise RuntimeError("Banco não configurado: chame configurar() primeiro.")
    return Session(_engine)


def fazer_backup(caminho_db: Path, pasta: Path, manter: int = 30) -> Path | None:
    """Copia o banco para backups/financas_AAAA-MM-DD.db e mantém só os N mais recentes."""
    if not caminho_db.exists():
        return None
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"financas_{datetime.now():%Y-%m-%d}.db"
    shutil.copy2(caminho_db, destino)
    for antigo in sorted(pasta.glob("financas_*.db"))[:-manter]:
        antigo.unlink()
    return destino
