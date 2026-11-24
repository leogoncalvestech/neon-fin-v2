"""Criar, editar, excluir e pagar lançamentos (inclui compras no cartão e parceladas)."""
from datetime import date

from dateutil.relativedelta import relativedelta
from sqlmodel import Session, select

from app.dados.modelos import CompraParcelada, Conta, Fatura, Lancamento
from app.servicos.cartoes import dividir_parcelas, informar_valor_fatura, obter_fatura, vencimento_da_fatura
from app.servicos.util import competencia


def _validar(valor_centavos: int, descricao: str, tipo: str) -> None:
    if valor_centavos is None or valor_centavos <= 0:
        raise ValueError("O valor precisa ser maior que zero.")
    if not (descricao or "").strip():
        raise ValueError("Informe uma descrição.")
    if tipo not in ("entrada", "saida"):
        raise ValueError("Tipo deve ser entrada ou saída.")


def _recalcular_ajuste(s: Session, fatura_id: int | None) -> None:
    """Se a fatura já tem valor real informado, refaz o ajuste para não contar nada duas vezes."""
    if fatura_id is None:
        return
    f = s.get(Fatura, fatura_id)
    if f and f.valor_informado_centavos is not None:
        informar_valor_fatura(s, f.id, f.valor_informado_centavos)


def aplicar_conta(s: Session, lanc: Lancamento, conta: Conta) -> None:
    """Define o mês do lançamento conforme a conta.

    Cartão de crédito: o gasto conta no mês em que a FATURA VENCE (quando o dinheiro sai),
    tanto no "livre para gastar" quanto nos relatórios. Outras contas: mês da data do lançamento.
    """
    if conta.tipo == "cartao":
        venc = vencimento_da_fatura(lanc.data, conta.dia_fechamento, conta.dia_vencimento)
        lanc.competencia_caixa = competencia(venc)
        lanc.competencia = lanc.competencia_caixa
        lanc.fatura_id = obter_fatura(s, conta.id, lanc.competencia_caixa).id
    else:
        lanc.competencia = competencia(lanc.data)
        lanc.competencia_caixa = lanc.competencia
        lanc.fatura_id = None


def criar_lancamento(s: Session, *, data: date, descricao: str, tipo: str, valor_centavos: int,
                     categoria_id: int, conta_id: int, parcelas: int = 1, hoje: date | None = None,
                     observacao: str = "") -> list[Lancamento]:
    hoje = hoje or date.today()
    _validar(valor_centavos, descricao, tipo)
    conta = s.get(Conta, conta_id)
    if conta is None:
        raise ValueError("Conta/cartão não encontrado.")
    if parcelas > 1 and conta.tipo != "cartao":
        raise ValueError("Parcelamento só é permitido em cartão de crédito.")
    if conta.tipo == "cartao" and tipo == "saida" and conta.modo_fatura == "simples":
        raise ValueError(f"O cartão {conta.nome} está no modo simples: informe só o total da fatura na tela Cartões.")

    criados: list[Lancamento] = []
    if parcelas > 1:
        compra = CompraParcelada(descricao=descricao, valor_total_centavos=valor_centavos, n_parcelas=parcelas,
                                 data_compra=data, conta_id=conta.id, categoria_id=categoria_id)
        s.add(compra)
        s.flush()
        venc1 = vencimento_da_fatura(data, conta.dia_fechamento, conta.dia_vencimento)
        for i, v in enumerate(dividir_parcelas(valor_centavos, parcelas)):
            comp = competencia(venc1 + relativedelta(months=i))
            l = Lancamento(data=data, competencia=comp, competencia_caixa=comp, descricao=descricao, tipo="saida",
                           valor_centavos=v, status="pago", origem="parcela", categoria_id=categoria_id,
                           conta_id=conta.id, compra_id=compra.id, parcela_n=i + 1, parcela_total=parcelas,
                           fatura_id=obter_fatura(s, conta.id, comp).id, observacao=observacao)
            s.add(l)
            criados.append(l)
    else:
        status = "previsto" if (data > hoje and conta.tipo != "cartao") else "pago"
        l = Lancamento(data=data, competencia=competencia(data), competencia_caixa="", descricao=descricao.strip(),
                       tipo=tipo, valor_centavos=valor_centavos, status=status, origem="manual",
                       categoria_id=categoria_id, conta_id=conta.id, observacao=observacao)
        aplicar_conta(s, l, conta)
        s.add(l)
        criados.append(l)
    s.commit()
    for fid in {l.fatura_id for l in criados}:
        _recalcular_ajuste(s, fid)
    for l in criados:
        s.refresh(l)
    return criados


def editar_lancamento(s: Session, lanc_id: int, **campos) -> Lancamento:
    l = s.get(Lancamento, lanc_id)
    if l is None:
        raise ValueError("Lançamento não encontrado.")
    if l.origem in ("fatura_total", "ajuste_fatura"):
        raise ValueError("Este lançamento é gerado pela fatura; altere o valor na tela Cartões.")
    if l.compra_id and ({"valor_centavos", "data", "conta_id"} & campos.keys()):
        raise ValueError("Em compras parceladas só é possível alterar descrição e categoria. "
                         "Para mudar valor ou parcelas, exclua a compra e lance de novo.")
    fatura_antiga = l.fatura_id
    for k, v in campos.items():
        setattr(l, k, v)
    _validar(l.valor_centavos, l.descricao, l.tipo)
    if "data" in campos or "conta_id" in campos:
        conta = s.get(Conta, l.conta_id)
        if conta.tipo == "cartao" and conta.modo_fatura == "simples" and l.tipo == "saida" and l.origem == "manual":
            raise ValueError(f"O cartão {conta.nome} está no modo simples.")
        aplicar_conta(s, l, conta)
    s.commit()
    for fid in {fatura_antiga, l.fatura_id}:
        _recalcular_ajuste(s, fid)
    s.refresh(l)
    return l


def excluir_lancamento(s: Session, lanc_id: int, compra_inteira: bool = True) -> int:
    """Exclui o lançamento. Em parcelas, por padrão exclui a compra inteira. Retorna quantos foram apagados."""
    l = s.get(Lancamento, lanc_id)
    if l is None:
        return 0
    if l.origem in ("fatura_total", "ajuste_fatura"):
        raise ValueError("Este lançamento é gerado pela fatura; altere o valor na tela Cartões.")
    alvo = [l]
    if l.compra_id and compra_inteira:
        alvo = list(s.exec(select(Lancamento).where(Lancamento.compra_id == l.compra_id)))
    faturas = {x.fatura_id for x in alvo}
    compra_id = l.compra_id
    for x in alvo:
        s.delete(x)
    s.flush()
    if compra_id and compra_inteira:
        compra = s.get(CompraParcelada, compra_id)
        if compra:
            s.delete(compra)
    s.commit()
    for fid in faturas:
        _recalcular_ajuste(s, fid)
    return len(alvo)


def marcar_pago(s: Session, lanc_id: int, pago: bool = True, valor_real_centavos: int | None = None) -> Lancamento:
    l = s.get(Lancamento, lanc_id)
    l.status = "pago" if pago else "previsto"
    if valor_real_centavos is not None:
        if valor_real_centavos <= 0:
            raise ValueError("O valor precisa ser maior que zero.")
        l.valor_centavos = valor_real_centavos
    s.commit()
    s.refresh(l)
    return l


def lancamentos_do_mes(s: Session, comp: str) -> list[Lancamento]:
    return list(s.exec(select(Lancamento).where(Lancamento.competencia == comp)
                       .order_by(Lancamento.data.desc(), Lancamento.id.desc())))
