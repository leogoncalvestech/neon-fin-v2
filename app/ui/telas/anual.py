from datetime import date

from nicegui import ui

from app.dados.banco import sessao
from app.servicos.projecao import definir_estimativa, estimativa_do_mes, ler_config, projecao
from app.servicos.relatorios import excel_periodo
from app.servicos.util import brl, meses_entre, nome_mes, parse_valor, periodo_filtrado, somar_meses
from app.ui.componentes import (CAMPO, DialogoLancamento, atalho_novo, cabecalho, echart_base, filtro_meses, fmt_curto,
                                html, moldura)

TIPO_PILL = {"real": ("real", "p-gr"), "atual": ("atual", "p-cy"), "previsto": ("previsto", "p-dim")}


@ui.page("/anual")
def pagina_anual(de: str | None = None, ate: str | None = None, meses: str | None = None):
    hoje = date.today()
    de, ate = de or f"{hoje.year}-01", ate or f"{hoje.year}-12"
    erro = None
    try:
        selecionados = periodo_filtrado(de, ate, meses)
    except ValueError as e:
        erro, de, ate = str(e), f"{hoje.year}-01", f"{hoje.year}-12"
        selecionados = periodo_filtrado(de, ate)
    de, ate = min(de, ate), max(de, ate)

    def comps_do_periodo():
        return selecionados

    @ui.refreshable
    def tudo():
        comps = comps_do_periodo()
        with sessao() as s:
            p = projecao(s, comps, hoje)
            padrao = int(ler_config(s, "estimativa_padrao_centavos"))
        meses, r = p["meses"], p["resumo"]

        # ------------------------------------------------ KPIs
        menor = r["menor_saldo"]
        sem_saldo = r["saldo_inicial"] == 0
        html(f'''<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:14px">
          <div class="nf-panel"><div class="nf-lbl">Saldo inicial · {nome_mes(r["mes_base"])}</div>
            <div class="num c-w" style="font-size:22px;margin-top:6px">{brl(r["saldo_inicial"])}</div>
            <div class="dim" style="font-size:12px">{"defina em Configurações" if sem_saldo else "início do mês, antes do salário"}</div></div>
          <div class="nf-panel"><div class="nf-lbl">Sobra no período</div>
            <div class="num {'g-gr' if r['sobra'] >= 0 else 'g-mg'}" style="font-size:22px;margin-top:6px">{brl(r["sobra"], sinal=True)}</div>
            <div class="dim" style="font-size:12px">entradas {brl(r["entradas"])} · saídas {brl(r["saidas"])}</div></div>
          <div class="nf-panel nf-hero"><div class="nf-lbl c-cy">◆ Saldo previsto em {nome_mes(comps[-1])}</div>
            <div class="num {'g-cy' if (r['saldo_final'] or 0) >= 0 else 'g-mg'}" style="font-size:26px;margin-top:6px">{brl(r["saldo_final"]) if r["saldo_final"] is not None else "—"}</div>
            <div class="dim" style="font-size:12px">fim do último mês do período</div></div>
          <div class="nf-panel {'mg' if menor and menor['saldo_fim'] < 0 else ''}"><div class="nf-lbl">Menor saldo</div>
            <div class="num {'g-mg' if menor and menor['saldo_fim'] < 0 else 'c-w'}" style="font-size:22px;margin-top:6px">{brl(menor["saldo_fim"]) if menor else "—"}</div>
            <div class="dim" style="font-size:12px">{("em " + nome_mes(menor["comp"])) if menor else ""}{" · " + str(len(r["meses_negativos"])) + " mês(es) com sobra negativa" if r["meses_negativos"] else ""}</div></div>
        </div>''')

        # ------------------------------------------------ gráfico
        cores_fut = lambda cor, m: {"color": cor, "opacity": 1 if m["tipo"] != "previsto" else .45,
                                    "shadowBlur": 10, "shadowColor": cor, "borderRadius": [3, 3, 0, 0]}
        opts = echart_base(
            xAxis={"type": "category", "data": [nome_mes(m["comp"]) for m in meses],
                   "axisLine": {"lineStyle": {"color": "#2A2A3A"}}},
            yAxis={"type": "value", "splitLine": {"lineStyle": {"color": "#1E1E2A"}},
                   "axisLabel": {"formatter": "{value}"}},
            series=[
                {"name": "Entradas", "type": "bar", "barGap": "10%", "barWidth": "28%", "itemStyle": {"color": "#39FF14"},
                 "data": [{"value": m["entradas"] / 100, "itemStyle": cores_fut("#39FF14", m)} for m in meses]},
                {"name": "Saídas", "type": "bar", "barWidth": "28%", "itemStyle": {"color": "#FF2E88"},
                 "data": [{"value": m["saidas"] / 100, "itemStyle": cores_fut("#FF2E88", m)} for m in meses]},
                {"name": "Saldo no fim do mês", "type": "line", "smooth": True, "symbolSize": 7,
                 "data": [None if m["saldo_fim"] is None else m["saldo_fim"] / 100 for m in meses],
                 "lineStyle": {"color": "#00F0FF", "width": 3, "shadowBlur": 14, "shadowColor": "#00F0FF"},
                 "itemStyle": {"color": "#00F0FF"}},
            ])
        opts["legend"]["data"] = ["Entradas", "Saídas", "Saldo no fim do mês"]
        opts["legend"]["textStyle"] = {"color": "#C9CEDC"}
        with ui.element("div").classes("nf-panel w-full").style("padding:12px 14px"):
            ui.label("Entradas, saídas e saldo mês a mês").classes("nf-h")
            ui.echart(opts).classes("w-full").style("height:270px")
            html('<div class="dim" style="font-size:11.5px">Barras apagadas = meses previstos. Clique num mês na tabela para ver ou anexar lançamentos.</div>')

        # ------------------------------------------------ tabela
        n = len(meses)
        cols = f"150px repeat({n}, minmax(66px, 1fr))"
        largura_min = 160 + n * 72

        def linha(rotulo, valores, cls="", estilo="", dica=""):
            html(f'''<div class="nf-row" style="grid-template-columns:{cols};padding:6px 8px;column-gap:6px;{estilo}">
                <span class="{cls}" title="{dica}">{rotulo}</span>{"".join(valores)}</div>''')

        def cel(texto, cls="", extra=""):
            return f'<span class="mono r {cls}" style="display:block;font-size:12.5px;{extra}">{texto}</span>'

        with ui.element("div").classes("nf-panel w-full").style("padding:6px 6px 10px;margin-top:14px;overflow-x:auto"):
            with ui.element("div").style(f"min-width:{largura_min}px"):
                with ui.element("div").classes("nf-row head").style(f"grid-template-columns:{cols};padding:8px;column-gap:6px"):
                    ui.label("Mês")
                    for m in meses:
                        t, pc = TIPO_PILL[m["tipo"]]
                        ui.html(f'<div class="r" style="cursor:pointer"><div class="c-cy">{nome_mes(m["comp"])}</div>'
                                f'<span class="pill {pc}" style="font-size:9.5px;padding:0 6px">{t}</span></div>', sanitize=False) \
                            .on("click", lambda m=m: ui.navigate.to(f'/lancamentos?mes={m["comp"]}')).tooltip("Ver lançamentos do mês")
                linha("Entradas", [cel(fmt_curto(m["entradas"]), "c-gr") for m in meses], "c-gr")
                linha("− Contas fixas", [cel(fmt_curto(m["fixas"])) for m in meses])
                linha("− Parcelas", [cel(fmt_curto(m["parcelas"])) for m in meses])
                # variáveis: editável nos meses atual/futuros
                with ui.element("div").classes("nf-row").style(f"grid-template-columns:{cols};padding:6px 8px;column-gap:6px"):
                    html('<span title="Dia a dia: mercado, gasolina, lazer... Clique no valor para estimar">− Variáveis <span class="c-ye" style="font-size:11px">✎ estimar</span></span>')
                    for m in meses:
                        if m["tipo"] == "real":
                            html(cel(fmt_curto(m["variaveis"])))
                        else:
                            marca = {"mes": "✎", "padrao": "", "nenhuma": ""}[m["origem_estimativa"]]
                            usou_lancado = m["var_lancados"] > m["estimativa"]
                            cor = "c-ye" if m["origem_estimativa"] == "mes" else ""
                            ui.html(cel(f'{marca} {fmt_curto(m["variaveis"])}', cor, "cursor:pointer;text-decoration:underline dotted #2A2A3A"),
                                    sanitize=False).on("click", lambda m=m: editar_estimativa(m)) \
                                .tooltip(f'Estimativa {brl(m["estimativa"])} ({m["origem_estimativa"]}) · lançado {brl(m["var_lancados"])}'
                                         + (" → vale o lançado" if usou_lancado else "")
                                         + (f' · planejados {brl(m["var_planejados"])}' if m["var_planejados"] else ""))
                linha('<span class="dim">&nbsp;&nbsp;dos quais planejados</span>',
                      [cel(fmt_curto(m["var_planejados"]) if m["var_planejados"] else "", "dim") for m in meses])
                linha("<b>= Total de saídas</b>", [cel(fmt_curto(m["saidas"]), "c-mg") for m in meses])
                linha("<b>Sobra do mês</b>", [cel(fmt_curto(m["sobra"]), "g-gr" if m["sobra"] >= 0 else "g-mg", "font-weight:600") for m in meses],
                      estilo="background:rgba(255,255,255,.02)")
                linha('<b class="c-cy">Saldo no fim do mês</b>',
                      [cel(fmt_curto(m["saldo_fim"]), "g-cy" if (m["saldo_fim"] or 0) >= 0 else "g-mg", "font-weight:600;font-size:13.5px") for m in meses],
                      estilo="background:rgba(0,240,255,.05)")

        html(f'''<div class="nf-panel" style="margin-top:14px;font-size:12.5px;line-height:1.75">
          <b class="c-cy">Como a projeção funciona</b><br>
          • Saldo considera quando o dinheiro <b>sai da conta</b>: compra no cartão entra no mês da fatura.<br>
          • Meses passados usam o que foi <b>lançado</b>. Mês atual e futuros: <b>o maior entre o lançado e a sua estimativa</b>, mais os gastos <b>planejados</b>
            (lançamentos com data futura, ex.: IPVA, viagem).<br>
          • Estimativa padrão: <b class="c-ye">{brl(padrao) if padrao else "não definida"}</b> (Configurações). Clique na célula de gastos variáveis para definir um valor só para aquele mês (aparece com ✎).<br>
          • A reserva para metas <b>não</b> é descontada do saldo: ela continua sendo seu dinheiro (só não entra no "livre para gastar").<br>
          • Valores em reais, sem centavos. Contas fixas futuras vêm das Recorrências.</div>''')

    def editar_estimativa(m):
        with sessao() as s:
            atual, origem = estimativa_do_mes(s, m["comp"])
        with ui.dialog() as d, ui.card().classes("nf-modal").style("width:480px;max-width:95vw"):
            ui.label(f"Gastos variáveis · {nome_mes(m['comp'], longo=True)}").classes("nf-title").style("font-size:20px")
            html(f'''<div class="dim" style="font-size:12.5px;line-height:1.7">Já lançado no mês: <span class="mono c-w">{brl(m["var_lancados"])}</span><br>
                Planejados (somam por cima): <span class="mono c-w">{brl(m["var_planejados"])}</span><br>
                Estimativa atual: <span class="mono c-ye">{brl(atual)}</span> ({ {"mes": "definida para este mês", "padrao": "valor padrão", "nenhuma": "nenhuma"}[origem] })</div>''')
            valor = ui.input("Quanto você estima gastar no dia a dia (R$)",
                             value=f"{atual / 100:.2f}".replace(".", ",") if atual else "").props(CAMPO + " autofocus").classes("w-full mt-2")
            ate = ui.select({c: nome_mes(c) for c in meses_entre(m["comp"], somar_meses(m["comp"], 11))},
                            value=m["comp"], label="Aplicar até").props(CAMPO).classes("w-full")

            def salvar():
                try:
                    v = parse_valor(valor.value)
                    with sessao() as s:
                        for c in meses_entre(m["comp"], ate.value):
                            definir_estimativa(s, c, v)
                except ValueError as e:
                    ui.notify(str(e), type="negative", position="top")
                    return
                d.close()
                ui.notify(f"Estimativa {brl(v)} aplicada de {nome_mes(m['comp'])} a {nome_mes(ate.value)}", type="positive", position="bottom-right")
                tudo.refresh()

            def usar_padrao():
                with sessao() as s:
                    definir_estimativa(s, m["comp"], None)
                d.close()
                ui.notify(f"{nome_mes(m['comp'])} voltou a usar a estimativa padrão", type="positive", position="bottom-right")
                tudo.refresh()
            valor.on("keydown.enter", salvar)
            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Usar padrão", on_click=usar_padrao).props("flat no-caps").classes("btn-gho")
                ui.button("Salvar", on_click=salvar).props("unelevated").classes("btn-pri")
        d.open()

    def exportar():
        comps = comps_do_periodo()
        with sessao() as s:
            p = projecao(s, comps, hoje)
            dados = excel_periodo(s, comps, p["meses"])
        ui.download.content(dados, f"neon_fin_visao_{comps[0]}_a_{comps[-1]}.xlsx")

    with moldura("/anual"):
        dialogo = DialogoLancamento(ao_salvar=lambda: tudo.refresh())
        atalho_novo(dialogo)
        sub = f"Saldo previsto mês a mês · {nome_mes(selecionados[0])} a {nome_mes(selecionados[-1])}"
        acoes = cabecalho("Visão anual", sub)
        with acoes:
            ui.button("Excel", icon="download", on_click=exportar).props("unelevated no-caps").classes("btn-gho")
            ui.button("+ Gasto planejado", on_click=lambda: dialogo.abrir()).props("unelevated").classes("btn-pri") \
                .tooltip("Lance com data futura: vira 'planejado' e entra na projeção")
        filtro_meses("/anual", de, ate, selecionados)
        if erro:
            ui.notify(erro, type="negative", position="top")
        tudo()
