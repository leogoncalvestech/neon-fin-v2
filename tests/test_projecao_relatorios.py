"""Testes da visão anual (saldo previsto) e dos relatórios. Rode com:  python -m pytest -q"""
from datetime import date
from io import BytesIO

import pytest
from openpyxl import load_workbook
from sqlmodel import Session, select

from app.dados.banco import criar_engine, iniciar_banco
from app.dados.modelos import Categoria, Conta
from app.exemplo import popular_exemplo
from app.servicos.lancamentos import criar_lancamento
from app.servicos.projecao import (definir_estimativa, definir_saldo_inicial, estimativa_do_mes, gravar_config,
                                   projecao)
from app.servicos.recorrencias import criar_recorrencia
from app.servicos.relatorios import excel_anual, excel_mensal, relatorio_anual, relatorio_mensal

HOJE = date(2026, 12, 14)


@pytest.fixture
def s():
    eng = criar_engine(":memory:")
    iniciar_banco(eng)
    with Session(eng) as sess:
        yield sess


def cat(s, nome):
    return s.exec(select(Categoria).where(Categoria.nome == nome)).one().id


def corrente(s):
    return s.exec(select(Conta).where(Conta.nome == "Conta corrente")).one().id


@pytest.fixture
def base(s):
    """Salário 4.800, aluguel 1.250, saldo inicial 1.000 em dez/26, estimativa padrão 2.000."""
    criar_recorrencia(s, descricao="Salário", tipo="entrada", valor_centavos=480000, dia=5, inicio="2026-12",
                      categoria_id=cat(s, "Salário"), conta_id=corrente(s), hoje=HOJE)
    criar_recorrencia(s, descricao="Aluguel", tipo="saida", valor_centavos=125000, dia=5, inicio="2026-12",
                      categoria_id=cat(s, "Moradia"), conta_id=corrente(s), hoje=HOJE)
    definir_saldo_inicial(s, 100000, "2026-12")
    gravar_config(s, "estimativa_padrao_centavos", 200000)
    return s


def mes(p, comp):
    return next(m for m in p["meses"] if m["comp"] == comp)


# ------------------------------------------------------------------ saldo e estimativa
def test_saldo_acumulado_com_estimativa_padrao(base):
    p = projecao(base, ["2026-12", "2027-01", "2027-02"], HOJE)
    dez = mes(p, "2026-12")
    assert (dez["entradas"], dez["fixas"], dez["variaveis"], dez["sobra"]) == (480000, 125000, 200000, 155000)
    assert dez["saldo_inicio"] == 100000 and dez["saldo_fim"] == 255000
    assert mes(p, "2027-01")["saldo_fim"] == 410000
    assert p["resumo"]["saldo_final"] == 565000


def test_estimativa_do_mes_sobrepoe_o_padrao_e_pode_voltar(base):
    definir_estimativa(base, "2027-01", 150000)
    assert estimativa_do_mes(base, "2027-01") == (150000, "mes")
    assert mes(projecao(base, ["2027-01"], HOJE), "2027-01")["variaveis"] == 150000
    definir_estimativa(base, "2027-01", None)
    assert estimativa_do_mes(base, "2027-01") == (200000, "padrao")


def test_sem_estimativa_conta_so_o_lancado(s):
    assert estimativa_do_mes(s, "2027-01") == (0, "nenhuma")
    criar_lancamento(s, data=HOJE, descricao="Mercado", tipo="saida", valor_centavos=30000,
                     categoria_id=cat(s, "Alimentação"), conta_id=corrente(s), hoje=HOJE)
    assert mes(projecao(s, ["2026-12"], HOJE), "2026-12")["variaveis"] == 30000


def test_gasto_lancado_maior_que_estimativa_vale_o_lancado(base):
    criar_lancamento(base, data=HOJE, descricao="Mercado grande", tipo="saida", valor_centavos=250000,
                     categoria_id=cat(base, "Alimentação"), conta_id=corrente(base), hoje=HOJE)
    assert mes(projecao(base, ["2026-12"], HOJE), "2026-12")["variaveis"] == 250000


def test_gasto_planejado_soma_por_cima_da_estimativa(base):
    criar_lancamento(base, data=date(2027, 1, 20), descricao="IPVA", tipo="saida", valor_centavos=64000,
                     categoria_id=cat(base, "Transporte"), conta_id=corrente(base), hoje=HOJE)
    jan = mes(projecao(base, ["2027-01"], HOJE), "2027-01")
    assert jan["var_planejados"] == 64000 and jan["variaveis"] == 264000


def test_mes_passado_usa_o_real(base):
    criar_lancamento(base, data=date(2026, 11, 10), descricao="Farmácia", tipo="saida", valor_centavos=30000,
                     categoria_id=cat(base, "Saúde"), conta_id=corrente(base), hoje=HOJE)
    nov = mes(projecao(base, ["2026-11"], HOJE), "2026-11")
    assert nov["tipo"] == "real" and nov["variaveis"] == 30000
    assert nov["saldo_fim"] is None  # antes do mês do saldo inicial não há saldo


def test_compra_no_cartao_sai_do_caixa_no_mes_da_fatura(base):
    c = Conta(nome="Santander", tipo="cartao", dia_fechamento=13, dia_vencimento=20, modo_fatura="detalhado")
    base.add(c)
    base.commit()
    criar_lancamento(base, data=date(2026, 12, 20), descricao="Fone", tipo="saida", valor_centavos=300000,
                     categoria_id=cat(base, "Compras"), conta_id=c.id, hoje=HOJE)
    p = projecao(base, ["2026-12", "2027-01"], HOJE)
    assert mes(p, "2026-12")["var_lancados"] == 0 and mes(p, "2027-01")["var_lancados"] == 300000


def test_recorrencia_alem_do_horizonte_entra_na_projecao(base):
    dez27 = mes(projecao(base, ["2027-12"], HOJE), "2027-12")  # previstos só vão até nov/27
    assert dez27["entradas"] == 480000 and dez27["fixas"] == 125000


def test_saldo_inicial_invalido(s):
    with pytest.raises(ValueError):
        definir_saldo_inicial(s, 100, "12/2026")


# ------------------------------------------------------------------ relatórios
def test_relatorio_mensal_do_exemplo(s):
    popular_exemplo(s, HOJE)
    r = relatorio_mensal(s, "2026-12")
    assert r["entradas"] == 525000
    assert r["grupos"] == {"fixas": 179470, "parcelas": 61250, "variaveis": 118735}
    assert r["saidas"] == 359455 and r["sobra"] == 165545
    assert sum(c["valor"] for c in r["por_categoria"]) == r["saidas"]
    assert r["top"][0]["descricao"] == "Aluguel"


def test_relatorio_anual_do_exemplo(s):
    popular_exemplo(s, HOJE)
    r = relatorio_anual(s, 2026, HOJE)
    dez = r["meses"][11]
    assert (dez["entradas"], dez["saidas"]) == (525000, 359455)
    assert sum(c["total"] for c in r["categorias"]) == r["saidas"]
    assert r["sobra"] == r["entradas"] - r["saidas"]


def test_excel_abre_com_as_abas_certas(s):
    popular_exemplo(s, HOJE)
    wb = load_workbook(BytesIO(excel_mensal(s, "2026-12")))
    assert wb.sheetnames == ["Resumo", "Por categoria", "Lançamentos"]
    assert wb["Resumo"]["B2"].value == 5250.0
    p = projecao(s, [f"2026-{m:02d}" for m in range(1, 13)], HOJE)
    wb2 = load_workbook(BytesIO(excel_anual(s, 2026, p["meses"])))
    assert wb2.sheetnames == ["Resumo mensal", "Categorias x mês", "Projeção de saldo"]


# ------------------------------------------------------------------ filtro de meses
from app.servicos.relatorios import excel_periodo, relatorio_periodo  # noqa: E402
from app.servicos.util import periodo_filtrado  # noqa: E402


def test_periodo_filtrado():
    assert periodo_filtrado("2026-11", "2027-02") == ["2026-11", "2026-12", "2027-01", "2027-02"]
    assert periodo_filtrado("2027-02", "2026-11", "2026-12,2027-02") == ["2026-12", "2027-02"]  # aceita invertido
    assert periodo_filtrado("2026-01", "2026-03", "2030-01") == ["2026-01", "2026-02", "2026-03"]  # fora do período → todos
    with pytest.raises(ValueError):
        periodo_filtrado("2026-13", "2027-01")
    with pytest.raises(ValueError):
        periodo_filtrado("2026-01", "2029-12")  # mais de 36 meses


def test_projecao_com_meses_pulados_mantem_saldo_correto(base):
    completo = projecao(base, ["2026-12", "2027-01", "2027-02", "2027-03"], HOJE)
    pulado = projecao(base, ["2026-12", "2027-03"], HOJE)
    assert mes(pulado, "2027-03")["saldo_fim"] == mes(completo, "2027-03")["saldo_fim"]  # saldo conta os meses escondidos
    assert pulado["resumo"]["sobra"] == mes(completo, "2026-12")["sobra"] + mes(completo, "2027-03")["sobra"]


def test_relatorio_periodo_atravessando_o_ano(s):
    popular_exemplo(s, HOJE)
    r = relatorio_periodo(s, ["2026-12", "2027-01", "2027-02"], HOJE)
    assert [m["comp"] for m in r["meses"]] == ["2026-12", "2027-01", "2027-02"]
    assert all(len(c["meses"]) == 3 for c in r["categorias"])
    assert r["meses"][0]["entradas"] == 525000
    wb = load_workbook(BytesIO(excel_periodo(s, [f"2026-{m:02d}" for m in range(1, 13)] + [f"2027-{m:02d}" for m in range(1, 13)])))
    assert wb["Categorias x mês"].max_column == 26  # categoria + 24 meses + total
