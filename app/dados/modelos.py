"""Tabelas do banco. Todos os valores em CENTAVOS (int) para evitar erro de arredondamento."""
from datetime import date

from sqlmodel import Field, SQLModel, UniqueConstraint


class Categoria(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    nome: str
    tipo: str  # "entrada" | "saida"
    cor: str = "#00F0FF"


class Conta(SQLModel, table=True):
    """Conta corrente, dinheiro ou cartão de crédito (tipo = "cartao")."""
    id: int | None = Field(default=None, primary_key=True)
    nome: str
    tipo: str = "corrente"  # "corrente" | "dinheiro" | "cartao"
    cor: str = "#00F0FF"
    final: str = ""  # últimos 4 dígitos do cartão (opcional)
    limite_centavos: int = 0
    dia_fechamento: int | None = None
    dia_vencimento: int | None = None
    modo_fatura: str = "hibrido"  # "detalhado" | "simples" | "hibrido"
    ativa: bool = True


class Recorrencia(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    descricao: str
    tipo: str  # "entrada" | "saida"
    valor_centavos: int
    dia: int
    inicio: str  # "AAAA-MM"
    fim: str | None = None  # "AAAA-MM" ou None (sem fim)
    categoria_id: int = Field(foreign_key="categoria.id")
    conta_id: int = Field(foreign_key="conta.id")
    ativa: bool = True


class CompraParcelada(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    descricao: str
    valor_total_centavos: int
    n_parcelas: int
    data_compra: date
    conta_id: int = Field(foreign_key="conta.id")
    categoria_id: int = Field(foreign_key="categoria.id")


class Fatura(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("conta_id", "competencia"),)
    id: int | None = Field(default=None, primary_key=True)
    conta_id: int = Field(foreign_key="conta.id")
    competencia: str  # mês do VENCIMENTO, "AAAA-MM"
    valor_informado_centavos: int | None = None  # valor real digitado (modo simples/híbrido)
    paga: bool = False


class Lancamento(SQLModel, table=True):
    """Tabela fato: tudo (manual, fixa, parcela, fatura, ajuste) vira uma linha aqui."""
    __table_args__ = (UniqueConstraint("recorrencia_id", "competencia"),)
    id: int | None = Field(default=None, primary_key=True)
    data: date
    competencia: str  # visão Orçamento (mês do consumo)
    competencia_caixa: str  # visão Caixa (mês em que o dinheiro sai)
    descricao: str
    tipo: str  # "entrada" | "saida"
    valor_centavos: int
    status: str = "pago"  # "previsto" | "pago"
    origem: str = "manual"  # "manual" | "recorrencia" | "parcela" | "fatura_total" | "ajuste_fatura"
    categoria_id: int = Field(foreign_key="categoria.id")
    conta_id: int = Field(foreign_key="conta.id")
    recorrencia_id: int | None = Field(default=None, foreign_key="recorrencia.id")
    compra_id: int | None = Field(default=None, foreign_key="compraparcelada.id")
    parcela_n: int | None = None
    parcela_total: int | None = None
    fatura_id: int | None = Field(default=None, foreign_key="fatura.id")
    observacao: str = ""


class Configuracao(SQLModel, table=True):
    chave: str = Field(primary_key=True)
    valor: str


class EstimativaMes(SQLModel, table=True):
    """Estimativa de gastos variáveis que você digita para um mês específico."""
    competencia: str = Field(primary_key=True)  # "AAAA-MM"
    valor_centavos: int
