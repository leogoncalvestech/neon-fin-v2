"""Contas fixas e receitas recorrentes: geração dos previstos e edição com escopo."""
from datetime import date

from sqlmodel import Session, select

from app.dados.modelos import Conta, Fatura, Lancamento, Recorrencia
from app.servicos.lancamentos import _recalcular_ajuste, aplicar_conta
from app.servicos.util import competencia, dia_seguro, meses_entre, somar_meses

HORIZONTE_MESES = 12


def criar_recorrencia(s: Session, *, descricao: str, tipo: str, valor_centavos: int, dia: int, inicio: str,
                      categoria_id: int, conta_id: int, fim: str | None = None, hoje: date | None = None) -> Recorrencia:
    if not (descricao or "").strip():
        raise ValueError("Informe uma descrição.")
    if valor_centavos <= 0:
        raise ValueError("O valor precisa ser maior que zero.")
    if not 1 <= dia <= 31:
        raise ValueError("O dia de vencimento deve estar entre 1 e 31.")
    if fim and fim < inicio:
        raise ValueError("O fim não pode ser antes do início.")
    conta = s.get(Conta, conta_id)
    if conta.tipo == "cartao" and conta.modo_fatura == "simples" and tipo == "saida":
        raise ValueError(f"O cartão {conta.nome} está no modo simples: a recorrência entraria duas vezes.")
    r = Recorrencia(descricao=descricao.strip(), tipo=tipo, valor_centavos=valor_centavos, dia=dia, inicio=inicio,
                    fim=fim, categoria_id=categoria_id, conta_id=conta_id)
    s.add(r)
    s.commit()
    s.refresh(r)
    garantir_previstos(s, hoje or date.today())
    return r


def garantir_previstos(s: Session, hoje: date, horizonte: int = HORIZONTE_MESES) -> int:
    """Cria os lançamentos previstos do mês atual até +horizonte. Idempotente: nunca duplica."""
    mes_atual = competencia(hoje)
    ultimo = somar_meses(mes_atual, horizonte - 1)
    criados, faturas = 0, set()
    for r in s.exec(select(Recorrencia).where(Recorrencia.ativa == True)):  # noqa: E712
        conta = s.get(Conta, r.conta_id)
        fim = min(r.fim, ultimo) if r.fim else ultimo
        # o mês da COBRANÇA (data) identifica cada ocorrência; no cartão o lançamento conta no mês da fatura
        existentes = {competencia(d) for d in s.exec(select(Lancamento.data).where(Lancamento.recorrencia_id == r.id))}
        for comp in meses_entre(max(r.inicio, mes_atual), fim):
            if comp in existentes:
                continue
            ano, mes = map(int, comp.split("-"))
            l = Lancamento(data=dia_seguro(ano, mes, r.dia), competencia=comp, competencia_caixa=comp,  # ajustado por aplicar_conta
                           descricao=r.descricao, tipo=r.tipo, valor_centavos=r.valor_centavos,
                           status="pago" if conta.tipo == "cartao" else "previsto", origem="recorrencia",
                           categoria_id=r.categoria_id, conta_id=r.conta_id, recorrencia_id=r.id)
            aplicar_conta(s, l, conta)
            s.add(l)
            faturas.add(l.fatura_id)
            criados += 1
    s.commit()
    for fid in faturas:
        _recalcular_ajuste(s, fid)
    return criados


def editar_valor_recorrencia(s: Session, rec_id: int, novo_valor_centavos: int, escopo: str, comp_ref: str) -> int:
    """escopo: 'so_este' | 'daqui_em_diante' | 'todos'. Lançamentos já pagos nunca são alterados
    (exceto em cartão, onde o item já está na fatura e é ajustado no previsto futuro)."""
    if novo_valor_centavos <= 0:
        raise ValueError("O valor precisa ser maior que zero.")
    r = s.get(Recorrencia, rec_id)
    q = select(Lancamento).where(Lancamento.recorrencia_id == rec_id)
    ini_ref = date(*map(int, comp_ref.split("-")), 1)
    fim_ref = date(*map(int, somar_meses(comp_ref, 1).split("-")), 1)
    if escopo == "so_este":
        q = q.where(Lancamento.data >= ini_ref, Lancamento.data < fim_ref)
    elif escopo == "daqui_em_diante":
        q = q.where(Lancamento.data >= ini_ref)
        r.valor_centavos = novo_valor_centavos
    elif escopo == "todos":
        r.valor_centavos = novo_valor_centavos
    else:
        raise ValueError("Escopo inválido.")
    conta = s.get(Conta, r.conta_id)
    afetados, faturas = 0, set()
    for l in s.exec(q):
        editavel = l.status == "previsto" or (conta.tipo == "cartao" and not _fatura_paga(s, l.fatura_id))
        if editavel:
            l.valor_centavos = novo_valor_centavos
            faturas.add(l.fatura_id)
            afetados += 1
    s.commit()
    for fid in faturas:
        _recalcular_ajuste(s, fid)
    return afetados


def _fatura_paga(s: Session, fatura_id: int | None) -> bool:
    f = s.get(Fatura, fatura_id) if fatura_id else None
    return bool(f and f.paga)


def encerrar_recorrencia(s: Session, rec_id: int, hoje: date) -> int:
    """Desativa e apaga só os previstos a partir do mês atual. Histórico pago fica."""
    r = s.get(Recorrencia, rec_id)
    r.ativa = False
    r.fim = somar_meses(competencia(hoje), -1) if r.inicio < competencia(hoje) else r.inicio
    apagados, faturas = 0, set()
    for l in s.exec(select(Lancamento).where(Lancamento.recorrencia_id == rec_id,
                                             Lancamento.data >= date(hoje.year, hoje.month, 1))):
        if l.status == "previsto" or not _fatura_paga(s, l.fatura_id):
            faturas.add(l.fatura_id)
            s.delete(l)
            apagados += 1
    s.commit()
    for fid in faturas:
        _recalcular_ajuste(s, fid)
    return apagados
