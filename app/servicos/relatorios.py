"""Relatórios mensal e anual (cartão: compras e parcelas no mês em que a fatura vence)
e exportação para Excel."""
from collections import defaultdict
from datetime import date
from io import BytesIO

from sqlmodel import Session, select

from app.dados.modelos import Categoria, Conta, Lancamento
from app.servicos.util import competencia, nome_mes, somar_meses

DIAS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]


def _saida_assinada(l: Lancamento) -> int:
    """Quanto o lançamento pesa nas saídas (ajuste de fatura a favor entra negativo)."""
    if l.tipo == "saida":
        return l.valor_centavos
    if l.origem == "ajuste_fatura":
        return -l.valor_centavos
    return 0


def _eh_entrada(l: Lancamento) -> bool:
    return l.tipo == "entrada" and l.origem in ("manual", "recorrencia")


def _grupo(l: Lancamento) -> str:
    return {"recorrencia": "fixas", "parcela": "parcelas"}.get(l.origem, "variaveis")


def _lancs(s: Session, comps: list[str]) -> list[Lancamento]:
    return list(s.exec(select(Lancamento).where(Lancamento.competencia.in_(comps))))


def _saidas_por_categoria(lancs) -> dict[int, int]:
    d = defaultdict(int)
    for l in lancs:
        v = _saida_assinada(l)
        if v:
            d[l.categoria_id] += v
    return d


# ------------------------------------------------------------------ mensal
def relatorio_mensal(s: Session, comp: str) -> dict:
    cats = {c.id: c for c in s.exec(select(Categoria))}
    contas = {c.id: c for c in s.exec(select(Conta))}
    anteriores = [somar_meses(comp, -i) for i in (1, 2, 3)]
    todos = _lancs(s, [comp] + anteriores)
    lancs = [l for l in todos if l.competencia == comp]

    entradas = sum(l.valor_centavos for l in lancs if _eh_entrada(l))
    saidas = sum(_saida_assinada(l) for l in lancs)
    grupos = defaultdict(int)
    for l in lancs:
        grupos[_grupo(l)] += _saida_assinada(l)

    atual = _saidas_por_categoria(lancs)
    ant = _saidas_por_categoria([l for l in todos if l.competencia == anteriores[0]])
    por_mes_ant = [_saidas_por_categoria([l for l in todos if l.competencia == c]) for c in anteriores]
    meses_com_dados = [d for d in por_mes_ant if d] or [{}]
    por_categoria = []
    for cid in sorted(set(atual) | set(ant), key=lambda c: -atual.get(c, 0)):
        v = atual.get(cid, 0)
        media3 = sum(d.get(cid, 0) for d in meses_com_dados) // len(meses_com_dados)
        por_categoria.append({"categoria": cats[cid].nome, "cor": cats[cid].cor, "valor": v,
                              "pct": v / saidas if saidas > 0 else 0, "mes_anterior": ant.get(cid, 0),
                              "media_3m": media3, "variacao": v - ant.get(cid, 0)})

    por_conta = defaultdict(int)
    por_dia = [0] * 7
    for l in lancs:
        v = _saida_assinada(l)
        if v:
            por_conta[contas[l.conta_id].nome] += v
        if l.tipo == "saida" and l.origem == "manual":
            por_dia[l.data.weekday()] += l.valor_centavos

    top = sorted([l for l in lancs if l.tipo == "saida"], key=lambda l: -l.valor_centavos)[:10]
    pendentes = [l for l in lancs if l.status == "previsto"]
    return {"comp": comp, "entradas": entradas, "saidas": saidas, "sobra": entradas - saidas,
            "taxa_poupanca": (entradas - saidas) / entradas if entradas else 0,
            "grupos": {"fixas": grupos["fixas"], "parcelas": grupos["parcelas"], "variaveis": grupos["variaveis"]},
            "por_categoria": por_categoria,
            "por_conta": sorted(por_conta.items(), key=lambda x: -x[1]),
            "por_dia_semana": list(zip(DIAS, por_dia)),
            "top": [{"data": l.data, "descricao": l.descricao, "categoria": cats[l.categoria_id].nome,
                     "conta": contas[l.conta_id].nome, "valor": l.valor_centavos,
                     "parcela": f"{l.parcela_n}/{l.parcela_total}" if l.parcela_total else ""} for l in top],
            "pendentes": {"qtd": len(pendentes), "valor": sum(l.valor_centavos for l in pendentes if l.tipo == "saida")},
            "lancamentos": sorted(lancs, key=lambda l: (l.data, l.id)), "_cats": cats, "_contas": contas}


# ------------------------------------------------------------------ anual / período
def relatorio_anual(s: Session, ano: int, hoje: date | None = None) -> dict:
    r = relatorio_periodo(s, [f"{ano}-{m:02d}" for m in range(1, 13)], hoje)
    r["ano"] = ano
    return r


def relatorio_periodo(s: Session, comps: list[str], hoje: date | None = None) -> dict:
    """Relatório de qualquer conjunto de meses (contíguos ou não)."""
    hoje = hoje or date.today()
    comps = sorted(comps)
    cats = {c.id: c for c in s.exec(select(Categoria))}
    lancs = _lancs(s, comps)
    meses = []
    matriz = defaultdict(lambda: [0] * len(comps))
    for i, c in enumerate(comps):
        ls = [l for l in lancs if l.competencia == c]
        ent = sum(l.valor_centavos for l in ls if _eh_entrada(l))
        g = defaultdict(int)
        for l in ls:
            v = _saida_assinada(l)
            g[_grupo(l)] += v
            if v:
                matriz[l.categoria_id][i] += v
        sai = sum(g.values())
        meses.append({"comp": c, "nome": nome_mes(c), "entradas": ent, "saidas": sai, "sobra": ent - sai,
                      "fixas": g["fixas"], "parcelas": g["parcelas"], "variaveis": g["variaveis"],
                      "futuro": c > competencia(hoje), "tem_dados": bool(ls)})
    categorias = sorted(({"categoria": cats[cid].nome, "cor": cats[cid].cor, "meses": vals, "total": sum(vals)}
                         for cid, vals in matriz.items()), key=lambda x: -x["total"])
    com_dados = [m for m in meses if m["tem_dados"]]
    ent, sai = sum(m["entradas"] for m in meses), sum(m["saidas"] for m in meses)
    maior = max(com_dados, key=lambda m: m["saidas"]) if com_dados else None
    return {"comps": comps, "meses": meses, "categorias": categorias, "entradas": ent, "saidas": sai, "sobra": ent - sai,
            "taxa_poupanca": (ent - sai) / ent if ent else 0,
            "media_saidas": sai // len(com_dados) if com_dados else 0,
            "maior_mes": maior, "meses_com_dados": len(com_dados)}


# ------------------------------------------------------------------ Excel
def _planilha(ws, cabecalho: list[str], linhas: list[list], col_moeda: set[int], larguras: list[int] | None = None):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    ws.append(cabecalho)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="12121A")
        c.alignment = Alignment(horizontal="center")
    for lin in linhas:
        ws.append(lin)
    for row in ws.iter_rows(min_row=2):
        for i, c in enumerate(row):
            if i in col_moeda and isinstance(c.value, (int, float)):
                c.number_format = '"R$" #,##0.00;[Red]-"R$" #,##0.00'
    for i, w in enumerate(larguras or [16] * len(cabecalho)):
        ws.column_dimensions[get_column_letter(i + 1)].width = w
    ws.freeze_panes = "A2"


def excel_mensal(s: Session, comp: str) -> bytes:
    from openpyxl import Workbook
    r = relatorio_mensal(s, comp)
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumo"
    _planilha(ws, ["Item", "Valor"], [["Entradas", r["entradas"] / 100], ["Contas fixas", r["grupos"]["fixas"] / 100],
                                      ["Parcelas", r["grupos"]["parcelas"] / 100], ["Gastos variáveis", r["grupos"]["variaveis"] / 100],
                                      ["Total de saídas", r["saidas"] / 100], ["Sobra do mês", r["sobra"] / 100]], {1}, [24, 16])
    _planilha(wb.create_sheet("Por categoria"), ["Categoria", "Valor", "% das saídas", "Mês anterior", "Média 3 meses"],
              [[c["categoria"], c["valor"] / 100, round(c["pct"] * 100, 1), c["mes_anterior"] / 100, c["media_3m"] / 100]
               for c in r["por_categoria"]], {1, 3, 4}, [22, 14, 14, 14, 16])
    _planilha(wb.create_sheet("Lançamentos"), ["Data", "Descrição", "Categoria", "Conta", "Tipo", "Valor", "Status", "Parcela"],
              [[l.data.strftime("%d/%m/%Y"), l.descricao, r["_cats"][l.categoria_id].nome, r["_contas"][l.conta_id].nome,
                l.tipo, (l.valor_centavos if l.tipo == "entrada" else -l.valor_centavos) / 100, l.status,
                f"{l.parcela_n}/{l.parcela_total}" if l.parcela_total else ""] for l in r["lancamentos"]],
              {5}, [12, 30, 18, 18, 10, 14, 10, 9])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def excel_anual(s: Session, ano: int, projecao_meses: list[dict] | None = None) -> bytes:
    return excel_periodo(s, [f"{ano}-{m:02d}" for m in range(1, 13)], projecao_meses)


def excel_periodo(s: Session, comps: list[str], projecao_meses: list[dict] | None = None) -> bytes:
    from openpyxl import Workbook
    r = relatorio_periodo(s, comps)
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumo mensal"
    _planilha(ws, ["Mês", "Entradas", "Fixas", "Parcelas", "Variáveis", "Saídas", "Sobra"],
              [[m["nome"], m["entradas"] / 100, m["fixas"] / 100, m["parcelas"] / 100, m["variaveis"] / 100,
                m["saidas"] / 100, m["sobra"] / 100] for m in r["meses"]] +
              [["TOTAL", r["entradas"] / 100, None, None, None, r["saidas"] / 100, r["sobra"] / 100]],
              {1, 2, 3, 4, 5, 6}, [10, 14, 14, 14, 14, 14, 14])
    _planilha(wb.create_sheet("Categorias x mês"), ["Categoria"] + [m["nome"] for m in r["meses"]] + ["Total"],
              [[c["categoria"]] + [v / 100 for v in c["meses"]] + [c["total"] / 100] for c in r["categorias"]],
              set(range(1, len(r["meses"]) + 2)), [20] + [11] * (len(r["meses"]) + 1))
    if projecao_meses:
        _planilha(wb.create_sheet("Projeção de saldo"),
                  ["Mês", "Tipo", "Entradas", "Fixas", "Parcelas", "Variáveis", "Sobra", "Saldo no fim do mês"],
                  [[nome_mes(m["comp"]), m["tipo"], m["entradas"] / 100, m["fixas"] / 100, m["parcelas"] / 100,
                    m["variaveis"] / 100, m["sobra"] / 100, None if m["saldo_fim"] is None else m["saldo_fim"] / 100]
                   for m in projecao_meses], {2, 3, 4, 5, 6, 7}, [10, 10, 14, 14, 14, 14, 14, 20])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
