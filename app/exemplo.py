"""Dados de exemplo (os mesmos do PDF), gerados em torno do mês de 'hoje'.

Usado pelos testes e pelo comando `python main.py --exemplo` (banco separado: dados/exemplo.db).
"""
from datetime import date

from dateutil.relativedelta import relativedelta
from sqlmodel import Session, select

from app.dados.modelos import Categoria, Configuracao, Conta
from app.servicos.lancamentos import criar_lancamento
from app.servicos.recorrencias import criar_recorrencia
from app.servicos.util import competencia, dia_seguro


def _cat(s, nome):
    return s.exec(select(Categoria).where(Categoria.nome == nome)).one().id


def popular_exemplo(s: Session, hoje: date) -> dict:
    corrente = s.exec(select(Conta).where(Conta.nome == "Conta corrente")).one()
    nubank = Conta(nome="Nubank", tipo="cartao", cor="#9D4EDD", final="4417", limite_centavos=400000,
                   dia_fechamento=1, dia_vencimento=8, modo_fatura="hibrido")
    mp = Conta(nome="Mercado Pago", tipo="cartao", cor="#00B1EA", final="7710", limite_centavos=250000,
               dia_fechamento=3, dia_vencimento=10, modo_fatura="detalhado")
    santander = Conta(nome="Santander", tipo="cartao", cor="#FF2E88", final="0932", limite_centavos=350000,
                      dia_fechamento=13, dia_vencimento=20, modo_fatura="hibrido")
    s.add_all([nubank, mp, santander])
    s.get(Configuracao, "reserva_mensal_centavos").valor = "80000"  # R$ 800,00 para metas
    for chave, valor in {"saldo_inicial_centavos": "320000", "saldo_inicial_mes": competencia(hoje),
                         "estimativa_padrao_centavos": "200000"}.items():  # saldo R$ 3.200 · estimativa R$ 2.000
        cfg = s.get(Configuracao, chave)
        if cfg:
            cfg.valor = valor
        else:
            s.add(Configuracao(chave=chave, valor=valor))
    s.commit()

    mes = competencia(hoje)
    m0 = date(hoje.year, hoje.month, 1)
    rec = [("Salário", "entrada", 480000, 5, "Salário"), ("Academia", "saida", 11900, 1, "Saúde"),
           ("Aluguel", "saida", 125000, 5, "Moradia"), ("Seguro do carro", "saida", 21500, 10, "Transporte"),
           ("Celular", "saida", 5490, 12, "Assinaturas"), ("Internet", "saida", 9990, 15, "Moradia"),
           ("Streaming", "saida", 5590, 18, "Assinaturas")]
    for desc, tipo, v, dia, cat in rec:
        criar_recorrencia(s, descricao=desc, tipo=tipo, valor_centavos=v, dia=dia, inicio=mes,
                          categoria_id=_cat(s, cat), conta_id=corrente.id, hoje=hoje)

    # parcelas que caem neste mês: Notebook 3/10, Tênis 2/4, Pneus 1/5
    d = lambda meses, dia: dia_seguro((m0 + relativedelta(months=meses)).year, (m0 + relativedelta(months=meses)).month, dia)
    criar_lancamento(s, data=d(-3, 20), descricao="Notebook", tipo="saida", valor_centavos=320000,
                     categoria_id=_cat(s, "Compras"), conta_id=santander.id, parcelas=10, hoje=hoje)
    criar_lancamento(s, data=d(-2, 15), descricao="Tênis", tipo="saida", valor_centavos=45000,
                     categoria_id=_cat(s, "Compras"), conta_id=nubank.id, parcelas=4, hoje=hoje)
    criar_lancamento(s, data=d(-1, 5), descricao="Pneus", tipo="saida", valor_centavos=90000,
                     categoria_id=_cat(s, "Transporte"), conta_id=mp.id, parcelas=5, hoje=hoje)

    dia = lambda n: dia_seguro(hoje.year, hoje.month, min(n, hoje.day))
    criar_lancamento(s, data=dia(9), descricao="Freela — edição de vídeo", tipo="entrada", valor_centavos=45000,
                     categoria_id=_cat(s, "Renda extra"), conta_id=corrente.id, hoje=hoje)
    gastos = [(2, "Supermercado", 41240, "Alimentação"), (6, "Gasolina", 38900, "Transporte"),
              (13, "Cinema + pipoca", 21800, "Lazer"), (10, "Farmácia", 4200, "Saúde"),
              (8, "Amazon — fone", 9815, "Compras"), (11, "Presente", 2780, "Outros")]
    for n, desc, v, cat in gastos:
        criar_lancamento(s, data=dia(n), descricao=desc, tipo="saida", valor_centavos=v,
                         categoria_id=_cat(s, cat), conta_id=corrente.id, hoje=hoje)
    # contas fixas que já venceram estão pagas
    from app.dados.modelos import Lancamento
    for l in s.exec(select(Lancamento).where(Lancamento.status == "previsto")).all():
        if l.data < hoje:
            l.status = "pago"
    s.commit()
    # faturas que já venceram estão pagas
    from app.dados.modelos import Fatura
    from app.servicos.cartoes import datas_da_fatura, marcar_fatura_paga
    for f in s.exec(select(Fatura)).all():
        c = s.get(Conta, f.conta_id)
        if datas_da_fatura(f.competencia, c.dia_fechamento, c.dia_vencimento)[1] < hoje:
            marcar_fatura_paga(s, f.id)
    return {"nubank": nubank.id, "mp": mp.id, "santander": santander.id, "corrente": corrente.id}
