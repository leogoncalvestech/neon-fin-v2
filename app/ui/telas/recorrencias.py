from datetime import date

from nicegui import ui
from sqlmodel import select

from app.dados.banco import sessao
from app.dados.modelos import Categoria, Conta, Lancamento, Recorrencia
from app.servicos.recorrencias import criar_recorrencia, editar_valor_recorrencia, encerrar_recorrencia
from app.servicos.util import brl, competencia, nome_mes, parse_valor
from app.ui.componentes import CAMPO, cabecalho, confirmar, html, moldura

COLS = "minmax(160px,1.4fr) 90px 130px 80px 150px 1fr 96px 90px"


@ui.page("/recorrencias")
def pagina_recorrencias():
    @ui.refreshable
    def lista():
        with sessao() as s:
            recs = list(s.exec(select(Recorrencia).order_by(Recorrencia.ativa.desc(), Recorrencia.tipo, Recorrencia.dia)))
            contas = {c.id: c.nome for c in s.exec(select(Conta))}
            cats = {c.id: c.nome for c in s.exec(select(Categoria))}
        ativas = [r for r in recs if r.ativa]
        ent = sum(r.valor_centavos for r in ativas if r.tipo == "entrada")
        sai = sum(r.valor_centavos for r in ativas if r.tipo == "saida")
        html(f'''<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:14px">
            <div class="nf-panel"><div class="nf-lbl">Receitas recorrentes / mês</div><div class="num g-gr" style="font-size:22px;margin-top:6px">{brl(ent)}</div></div>
            <div class="nf-panel"><div class="nf-lbl">Contas fixas / mês</div><div class="num g-mg" style="font-size:22px;margin-top:6px">{brl(sai)}</div>
              <div class="dim" style="font-size:12px">{(sai / ent * 100 if ent else 0):.1f}% das receitas recorrentes</div></div>
            <div class="nf-panel"><div class="nf-lbl">Sobra antes dos gastos</div><div class="num g-cy" style="font-size:22px;margin-top:6px">{brl(ent - sai)}</div></div></div>''')
        with ui.element("div").classes("nf-panel w-full").style("padding:6px 10px"):
            with ui.element("div").classes("nf-row head").style(f"grid-template-columns:{COLS}"):
                for h in ("Descrição", "Tipo", "Valor", "Dia", "Vigência", "Categoria · conta", "Status", ""):
                    ui.label(h).classes("r" if h == "Valor" else "")
            if not recs:
                html('<div class="vazio">Nenhuma recorrência. Cadastre salário, aluguel, internet...<br>Elas geram os lançamentos previstos dos próximos 12 meses.</div>')
            for r in recs:
                vig = f'{nome_mes(r.inicio)} → {nome_mes(r.fim) if r.fim else "sem fim"}'
                with ui.element("div").classes("nf-row").style(f"grid-template-columns:{COLS};{'' if r.ativa else 'opacity:.5'}"):
                    html(f'<span class="c-w">{r.descricao}</span>')
                    html(f'<span class="pill {"p-gr" if r.tipo == "entrada" else "p-mg"}">{"entrada" if r.tipo == "entrada" else "saída"}</span>')
                    html(f'<span class="mono r" style="display:block">{brl(r.valor_centavos)}</span>')
                    html(f'<span class="mono">dia {r.dia:02d}</span>')
                    html(f'<span class="dim">{vig}</span>')
                    html(f'<span class="dim">{cats[r.categoria_id]} · {contas[r.conta_id]}</span>')
                    html(f'<span class="pill {"p-cy" if r.ativa else "p-dim"}">{"ativa" if r.ativa else "encerrada"}</span>')
                    with ui.row().classes("gap-0 justify-end no-wrap"):
                        if r.ativa:
                            ui.button(icon="edit", on_click=lambda r=r: editar(r)).props("flat round dense").classes("btn-ico").tooltip("Alterar valor")
                            ui.button(icon="stop_circle", on_click=lambda r=r: encerrar(r)).props("flat round dense").classes("btn-ico").tooltip("Encerrar")

    def nova():
        with sessao() as s:
            cats = list(s.exec(select(Categoria).order_by(Categoria.nome)))
            contas = {c.id: ("💳 " if c.tipo == "cartao" else "") + c.nome
                      for c in s.exec(select(Conta).where(Conta.ativa == True)) if c.modo_fatura != "simples" or c.tipo != "cartao"}  # noqa: E712
        with ui.dialog() as d, ui.card().classes("nf-modal").style("width:520px;max-width:95vw"):
            ui.label("Nova recorrência").classes("nf-title").style("font-size:21px")
            tipo = ui.toggle({"saida": "Conta fixa (saída)", "entrada": "Receita (entrada)"}, value="saida").props("no-caps toggle-color=cyan dark unelevated")
            desc = ui.input("Descrição", placeholder="ex.: Aluguel").props(CAMPO + " autofocus").classes("w-full")
            with ui.row().classes("w-full no-wrap gap-2"):
                valor = ui.input("Valor (R$)").props(CAMPO).classes("flex-grow")
                dia = ui.number("Dia do vencimento", value=5, min=1, max=31, format="%d").props(CAMPO).style("width:170px")
            with ui.row().classes("w-full no-wrap gap-2"):
                ini = ui.input("Começa em", value=competencia(date.today())).props(CAMPO + " type=month").classes("flex-grow")
                fim = ui.input("Termina em (opcional)").props(CAMPO + " type=month clearable").classes("flex-grow")
            with ui.row().classes("w-full no-wrap gap-2"):
                cat = ui.select({}, label="Categoria").props(CAMPO).classes("flex-grow")
                conta = ui.select(contas, value=next(iter(contas)), label="Conta").props(CAMPO).classes("flex-grow")

            def filtra(*_):
                cat.options = {c.id: c.nome for c in cats if c.tipo == tipo.value}
                cat.value = next(iter(cat.options))
                cat.update()
            tipo.on_value_change(filtra)
            filtra()

            def salvar():
                try:
                    with sessao() as s:
                        criar_recorrencia(s, descricao=desc.value or "", tipo=tipo.value, valor_centavos=parse_valor(valor.value),
                                          dia=int(dia.value or 0), inicio=ini.value, fim=fim.value or None,
                                          categoria_id=cat.value, conta_id=conta.value)
                except ValueError as e:
                    ui.notify(str(e), type="negative", position="top")
                    return
                d.close()
                ui.notify("Recorrência criada — previstos gerados para 12 meses", type="positive", position="bottom-right")
                lista.refresh()
            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Cancelar", on_click=d.close).props("flat no-caps").classes("btn-gho")
                ui.button("Salvar", on_click=salvar).props("unelevated").classes("btn-pri")
        d.open()

    def editar(r: Recorrencia):
        with ui.dialog() as d, ui.card().classes("nf-modal").style("width:540px;max-width:95vw"):
            ui.label(f"Alterar valor · {r.descricao}").classes("nf-title").style("font-size:21px")
            html(f'<div class="dim">Valor atual <span class="mono c-w">{brl(r.valor_centavos)}</span></div>')
            with ui.row().classes("w-full no-wrap gap-2"):
                novo = ui.input("Novo valor (R$)").props(CAMPO + " autofocus").classes("flex-grow")
                ref = ui.input("A partir de / mês", value=competencia(date.today())).props(CAMPO + " type=month").style("width:200px")
            ui.label("Aplicar alteração em").classes("nf-lbl mt-1")
            escopo = ui.radio({"so_este": "Só neste mês", "daqui_em_diante": "Deste mês em diante",
                               "todos": "Todos os previstos (inclusive meses anteriores ainda não pagos)"},
                              value="daqui_em_diante").props("dark color=cyan")
            html('<div class="nf-impacto" style="margin-top:6px">Lançamentos já pagos nunca são alterados.</div>')

            def salvar():
                try:
                    with sessao() as s:
                        n = editar_valor_recorrencia(s, r.id, parse_valor(novo.value), escopo.value, ref.value)
                except ValueError as e:
                    ui.notify(str(e), type="negative", position="top")
                    return
                d.close()
                ui.notify(f"{n} lançamento(s) previsto(s) atualizados", type="positive", position="bottom-right")
                lista.refresh()
            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Cancelar", on_click=d.close).props("flat no-caps").classes("btn-gho")
                ui.button("Confirmar", on_click=salvar).props("unelevated").classes("btn-pri")
        d.open()

    def encerrar(r: Recorrencia):
        def ok():
            with sessao() as s:
                n = encerrar_recorrencia(s, r.id, date.today())
            ui.notify(f"{r.descricao} encerrada · {n} previsto(s) removido(s)", type="warning", position="bottom-right")
            lista.refresh()
        confirmar(f"Encerrar {r.descricao}?", "Os previstos a partir deste mês serão removidos. O que já foi pago continua no histórico.",
                  ok, "Encerrar", perigo=True)

    with moldura("/recorrencias"):
        acoes = cabecalho("Recorrências", "Contas fixas e receitas que se repetem todo mês")
        with acoes:
            ui.button("+ Nova recorrência", on_click=nova).props("unelevated").classes("btn-pri")
        lista()
