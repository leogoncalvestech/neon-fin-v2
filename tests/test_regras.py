"""Testes das regras de negócio. Rode com:  python -m pytest -q"""
from datetime import date

import pytest
from sqlmodel import Session, select

from app.dados.banco import criar_engine, iniciar_banco
from app.dados.modelos import Categoria, Conta, Fatura, Lancamento
from app.exemplo import popular_exemplo
from app.servicos.cartoes import (datas_da_fatura, dividir_parcelas, informar_valor_fatura, marcar_fatura_paga,
                                  obter_fatura, resumo_fatura, vencimento_da_fatura)
from app.servicos.lancamentos import criar_lancamento, editar_lancamento, excluir_lancamento
from app.servicos.livre import livre_para_gastar, proximos_vencimentos, resumo_mes
from app.servicos.recorrencias import (criar_recorrencia, editar_valor_recorrencia, encerrar_recorrencia,
                                       garantir_previstos)
from app.servicos.util import brl, parse_valor

HOJE = date(2026, 12, 14)  # segunda-feira, como no PDF


@pytest.fixture
def s():
    eng = criar_engine(":memory:")
    iniciar_banco(eng)
    with Session(eng) as sess:
        yield sess


def cat(s, nome):
    return s.exec(select(Categoria).where(Categoria.nome == nome)).one().id


def cartao(s, nome="Teste", fech=13, venc=20, modo="hibrido"):
    c = Conta(nome=nome, tipo="cartao", dia_fechamento=fech, dia_vencimento=venc, modo_fatura=modo, limite_centavos=500000)
    s.add(c)
    s.commit()
    return c


def corrente(s):
    return s.exec(select(Conta).where(Conta.nome == "Conta corrente")).one()


# ------------------------------------------------------------ livre para gastar
def test_livre_exemplo_do_pdf():
    r = livre_para_gastar(525000, 179470, 61250, 80000, 118735, HOJE)
    assert r["disponivel"] == 204280          # R$ 2.042,80
    assert r["livre"] == 85545                # R$ 855,45
    assert r["dias_restantes"] == 18
    assert r["por_dia"] == 4752               # arredonda para baixo (conservador)
    assert r["semana"] == 4752 * 7            # seg 14 → dom 20


def test_livre_negativo_zera_dia_e_semana():
    r = livre_para_gastar(100000, 50000, 0, 0, 70000, HOJE)
    assert r["livre"] == -20000 and r["por_dia"] == 0 and r["semana"] == 0


def test_semana_nao_passa_do_fim_do_mes():
    r = livre_para_gastar(310000, 0, 0, 0, 0, date(2026, 12, 29), "2026-12")  # terça; mês acaba quinta
    assert r["dias_restantes"] == 3 and r["semana"] == r["por_dia"] * 3


def test_resumo_mes_reproduz_o_pdf_pelo_banco(s):
    popular_exemplo(s, HOJE)
    r = resumo_mes(s, "2026-12", HOJE)
    assert (r["entradas"], r["fixas"], r["parcelas"], r["metas"], r["variaveis"]) == (525000, 179470, 61250, 80000, 118735)
    assert r["livre"] == 85545


# ------------------------------------------------------------ fatura: em qual mês cai
@pytest.mark.parametrize("compra,fech,venc,esperado", [
    (date(2026, 12, 10), 1, 8, date(2027, 1, 8)),     # Nubank
    (date(2026, 11, 30), 1, 8, date(2026, 12, 8)),
    (date(2026, 12, 12), 13, 20, date(2026, 12, 20)),  # Santander, antes do fechamento
    (date(2026, 12, 13), 13, 20, date(2027, 1, 20)),   # no dia do fechamento → próxima
    (date(2026, 11, 14), 13, 20, date(2026, 12, 20)),
    (date(2026, 12, 20), 25, 5, date(2027, 1, 5)),     # vence no mês seguinte ao fechamento
    (date(2026, 12, 26), 25, 5, date(2027, 2, 5)),
    (date(2027, 1, 31), 31, 7, date(2027, 3, 7)),      # dia 31 em mês curto
])
def test_vencimento_da_fatura(compra, fech, venc, esperado):
    assert vencimento_da_fatura(compra, fech, venc) == esperado


def test_datas_da_fatura_batem_com_o_vencimento():
    assert datas_da_fatura("2026-12", 13, 20) == (date(2026, 12, 13), date(2026, 12, 20))
    assert datas_da_fatura("2027-01", 25, 5) == (date(2026, 12, 25), date(2027, 1, 5))


def test_dividir_parcelas_centavos_na_primeira():
    p = dividir_parcelas(100000, 3)
    assert p == [33334, 33333, 33333] and sum(p) == 100000


# ------------------------------------------------------------ lançamentos
def test_compra_parcelada_gera_parcelas_nas_faturas_certas(s):
    c = cartao(s, "Santander", 13, 20)
    ls = criar_lancamento(s, data=date(2026, 12, 8), descricao="Celular", tipo="saida", valor_centavos=240000,
                          categoria_id=cat(s, "Compras"), conta_id=c.id, parcelas=10, hoje=HOJE)
    assert len(ls) == 10
    assert [l.competencia for l in ls][:2] == ["2026-12", "2027-01"] and ls[-1].competencia == "2027-09"
    assert all(l.valor_centavos == 24000 for l in ls)
    assert len({l.fatura_id for l in ls}) == 10


def test_validacoes(s):
    with pytest.raises(ValueError, match="maior que zero"):
        criar_lancamento(s, data=HOJE, descricao="x", tipo="saida", valor_centavos=0,
                         categoria_id=cat(s, "Outros"), conta_id=corrente(s).id)
    with pytest.raises(ValueError, match="cartão de crédito"):
        criar_lancamento(s, data=HOJE, descricao="x", tipo="saida", valor_centavos=100,
                         categoria_id=cat(s, "Outros"), conta_id=corrente(s).id, parcelas=3)


def test_lancamento_futuro_fica_previsto(s):
    l = criar_lancamento(s, data=date(2026, 12, 20), descricao="IPVA", tipo="saida", valor_centavos=64000,
                         categoria_id=cat(s, "Transporte"), conta_id=corrente(s).id, hoje=HOJE)[0]
    assert l.status == "previsto"
    assert any(v["lancamento_id"] == l.id for v in proximos_vencimentos(s, HOJE))


def test_editar_data_muda_competencia(s):
    l = criar_lancamento(s, data=HOJE, descricao="Mercado", tipo="saida", valor_centavos=5000,
                         categoria_id=cat(s, "Alimentação"), conta_id=corrente(s).id, hoje=HOJE)[0]
    l = editar_lancamento(s, l.id, data=date(2027, 1, 3), valor_centavos=6000)
    assert l.competencia == "2027-01" and l.valor_centavos == 6000


def test_excluir_parcela_apaga_a_compra_inteira(s):
    c = cartao(s)
    ls = criar_lancamento(s, data=HOJE, descricao="TV", tipo="saida", valor_centavos=300000,
                          categoria_id=cat(s, "Compras"), conta_id=c.id, parcelas=3, hoje=HOJE)
    assert excluir_lancamento(s, ls[1].id) == 3
    assert s.exec(select(Lancamento)).all() == []


# ------------------------------------------------------------ 3 modos de fatura (sem contar duas vezes)
def _total_cartao(s, fatura_id):
    ls = s.exec(select(Lancamento).where(Lancamento.fatura_id == fatura_id)).all()
    return sum(l.valor_centavos if l.tipo == "saida" else -l.valor_centavos for l in ls)


def test_modo_detalhado_soma_as_compras(s):
    c = cartao(s, modo="detalhado")
    for v in (10000, 5000):
        criar_lancamento(s, data=date(2026, 12, 5), descricao="x", tipo="saida", valor_centavos=v,
                         categoria_id=cat(s, "Outros"), conta_id=c.id, hoje=HOJE)
    f = obter_fatura(s, c.id, "2026-12")
    assert resumo_fatura(s, f)["valor"] == 15000


def test_modo_simples_nao_aceita_compras_e_usa_o_total(s):
    c = cartao(s, modo="simples")
    with pytest.raises(ValueError, match="modo simples"):
        criar_lancamento(s, data=HOJE, descricao="x", tipo="saida", valor_centavos=100,
                         categoria_id=cat(s, "Outros"), conta_id=c.id)
    f = obter_fatura(s, c.id, "2026-12")
    informar_valor_fatura(s, f.id, 114830)
    informar_valor_fatura(s, f.id, 114830)  # informar de novo não duplica
    assert _total_cartao(s, f.id) == 114830
    assert resumo_mes(s, "2026-12", HOJE)["variaveis"] == 114830


def test_modo_hibrido_cria_ajuste_e_recalcula(s):
    c = cartao(s, modo="hibrido")
    criar_lancamento(s, data=date(2026, 12, 5), descricao="a", tipo="saida", valor_centavos=10000,
                     categoria_id=cat(s, "Outros"), conta_id=c.id, hoje=HOJE)
    criar_lancamento(s, data=date(2026, 12, 6), descricao="b", tipo="saida", valor_centavos=5000,
                     categoria_id=cat(s, "Outros"), conta_id=c.id, hoje=HOJE)
    f = obter_fatura(s, c.id, "2026-12")
    r = informar_valor_fatura(s, f.id, 16000)              # banco cobrou R$ 10 a mais (IOF)
    assert r["ajuste"] == 1000 and _total_cartao(s, f.id) == 16000
    criar_lancamento(s, data=date(2026, 12, 7), descricao="esquecida", tipo="saida", valor_centavos=2000,
                     categoria_id=cat(s, "Outros"), conta_id=c.id, hoje=HOJE)
    assert resumo_fatura(s, f)["ajuste"] == -1000          # ajuste refeito sozinho
    assert _total_cartao(s, f.id) == 16000                 # continua batendo com o banco


def test_trocar_para_simples_com_compras_nao_duplica(s):
    c = cartao(s, modo="hibrido")
    criar_lancamento(s, data=date(2026, 12, 5), descricao="a", tipo="saida", valor_centavos=30000,
                     categoria_id=cat(s, "Outros"), conta_id=c.id, hoje=HOJE)
    c.modo_fatura = "simples"
    s.commit()
    f = obter_fatura(s, c.id, "2026-12")
    informar_valor_fatura(s, f.id, 45000)
    assert _total_cartao(s, f.id) == 45000


def test_pagar_fatura_marca_ajuste_como_pago(s):
    c = cartao(s, modo="simples")
    f = obter_fatura(s, c.id, "2026-12")
    informar_valor_fatura(s, f.id, 50000)
    assert any(v["fatura_id"] == f.id for v in proximos_vencimentos(s, HOJE))
    marcar_fatura_paga(s, f.id)
    assert all(l.status == "pago" for l in s.exec(select(Lancamento).where(Lancamento.fatura_id == f.id)))
    assert not any(v["fatura_id"] == f.id for v in proximos_vencimentos(s, HOJE))


# ------------------------------------------------------------ recorrências
def _rec(s, **kw):
    base = dict(descricao="Aluguel", tipo="saida", valor_centavos=125000, dia=31, inicio="2026-12",
                categoria_id=cat(s, "Moradia"), conta_id=corrente(s).id, hoje=HOJE)
    base.update(kw)
    return criar_recorrencia(s, **base)


def test_recorrencia_gera_12_meses_sem_duplicar_e_ajusta_dia_31(s):
    r = _rec(s)
    assert garantir_previstos(s, HOJE) == 0                 # rodar de novo não duplica
    ls = s.exec(select(Lancamento).where(Lancamento.recorrencia_id == r.id).order_by(Lancamento.competencia)).all()
    assert len(ls) == 12
    fev = next(l for l in ls if l.competencia == "2027-02")
    assert fev.data == date(2027, 2, 28)


def test_recorrencia_respeita_fim(s):
    r = _rec(s, fim="2027-03")
    assert len(s.exec(select(Lancamento).where(Lancamento.recorrencia_id == r.id)).all()) == 4


def test_editar_recorrencia_com_escopo(s):
    r = _rec(s, dia=5)
    dez = s.exec(select(Lancamento).where(Lancamento.recorrencia_id == r.id, Lancamento.competencia == "2026-12")).one()
    dez.status = "pago"
    s.commit()
    assert editar_valor_recorrencia(s, r.id, 130000, "so_este", "2027-01") == 1
    assert editar_valor_recorrencia(s, r.id, 132000, "daqui_em_diante", "2027-03") == 9   # mar/27 → nov/27
    valores = {l.competencia: l.valor_centavos for l in s.exec(select(Lancamento).where(Lancamento.recorrencia_id == r.id))}
    assert valores["2026-12"] == 125000                    # pago: não muda
    assert valores["2027-01"] == 130000 and valores["2027-02"] == 125000 and valores["2027-03"] == 132000
    assert editar_valor_recorrencia(s, r.id, 140000, "todos", "2027-01") == 11   # todos os previstos


def test_encerrar_recorrencia_mantem_historico_pago(s):
    r = _rec(s, inicio="2026-11", hoje=date(2026, 11, 20))
    nov = s.exec(select(Lancamento).where(Lancamento.recorrencia_id == r.id, Lancamento.competencia == "2026-11")).one()
    nov.status = "pago"
    s.commit()
    encerrar_recorrencia(s, r.id, HOJE)
    restantes = s.exec(select(Lancamento).where(Lancamento.recorrencia_id == r.id)).all()
    assert [l.competencia for l in restantes] == ["2026-11"]


# ------------------------------------------------------------ dinheiro
@pytest.mark.parametrize("txt,cent", [("38,90", 3890), ("38.90", 3890), ("R$ 1.234,56", 123456), ("1.234", 123400),
                                      ("1234.5", 123450), ("10", 1000), (38.9, 3890)])
def test_parse_valor(txt, cent):
    assert parse_valor(txt) == cent


def test_brl():
    assert brl(85545) == "R$ 855,45" and brl(123456) == "R$ 1.234,56" and brl(-3890, sinal=True) == "- R$ 38,90"


# ------------------------------------------------------------ cartão conta no mês da fatura
def test_compra_no_cartao_conta_no_mes_da_fatura(s):
    c = cartao(s, "Nubank", 1, 8)
    l = criar_lancamento(s, data=date(2026, 12, 10), descricao="Mercado", tipo="saida", valor_centavos=10000,
                         categoria_id=cat(s, "Alimentação"), conta_id=c.id, hoje=HOJE)[0]
    assert l.competencia == "2027-01" and l.competencia_caixa == "2027-01"   # fatura vence 08/01
    assert resumo_mes(s, "2026-12", HOJE)["variaveis"] == 0
    assert resumo_mes(s, "2027-01", HOJE)["variaveis"] == 10000


def test_editar_data_no_cartao_muda_para_a_fatura_certa(s):
    c = cartao(s, "Santander", 13, 20)
    l = criar_lancamento(s, data=date(2026, 12, 12), descricao="x", tipo="saida", valor_centavos=500,
                         categoria_id=cat(s, "Outros"), conta_id=c.id, hoje=HOJE)[0]
    assert l.competencia == "2026-12"
    l = editar_lancamento(s, l.id, data=date(2026, 12, 13))                  # no dia do fechamento
    assert l.competencia == "2027-01"


def test_recorrencia_no_cartao_nao_duplica_e_conta_na_fatura(s):
    c = cartao(s, "Santander", 13, 20)
    r = criar_recorrencia(s, descricao="Streaming", tipo="saida", valor_centavos=5590, dia=18, inicio="2026-12",
                          categoria_id=cat(s, "Assinaturas"), conta_id=c.id, hoje=HOJE)
    assert garantir_previstos(s, HOJE) == 0
    ls = s.exec(select(Lancamento).where(Lancamento.recorrencia_id == r.id).order_by(Lancamento.data)).all()
    assert len(ls) == 12
    assert ls[0].data == date(2026, 12, 18) and ls[0].competencia == "2027-01"   # cobra dia 18, fatura vence 20/01
    assert editar_valor_recorrencia(s, r.id, 6000, "so_este", "2026-12") == 1
    assert ls[0].valor_centavos == 6000 or s.get(Lancamento, ls[0].id).valor_centavos == 6000
