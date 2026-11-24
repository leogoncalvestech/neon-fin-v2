"""Visão anual: saldo previsto mês a mês (visão Caixa = quando o dinheiro sai/entra da conta).

Regras dos gastos variáveis:
- Meses que já passaram: valor REAL lançado.
- Mês atual e futuros: max(variáveis já lançados, estimativa do mês) + gastos planejados.
  * "planejados" = lançamentos manuais com status previsto (ex.: IPVA em janeiro, viagem em julho).
    Eles somam POR CIMA da estimativa, porque são gastos fora do dia a dia.
  * se você já gastou mais do que estimou, vale o que foi lançado.
- Estimativa do mês: o valor que você digitou para aquele mês; se não digitou, o valor padrão
  (Configurações); se nenhum dos dois, zero (só conta o que estiver lançado).
"""
from datetime import date

from sqlmodel import Session, select

from app.dados.modelos import Configuracao, Conta, EstimativaMes, Lancamento, Recorrencia
from app.servicos.cartoes import vencimento_da_fatura
from app.servicos.util import competencia, dia_seguro, meses_entre, somar_meses


# ------------------------------------------------------------------ configurações
def ler_config(s: Session, chave: str, padrao: str = "0") -> str:
    c = s.get(Configuracao, chave)
    return c.valor if c else padrao


def gravar_config(s: Session, chave: str, valor) -> None:
    c = s.get(Configuracao, chave)
    if c:
        c.valor = str(valor)
    else:
        s.add(Configuracao(chave=chave, valor=str(valor)))
    s.commit()


def definir_saldo_inicial(s: Session, valor_centavos: int, mes: str) -> None:
    """Saldo da conta no INÍCIO do mês informado (antes do salário e das contas daquele mês)."""
    if len(mes or "") != 7 or mes[4] != "-":
        raise ValueError("Mês do saldo inválido (use AAAA-MM).")
    gravar_config(s, "saldo_inicial_centavos", valor_centavos)
    gravar_config(s, "saldo_inicial_mes", mes)


def definir_estimativa(s: Session, comp: str, valor_centavos: int | None) -> None:
    """Grava a estimativa de gastos variáveis de um mês. None = volta a usar o padrão."""
    atual = s.get(EstimativaMes, comp)
    if valor_centavos is None:
        if atual:
            s.delete(atual)
    else:
        if valor_centavos < 0:
            raise ValueError("A estimativa não pode ser negativa.")
        if atual:
            atual.valor_centavos = valor_centavos
        else:
            s.add(EstimativaMes(competencia=comp, valor_centavos=valor_centavos))
    s.commit()


def estimativa_do_mes(s: Session, comp: str) -> tuple[int, str]:
    """(valor, origem) — origem: 'mes' (digitada p/ o mês), 'padrao' ou 'nenhuma'."""
    e = s.get(EstimativaMes, comp)
    if e:
        return e.valor_centavos, "mes"
    padrao = int(ler_config(s, "estimativa_padrao_centavos"))
    return (padrao, "padrao") if padrao > 0 else (0, "nenhuma")


# ------------------------------------------------------------------ totais por mês (caixa)
def _vazio():
    return {"entradas": 0, "fixas": 0, "parcelas": 0, "var_lancados": 0, "var_planejados": 0}


def totais_caixa(s: Session, comps: list[str], hoje: date) -> dict[str, dict]:
    t = {c: _vazio() for c in comps}
    lancs = s.exec(select(Lancamento).where(Lancamento.competencia_caixa.in_(comps))).all()
    # ocorrências já geradas de cada recorrência, pelo mês da cobrança
    existentes = {(rid, competencia(d)) for rid, d in
                  s.exec(select(Lancamento.recorrencia_id, Lancamento.data).where(Lancamento.recorrencia_id != None))}  # noqa: E711
    for l in lancs:
        x = t[l.competencia_caixa]
        sinal = 1 if l.tipo == "saida" else -1
        if l.tipo == "entrada" and l.origem in ("manual", "recorrencia"):
            x["entradas"] += l.valor_centavos
        elif l.origem == "recorrencia":
            x["fixas"] += l.valor_centavos
        elif l.origem == "parcela":
            x["parcelas"] += l.valor_centavos
        elif l.origem == "manual" and l.status == "previsto":
            x["var_planejados"] += l.valor_centavos
        else:  # manual pago, total de fatura, ajuste de fatura
            x["var_lancados"] += sinal * l.valor_centavos
    # recorrências além do horizonte de previstos já gerados: entram "virtualmente" na projeção
    mes_atual = competencia(hoje)
    for r in s.exec(select(Recorrencia).where(Recorrencia.ativa == True)):  # noqa: E712
        conta = s.get(Conta, r.conta_id)
        for c in meses_entre(somar_meses(comps[0], -1), comps[-1]):
            if c < mes_atual or c < r.inicio or (r.fim and c > r.fim) or (r.id, c) in existentes:
                continue
            destino = c
            if conta.tipo == "cartao":  # cobrança no cartão sai do caixa no mês da fatura
                ano, mes = map(int, c.split("-"))
                destino = competencia(vencimento_da_fatura(dia_seguro(ano, mes, r.dia), conta.dia_fechamento, conta.dia_vencimento))
            if destino in t:
                t[destino]["entradas" if r.tipo == "entrada" else "fixas"] += r.valor_centavos
    return t


# ------------------------------------------------------------------ projeção
def projecao(s: Session, comps: list[str], hoje: date) -> dict:
    """Linhas por mês com entradas, saídas, sobra e saldo acumulado (início e fim do mês)."""
    if not comps:
        return {"meses": [], "resumo": {}}
    mes_atual = competencia(hoje)
    saldo_ini = int(ler_config(s, "saldo_inicial_centavos"))
    base = ler_config(s, "saldo_inicial_mes", mes_atual)
    todos = list(meses_entre(min(base, comps[0]), comps[-1]))
    tot = totais_caixa(s, todos, hoje)

    linhas, saldo = {}, None
    for c in todos:
        x = tot[c]
        if c < mes_atual:
            est, origem_est = 0, "real"
            variaveis = x["var_lancados"] + x["var_planejados"]
            tipo = "real"
        else:
            est, origem_est = estimativa_do_mes(s, c)
            variaveis = max(x["var_lancados"], est) + x["var_planejados"]
            tipo = "atual" if c == mes_atual else "previsto"
        saidas = x["fixas"] + x["parcelas"] + variaveis
        sobra = x["entradas"] - saidas
        if c == base:
            saldo = saldo_ini
        inicio = saldo
        if saldo is not None:
            saldo += sobra
        linhas[c] = {"comp": c, "tipo": tipo, "entradas": x["entradas"], "fixas": x["fixas"], "parcelas": x["parcelas"],
                     "var_lancados": x["var_lancados"], "var_planejados": x["var_planejados"],
                     "estimativa": est, "origem_estimativa": origem_est, "variaveis": variaveis,
                     "saidas": saidas, "sobra": sobra, "saldo_inicio": inicio, "saldo_fim": saldo}

    meses = [linhas[c] for c in comps]
    com_saldo = [m for m in meses if m["saldo_fim"] is not None]
    menor = min(com_saldo, key=lambda m: m["saldo_fim"]) if com_saldo else None
    resumo = {"entradas": sum(m["entradas"] for m in meses), "saidas": sum(m["saidas"] for m in meses),
              "sobra": sum(m["sobra"] for m in meses),
              "saldo_final": meses[-1]["saldo_fim"], "menor_saldo": menor,
              "meses_negativos": [m["comp"] for m in meses if m["sobra"] < 0],
              "saldo_inicial": saldo_ini, "mes_base": base}
    return {"meses": meses, "resumo": resumo}
