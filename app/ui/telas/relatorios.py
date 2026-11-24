from datetime import date

from nicegui import ui

from app.dados.banco import sessao
from app.servicos.projecao import projecao
from app.servicos.relatorios import excel_mensal, excel_periodo, relatorio_mensal, relatorio_periodo
from app.servicos.util import brl, competencia, nome_mes, periodo_filtrado
from app.ui.componentes import cabecalho, echart_base, filtro_meses, fmt_curto, html, moldura, seletor_mes


def _kpis(itens):
    cards = "".join(f'''<div class="nf-panel"><div class="nf-lbl">{t}</div>
        <div class="num {c}" style="font-size:21px;margin-top:6px">{v}</div><div class="dim" style="font-size:12px">{sub}</div></div>'''
                    for t, v, c, sub in itens)
    html(f'<div style="display:grid;grid-template-columns:repeat({len(itens)},1fr);gap:14px;margin-bottom:14px">{cards}</div>')


def _variacao(v, ant):
    if not ant:
        return '<span class="dim">novo</span>' if v else ""
    d = (v - ant) / ant
    if abs(d) < .005:
        return '<span class="dim">=</span>'
    return f'<span class="{"c-mg" if d > 0 else "c-gr"}">{"▲" if d > 0 else "▼"} {abs(d) * 100:.0f}%</span>'


@ui.page("/relatorios")
def pagina_relatorios(tipo: str = "mensal", mes: str | None = None, de: str | None = None, ate: str | None = None,
                      meses: str | None = None):
    hoje = date.today()
    comp = mes or competencia(hoje)
    de, ate = de or f"{hoje.year}-01", ate or f"{hoje.year}-12"
    erro = None
    try:
        selecionados = periodo_filtrado(de, ate, meses)
    except ValueError as e:
        erro, de, ate = str(e), f"{hoje.year}-01", f"{hoje.year}-12"
        selecionados = periodo_filtrado(de, ate)
    de, ate = min(de, ate), max(de, ate)
    rotulo = f"{nome_mes(selecionados[0])} a {nome_mes(selecionados[-1])}" if len(selecionados) > 1 else nome_mes(selecionados[0])

    # ======================================================================= MENSAL
    def mensal():
        with sessao() as s:
            r = relatorio_mensal(s, comp)
        g = r["grupos"]
        _kpis([("Entradas", brl(r["entradas"]), "g-gr", "salário e outras receitas"),
               ("Saídas", brl(r["saidas"]), "g-mg", f'fixas {fmt_curto(g["fixas"])} · parcelas {fmt_curto(g["parcelas"])} · variáveis {fmt_curto(g["variaveis"])}'),
               ("Sobra do mês", brl(r["sobra"], sinal=True), "g-cy" if r["sobra"] >= 0 else "g-mg", "entradas − saídas"),
               ("Taxa de poupança", f'{r["taxa_poupanca"] * 100:.1f}%', "g-pu", "quanto da renda sobrou"),
               ("A pagar no mês", brl(r["pendentes"]["valor"]), "c-ye", f'{r["pendentes"]["qtd"]} lançamento(s) previstos')])

        with ui.element("div").classes("w-full").style("display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr);gap:14px"):
            with ui.element("div").classes("nf-panel"):
                ui.label("Saídas por categoria").classes("nf-h mb-2")
                if not r["por_categoria"]:
                    html('<div class="vazio">Sem saídas neste mês.</div>')
                else:
                    cols = "minmax(110px,1.2fr) minmax(50px,1fr) 100px 40px 96px 76px"
                    html(f'''<div class="nf-row head" style="grid-template-columns:{cols}"><span>Categoria</span><span></span>
                        <span class="r">Valor</span><span class="r">%</span><span class="r">Média 3m</span><span class="r">vs ant.</span></div>''')
                    for c in r["por_categoria"]:
                        html(f'''<div class="nf-row" style="grid-template-columns:{cols}">
                            <span class="c-w"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{c["cor"]};box-shadow:0 0 6px {c["cor"]};margin-right:7px"></span>{c["categoria"]}</span>
                            <div class="bar"><i style="width:{max(c["pct"], 0) * 100:.0f}%;background:{c["cor"]};box-shadow:0 0 8px {c["cor"]}"></i></div>
                            <span class="mono r">{brl(c["valor"])}</span><span class="mono r dim">{c["pct"] * 100:.0f}%</span>
                            <span class="mono r dim">{brl(c["media_3m"]) if c["media_3m"] else "—"}</span><span class="r">{_variacao(c["valor"], c["mes_anterior"])}</span></div>''')
            with ui.element("div").classes("nf-panel"):
                ui.label("Para onde foi o dinheiro").classes("nf-h")
                tot = max(r["entradas"], r["saidas"], 1)
                fatias = [("Contas fixas", g["fixas"], "#FF2E88"), ("Parcelas", g["parcelas"], "#9D4EDD"),
                          ("Variáveis", g["variaveis"], "#FFE600"), ("Sobra", max(r["sobra"], 0), "#00F0FF")]
                ui.echart(echart_base(
                    tooltip={"trigger": "item", "backgroundColor": "#14141D", "borderColor": "#00F0FF", "textStyle": {"color": "#F4F6FF"}},
                    legend={"bottom": 0, "textStyle": {"color": "#C9CEDC"}},
                    series=[{"type": "pie", "radius": ["52%", "74%"], "center": ["50%", "45%"], "label": {"show": False},
                             "itemStyle": {"borderColor": "#12121A", "borderWidth": 3},
                             "data": [{"name": n, "value": v / 100, "itemStyle": {"color": c, "shadowBlur": 14, "shadowColor": c}}
                                      for n, v, c in fatias if v > 0]}])).classes("w-full").style("height:250px")
                html(f'<div class="dim" style="font-size:12px;text-align:center">{(r["saidas"] / tot * 100 if r["entradas"] else 0):.0f}% das entradas comprometidas</div>')

        with ui.element("div").classes("w-full").style("display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr) minmax(0,1fr);gap:14px;margin-top:14px"):
            with ui.element("div").classes("nf-panel"):
                ui.label("10 maiores saídas").classes("nf-h mb-1")
                for i, t in enumerate(r["top"], 1):
                    parc = f' <span class="pill p-pu">{t["parcela"]}</span>' if t["parcela"] else ""
                    html(f'''<div class="nf-row" style="grid-template-columns:22px 48px 1fr 110px;padding:5px 6px">
                        <span class="num dim" style="font-size:11px">{i}</span><span class="mono dim">{t["data"]:%d/%m}</span>
                        <span class="c-w">{t["descricao"]}{parc} <span class="dim" style="font-size:11px">· {t["categoria"]}</span></span>
                        <span class="mono r">{brl(t["valor"])}</span></div>''')
                if not r["top"]:
                    html('<div class="vazio">—</div>')
            with ui.element("div").classes("nf-panel"):
                ui.label("Por conta / cartão").classes("nf-h mb-2")
                maxc = max([v for _, v in r["por_conta"]] or [1])
                for nome, v in r["por_conta"]:
                    html(f'''<div style="margin-bottom:9px"><div style="display:flex;font-size:12.5px;margin-bottom:3px"><span class="c-w">{nome}</span>
                        <span class="mono" style="margin-left:auto">{brl(v)}</span></div>
                        <div class="bar"><i class="b-cy" style="width:{v / maxc * 100:.0f}%"></i></div></div>''')
            with ui.element("div").classes("nf-panel"):
                ui.label("Gastos do dia a dia por dia da semana").classes("nf-h")
                ui.echart(echart_base(
                    grid={"left": 46, "right": 8, "top": 14, "bottom": 24}, legend={"show": False},
                    xAxis={"type": "category", "data": [d for d, _ in r["por_dia_semana"]], "axisLine": {"lineStyle": {"color": "#2A2A3A"}}},
                    yAxis={"type": "value", "splitLine": {"lineStyle": {"color": "#1E1E2A"}}},
                    series=[{"type": "bar", "barWidth": "55%", "name": "Gasto",
                             "data": [v / 100 for _, v in r["por_dia_semana"]],
                             "itemStyle": {"color": "#9D4EDD", "borderRadius": [4, 4, 0, 0], "shadowBlur": 12, "shadowColor": "#9D4EDD"}}])) \
                    .classes("w-full").style("height:210px")

    # ======================================================================= ANUAL
    def anual():
        with sessao() as s:
            r = relatorio_periodo(s, selecionados, hoje)
        maior = r["maior_mes"]
        _kpis([("Entradas no período", brl(r["entradas"]), "g-gr", f'{r["meses_com_dados"]} de {len(selecionados)} mês(es) com lançamentos'),
               ("Saídas no período", brl(r["saidas"]), "g-mg", f'média {brl(r["media_saidas"])}/mês'),
               ("Sobra no período", brl(r["sobra"], sinal=True), "g-cy" if r["sobra"] >= 0 else "g-mg", "entradas − saídas"),
               ("Taxa de poupança", f'{r["taxa_poupanca"] * 100:.1f}%', "g-pu", rotulo),
               ("Mês mais caro", nome_mes(maior["comp"]) if maior else "—", "c-ye", brl(maior["saidas"]) if maior else "")])

        meses = r["meses"]
        estilo = lambda cor, m: {"color": cor, "opacity": .45 if m["futuro"] else 1, "shadowBlur": 10, "shadowColor": cor,
                                 "borderRadius": [3, 3, 0, 0]}
        opts = echart_base(
            xAxis={"type": "category", "data": [m["nome"] for m in meses], "axisLine": {"lineStyle": {"color": "#2A2A3A"}}},
            yAxis={"type": "value", "splitLine": {"lineStyle": {"color": "#1E1E2A"}}},
            series=[{"name": "Entradas", "type": "bar", "barWidth": "28%", "itemStyle": {"color": "#39FF14"},
                     "data": [{"value": m["entradas"] / 100, "itemStyle": estilo("#39FF14", m)} for m in meses]},
                    {"name": "Saídas", "type": "bar", "barWidth": "28%", "itemStyle": {"color": "#FF2E88"},
                     "data": [{"value": m["saidas"] / 100, "itemStyle": estilo("#FF2E88", m)} for m in meses]},
                    {"name": "Sobra", "type": "line", "smooth": True, "data": [m["sobra"] / 100 for m in meses],
                     "lineStyle": {"color": "#00F0FF", "width": 3, "shadowBlur": 14, "shadowColor": "#00F0FF"},
                     "itemStyle": {"color": "#00F0FF"}}])
        with ui.element("div").classes("nf-panel w-full").style("padding:12px 14px"):
            ui.label(f"Entradas, saídas e sobra · {rotulo}").classes("nf-h")
            ui.echart(opts).classes("w-full").style("height:260px")
            html('<div class="dim" style="font-size:11.5px">Meses apagados ainda não aconteceram: mostram só o que já está lançado ou previsto (fixas, parcelas e planejados). Para a estimativa completa de saldo, veja a <b>Visão anual</b>.</div>')

        n = len(meses)
        cols = f"140px repeat({n}, minmax(60px,1fr)) 84px"
        maxv = max([v for c in r["categorias"] for v in c["meses"]] or [1])
        with ui.element("div").classes("nf-panel w-full").style("padding:6px 6px 10px;margin-top:14px;overflow-x:auto"):
            ui.label("Saídas por categoria, mês a mês").classes("nf-h").style("padding:8px 8px 4px")
            with ui.element("div").style(f"min-width:{230 + n * 66}px"):
                html(f'<div class="nf-row head" style="grid-template-columns:{cols};column-gap:4px;padding:7px 8px"><span>Categoria</span>'
                     + "".join(f'<span class="r">{m["nome"]}</span>' for m in meses) + '<span class="r">Total</span></div>')
                if not r["categorias"]:
                    html('<div class="vazio">Sem saídas no período.</div>')
                for c in r["categorias"]:
                    cels = "".join(
                        f'<span class="mono r" style="display:block;font-size:12px;padding:3px 4px;border-radius:5px;'
                        f'background:{c["cor"]}{min(int(v / maxv * 90) + 8, 98):02d};{"" if v else "background:none;color:#3A3A4A"}">{fmt_curto(v) if v else "·"}</span>'
                        for v in c["meses"])
                    html(f'''<div class="nf-row" style="grid-template-columns:{cols};column-gap:4px;padding:5px 8px">
                        <span class="c-w"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{c["cor"]};margin-right:7px"></span>{c["categoria"]}</span>
                        {cels}<span class="mono r c-mg" style="display:block">{fmt_curto(c["total"])}</span></div>''')
                html(f'<div class="nf-row" style="grid-template-columns:{cols};column-gap:4px;padding:7px 8px;background:rgba(255,46,136,.05)"><b>Total</b>'
                     + "".join(f'<span class="mono r c-mg" style="display:block;font-size:12px">{fmt_curto(m["saidas"])}</span>' for m in meses)
                     + f'<span class="mono r g-mg" style="display:block">{fmt_curto(r["saidas"])}</span></div>')

    def exportar():
        with sessao() as s:
            if tipo == "anual":
                p = projecao(s, selecionados, hoje)
                ui.download.content(excel_periodo(s, selecionados, p["meses"]),
                                    f"neon_fin_relatorio_{selecionados[0]}_a_{selecionados[-1]}.xlsx")
            else:
                ui.download.content(excel_mensal(s, comp), f"neon_fin_relatorio_{comp}.xlsx")

    with moldura("/relatorios"):
        acoes = cabecalho("Relatórios", "Mensal: detalhe de um mês · Anual / período: os meses que você escolher, por mês e categoria")
        with acoes:
            ui.toggle({"mensal": "Mensal", "anual": "Anual / período"}, value=tipo,
                      on_change=lambda e: ui.navigate.to(f"/relatorios?tipo={e.value}&mes={comp}")) \
                .props("no-caps toggle-color=cyan dark unelevated dense")
            if tipo != "anual":
                seletor_mes("/relatorios", comp)
            ui.button("Exportar Excel", icon="download", on_click=exportar).props("unelevated no-caps").classes("btn-gho")
        if tipo == "anual":
            filtro_meses("/relatorios", de, ate, selecionados, extra="&tipo=anual")
            if erro:
                ui.notify(erro, type="negative", position="top")
            anual()
        else:
            mensal()
