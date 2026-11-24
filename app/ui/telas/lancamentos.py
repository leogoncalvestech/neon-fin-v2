from datetime import date

from nicegui import ui
from sqlmodel import select

from app.dados.banco import sessao
from app.dados.modelos import Categoria, Conta, Lancamento
from app.servicos.lancamentos import excluir_lancamento, lancamentos_do_mes, marcar_pago
from app.servicos.util import brl, competencia
from app.ui.componentes import CAMPO, DialogoLancamento, atalho_novo, cabecalho, confirmar, html, moldura, seletor_mes

ORIGEM = {"recorrencia": ("fixa", "p-cy"), "fatura_total": ("fatura", "p-pu"), "ajuste_fatura": ("ajuste", "p-pu")}
COLS = "64px minmax(180px,1.6fr) 1fr 1fr 130px 84px 110px"


@ui.page("/lancamentos")
def pagina_lancamentos(mes: str | None = None):
    comp = mes or competencia(date.today())
    filtro = {"tipo": "todos", "texto": ""}

    @ui.refreshable
    def lista():
        with sessao() as s:
            lancs = lancamentos_do_mes(s, comp)
            cats = {c.id: c for c in s.exec(select(Categoria))}
            contas = {c.id: c for c in s.exec(select(Conta))}
        t = filtro["texto"].lower().strip()
        vis = [l for l in lancs if (filtro["tipo"] == "todos" or l.tipo == filtro["tipo"])
               and (not t or t in l.descricao.lower() or t in cats[l.categoria_id].nome.lower())]
        ent = sum(l.valor_centavos for l in vis if l.tipo == "entrada")
        sai = sum(l.valor_centavos for l in vis if l.tipo == "saida")
        html(f'<div style="font-size:13px;margin:0 0 10px 2px">{len(vis)} lançamentos · Entradas <b class="c-gr mono">{brl(ent)}</b>'
             f' &nbsp;·&nbsp; Saídas <b class="c-mg mono">{brl(sai)}</b></div>')
        with ui.element("div").classes("nf-panel w-full").style("padding:6px 10px"):
            with ui.element("div").classes("nf-row head").style(f"grid-template-columns:{COLS}"):
                for h in ("Data", "Descrição", "Categoria", "Conta / cartão", "Valor", "Status", ""):
                    ui.label(h).classes("r" if h == "Valor" else "")
            if not vis:
                html('<div class="vazio">Nenhum lançamento neste mês.<br>Aperte <kbd>N</kbd> ou clique em <b>+ Lançar</b>.</div>')
            for l in vis:
                c = cats[l.categoria_id]
                conta = contas[l.conta_id]
                tags = ""
                if l.parcela_total:
                    tags += f' <span class="pill p-pu">{l.parcela_n}/{l.parcela_total}</span>'
                if l.origem in ORIGEM:
                    tags += f' <span class="pill {ORIGEM[l.origem][1]}">{ORIGEM[l.origem][0]}</span>'
                cor = "c-gr" if l.tipo == "entrada" else "c-mg"
                status = '<span class="pill p-gr">pago</span>' if l.status == "pago" else '<span class="pill p-dim">previsto</span>'
                with ui.element("div").classes("nf-row").style(f"grid-template-columns:{COLS}"):
                    html(f'<span class="mono dim">{l.data:%d/%m}</span>')
                    html(f'<span class="c-w">{l.descricao}</span>{tags}')
                    html(f'<span><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:{c.cor};margin-right:6px"></span>{c.nome}</span>')
                    html(f'<span class="dim">{"💳 " if conta.tipo == "cartao" else ""}{conta.nome}</span>')
                    html(f'<span class="mono {cor} r" style="display:block">{brl(l.valor_centavos if l.tipo == "entrada" else -l.valor_centavos, sinal=True)}</span>')
                    html(status)
                    with ui.row().classes("gap-0 justify-end no-wrap"):
                        if l.status == "previsto":
                            ui.button(icon="check", on_click=lambda l=l: pagar(l)).props("flat round dense").classes("btn-ico").tooltip("Marcar como pago")
                        if l.origem in ("fatura_total", "ajuste_fatura"):
                            ui.button(icon="credit_card", on_click=lambda l=l: ui.navigate.to(f"/cartoes?id={l.conta_id}")) \
                                .props("flat round dense").classes("btn-ico").tooltip("Abrir fatura")
                        else:
                            ui.button(icon="edit", on_click=lambda l=l: dialogo.abrir(l)).props("flat round dense").classes("btn-ico").tooltip("Editar")
                            ui.button(icon="delete_outline", on_click=lambda l=l: excluir(l)).props("flat round dense").classes("btn-ico").tooltip("Excluir")

    def pagar(l):
        with sessao() as s:
            marcar_pago(s, l.id)
        ui.notify(f"{l.descricao} marcado como pago", type="positive", position="bottom-right")
        lista.refresh()

    def excluir(l):
        texto = (f"Esta é a parcela {l.parcela_n}/{l.parcela_total}. A compra inteira ({l.parcela_total} parcelas) será apagada."
                 if l.compra_id else f"{l.descricao} · {brl(l.valor_centavos)} será apagado.")

        def ok():
            with sessao() as s:
                try:
                    n = excluir_lancamento(s, l.id)
                except ValueError as e:
                    ui.notify(str(e), type="negative")
                    return
            ui.notify(f"{n} lançamento(s) excluído(s)", type="warning", position="bottom-right")
            lista.refresh()
        if l.recorrencia_id:
            texto += " Ele volta a ser gerado se a recorrência continuar ativa — para parar, encerre em Recorrências."
        confirmar("Excluir lançamento?", texto, ok, "Excluir", perigo=True)

    with moldura("/lancamentos"):
        dialogo = DialogoLancamento(ao_salvar=lambda: lista.refresh())
        atalho_novo(dialogo)
        acoes = cabecalho("Lançamentos", "Tudo que entrou e saiu no mês · compras no cartão aparecem no mês em que a fatura vence")
        with acoes:
            seletor_mes("/lancamentos", comp)
            ui.button("+ Lançar", on_click=lambda: dialogo.abrir()).props("unelevated").classes("btn-pri")
        with ui.row().classes("items-center gap-3 mb-3"):
            def set_tipo(e):
                filtro["tipo"] = e.value
                lista.refresh()

            def set_texto(e):
                filtro["texto"] = e.value or ""
                lista.refresh()
            ui.toggle({"todos": "Todos", "saida": "Saídas", "entrada": "Entradas"}, value="todos", on_change=set_tipo) \
                .props("no-caps toggle-color=cyan dark unelevated dense")
            ui.input(placeholder="Buscar descrição ou categoria...", on_change=set_texto).props(CAMPO + " clearable") \
                .style("width:300px")
        lista()
