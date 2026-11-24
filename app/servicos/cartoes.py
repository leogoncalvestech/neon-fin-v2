"""Regras de cartão de crédito: em qual fatura cai cada compra, parcelas e valor da fatura."""
from datetime import date

from dateutil.relativedelta import relativedelta
from sqlmodel import Session, select

from app.dados.modelos import Categoria, Conta, Fatura, Lancamento
from app.servicos.util import competencia, dia_seguro


# ---------------------------------------------------------------- funções puras
def vencimento_da_fatura(data_compra: date, dia_fechamento: int, dia_vencimento: int) -> date:
    """Compra no dia do fechamento ou depois vai para a próxima fatura."""
    fechamento = dia_seguro(data_compra.year, data_compra.month, dia_fechamento)
    if data_compra >= fechamento:
        prox = data_compra + relativedelta(months=1)
        fechamento = dia_seguro(prox.year, prox.month, dia_fechamento)
    venc_base = fechamento
    if dia_vencimento <= dia_fechamento:  # ex.: fecha dia 25, vence dia 05 do mês seguinte
        venc_base = fechamento + relativedelta(months=1)
    return dia_seguro(venc_base.year, venc_base.month, dia_vencimento)


def datas_da_fatura(comp_vencimento: str, dia_fechamento: int, dia_vencimento: int) -> tuple[date, date]:
    """(fechamento, vencimento) da fatura que vence no mês comp_vencimento."""
    ano, mes = map(int, comp_vencimento.split("-"))
    venc = dia_seguro(ano, mes, dia_vencimento)
    base = venc - relativedelta(months=1) if dia_vencimento <= dia_fechamento else venc
    return dia_seguro(base.year, base.month, dia_fechamento), venc


def dividir_parcelas(total_centavos: int, n: int) -> list[int]:
    """Divide em N parcelas; os centavos que sobram vão para a 1ª."""
    if n < 1:
        raise ValueError("Número de parcelas deve ser pelo menos 1.")
    base = total_centavos // n
    resto = total_centavos - base * n
    return [base + resto] + [base] * (n - 1)


def status_fatura(fatura: Fatura, conta: Conta, hoje: date) -> str:
    if fatura.paga:
        return "paga"
    fech, venc = datas_da_fatura(fatura.competencia, conta.dia_fechamento, conta.dia_vencimento)
    if hoje > venc:
        return "atrasada"
    if hoje >= fech:
        return "fechada"
    return "aberta"


# ---------------------------------------------------------------- banco
def obter_fatura(s: Session, conta_id: int, comp: str) -> Fatura:
    f = s.exec(select(Fatura).where(Fatura.conta_id == conta_id, Fatura.competencia == comp)).first()
    if not f:
        f = Fatura(conta_id=conta_id, competencia=comp)
        s.add(f)
        s.flush()
    return f


def _categoria_cartao(s: Session) -> int:
    cat = s.exec(select(Categoria).where(Categoria.nome == "Cartão de crédito")).first()
    if not cat:
        cat = Categoria(nome="Cartão de crédito", tipo="saida", cor="#C18BFF")
        s.add(cat)
        s.flush()
    return cat.id


def itens_da_fatura(s: Session, fatura_id: int) -> list[Lancamento]:
    return list(s.exec(select(Lancamento).where(Lancamento.fatura_id == fatura_id,
                                                Lancamento.origem.in_(["manual", "parcela", "recorrencia"]))
                       .order_by(Lancamento.data)))


def resumo_fatura(s: Session, fatura: Fatura) -> dict:
    """Valor da fatura sem contar nada duas vezes.

    - detalhado: soma das compras e parcelas
    - híbrido:   valor informado (a diferença para a soma vira lançamento de ajuste)
    - simples:   valor informado (um único lançamento 'fatura_total')
    """
    itens = itens_da_fatura(s, fatura.id)
    soma = sum(i.valor_centavos if i.tipo == "saida" else -i.valor_centavos for i in itens)
    valor = fatura.valor_informado_centavos if fatura.valor_informado_centavos is not None else soma
    return {"itens": itens, "soma_itens": soma, "valor": valor,
            "ajuste": (valor - soma) if fatura.valor_informado_centavos is not None else 0}


def informar_valor_fatura(s: Session, fatura_id: int, valor_centavos: int) -> dict:
    """Grava o valor real da fatura (vindo do app do banco)."""
    if valor_centavos < 0:
        raise ValueError("O valor da fatura não pode ser negativo.")
    fatura = s.get(Fatura, fatura_id)
    conta = s.get(Conta, fatura.conta_id)
    fatura.valor_informado_centavos = valor_centavos
    _, venc = datas_da_fatura(fatura.competencia, conta.dia_fechamento, conta.dia_vencimento)

    soma_itens = resumo_fatura(s, fatura)["soma_itens"]
    # modo simples sem compras lançadas → um lançamento com o total;
    # qualquer outro caso → só a diferença (assim nada é contado duas vezes)
    origem = "fatura_total" if (conta.modo_fatura == "simples" and soma_itens == 0) else "ajuste_fatura"
    valor_lanc = valor_centavos if origem == "fatura_total" else valor_centavos - soma_itens
    # remove lançamentos antigos de total/ajuste desta fatura (evita contar duas vezes)
    for antigo in s.exec(select(Lancamento).where(Lancamento.fatura_id == fatura.id,
                                                  Lancamento.origem.in_(["fatura_total", "ajuste_fatura"]))):
        s.delete(antigo)
    s.flush()
    if valor_lanc != 0:
        s.add(Lancamento(
            data=venc, competencia=fatura.competencia, competencia_caixa=fatura.competencia,
            descricao=f"Fatura {conta.nome}" if origem == "fatura_total" else f"Ajuste fatura {conta.nome} (encargos/diferença)",
            tipo="saida" if valor_lanc > 0 else "entrada", valor_centavos=abs(valor_lanc),
            status="pago" if fatura.paga else "previsto", origem=origem,
            categoria_id=_categoria_cartao(s), conta_id=conta.id, fatura_id=fatura.id))
    s.commit()
    return resumo_fatura(s, fatura)


def marcar_fatura_paga(s: Session, fatura_id: int, paga: bool = True) -> None:
    fatura = s.get(Fatura, fatura_id)
    fatura.paga = paga
    for l in s.exec(select(Lancamento).where(Lancamento.fatura_id == fatura_id,
                                             Lancamento.origem.in_(["fatura_total", "ajuste_fatura"]))):
        l.status = "pago" if paga else "previsto"
    s.commit()


def faturas_do_cartao(s: Session, conta_id: int) -> list[Fatura]:
    return list(s.exec(select(Fatura).where(Fatura.conta_id == conta_id).order_by(Fatura.competencia)))


def limite_usado(s: Session, conta: Conta) -> int:
    """Soma das faturas não pagas (inclui parcelas futuras, que já estão nas faturas futuras)."""
    return sum(max(resumo_fatura(s, f)["valor"], 0) for f in faturas_do_cartao(s, conta.id) if not f.paga)


# ---------------------------------------------------------------- cadastro
def salvar_cartao(s: Session, *, nome: str, dia_fechamento: int, dia_vencimento: int, limite_centavos: int = 0,
                  cor: str = "#9D4EDD", final: str = "", modo_fatura: str = "hibrido", conta_id: int | None = None) -> Conta:
    """Cria ou edita um cartão. Mudar dias de fechamento/vencimento vale para as próximas compras."""
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Dê um nome ao cartão.")
    for d in (dia_fechamento, dia_vencimento):
        if d is None or not 1 <= int(d) <= 31:
            raise ValueError("Dias de fechamento e vencimento devem estar entre 1 e 31.")
    if modo_fatura not in ("detalhado", "simples", "hibrido"):
        raise ValueError("Modo de fatura inválido.")
    if limite_centavos < 0:
        raise ValueError("Limite não pode ser negativo.")
    c = s.get(Conta, conta_id) if conta_id else Conta(tipo="cartao")
    c.nome, c.cor, c.final = nome, cor, (final or "")[-4:]
    c.dia_fechamento, c.dia_vencimento = int(dia_fechamento), int(dia_vencimento)
    c.limite_centavos, c.modo_fatura = limite_centavos, modo_fatura
    s.add(c)
    s.commit()
    s.refresh(c)
    return c


def arquivar_cartao(s: Session, conta_id: int, arquivar: bool = True) -> None:
    c = s.get(Conta, conta_id)
    c.ativa = not arquivar
    s.commit()
