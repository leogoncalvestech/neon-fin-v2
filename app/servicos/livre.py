"""Cálculo do 'livre para gastar' e resumo do mês."""
import calendar
from datetime import date, timedelta

from sqlmodel import Session, select

from app.dados.modelos import Configuracao, Conta, Fatura, Lancamento
from app.servicos.cartoes import datas_da_fatura, resumo_fatura
from app.servicos.util import competencia


def livre_para_gastar(entradas: int, fixas: int, parcelas: int, metas: int, gastos_variaveis: int,
                      hoje: date, comp: str | None = None) -> dict:
    """Função pura (centavos).

    disponível  = entradas − fixas − parcelas − metas
    livre (mês) = disponível − gastos variáveis
    hoje        = livre ÷ dias restantes (inclui hoje)
    semana      = hoje × dias até domingo (sem passar do fim do mês)
    """
    comp = comp or competencia(hoje)
    ano, mes = map(int, comp.split("-"))
    ultimo = date(ano, mes, calendar.monthrange(ano, mes)[1])
    disponivel = entradas - fixas - parcelas - metas
    livre = disponivel - gastos_variaveis

    if comp == competencia(hoje):
        ref = hoje
    elif comp > competencia(hoje):
        ref = date(ano, mes, 1)  # mês futuro: conta o mês inteiro
    else:
        ref = None  # mês passado: não há mais dias para gastar

    if ref is None:
        dias_rest, dias_semana, por_dia = 0, 0, 0
    else:
        dias_rest = (ultimo - ref).days + 1
        fim_semana = min(ref + timedelta(days=6 - ref.weekday()), ultimo)
        dias_semana = (fim_semana - ref).days + 1
        por_dia = max(livre, 0) // dias_rest

    gasto_dia_medio = gastos_variaveis // max((ref - date(ano, mes, 1)).days + 1, 1) if ref else 0
    projecao_estouro = gasto_dia_medio * dias_rest - max(livre, 0) if ref else 0
    return {"entradas": entradas, "fixas": fixas, "parcelas": parcelas, "metas": metas,
            "variaveis": gastos_variaveis, "disponivel": disponivel, "livre": livre,
            "por_dia": por_dia, "semana": por_dia * dias_semana, "dias_restantes": dias_rest,
            "media_diaria": gasto_dia_medio, "estouro_no_ritmo": max(projecao_estouro, 0)}


def reserva_mensal(s: Session) -> int:
    c = s.get(Configuracao, "reserva_mensal_centavos")
    return int(c.valor) if c else 0


def resumo_mes(s: Session, comp: str, hoje: date) -> dict:
    """Agrupa os lançamentos do mês (visão Orçamento) e calcula o livre."""
    lancs = s.exec(select(Lancamento).where(Lancamento.competencia == comp)).all()
    soma = lambda f: sum(l.valor_centavos if l.tipo == "saida" else -l.valor_centavos for l in lancs if f(l))
    entradas = sum(l.valor_centavos for l in lancs if l.tipo == "entrada" and l.origem in ("manual", "recorrencia"))
    fixas = soma(lambda l: l.origem == "recorrencia" and l.tipo == "saida")
    parcelas = soma(lambda l: l.origem == "parcela")
    variaveis = soma(lambda l: l.origem in ("manual", "fatura_total", "ajuste_fatura") and
                     (l.tipo == "saida" or l.origem == "ajuste_fatura"))
    r = livre_para_gastar(entradas, fixas, parcelas, reserva_mensal(s), variaveis, hoje, comp)
    r["por_categoria"] = {}
    for l in lancs:
        if l.tipo == "saida":
            r["por_categoria"][l.categoria_id] = r["por_categoria"].get(l.categoria_id, 0) + l.valor_centavos
    return r


def proximos_vencimentos(s: Session, hoje: date, limite: int = 8) -> list[dict]:
    """Contas previstas (fora do cartão) + faturas não pagas, em ordem de data. Inclui atrasados."""
    itens = []
    for l in s.exec(select(Lancamento).where(Lancamento.status == "previsto", Lancamento.fatura_id == None)):  # noqa: E711
        itens.append({"data": l.data, "descricao": l.descricao, "valor": l.valor_centavos, "tipo": l.tipo,
                      "lancamento_id": l.id, "fatura_id": None})
    for f in s.exec(select(Fatura).where(Fatura.paga == False)):  # noqa: E712
        conta = s.get(Conta, f.conta_id)
        _, venc = datas_da_fatura(f.competencia, conta.dia_fechamento, conta.dia_vencimento)
        valor = resumo_fatura(s, f)["valor"]
        if valor > 0:
            itens.append({"data": venc, "descricao": f"Fatura {conta.nome}", "valor": valor, "tipo": "saida",
                          "lancamento_id": None, "fatura_id": f.id})
    itens.sort(key=lambda x: x["data"])
    atrasados = [i for i in itens if i["data"] < hoje]
    futuros = [i for i in itens if i["data"] >= hoje]
    return (atrasados + futuros)[:limite]
