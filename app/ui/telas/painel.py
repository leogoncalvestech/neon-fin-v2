from datetime import date

from nicegui import ui
from sqlmodel import select

from app.dados.banco import sessao
from app.dados.modelos import Categoria
from app.servicos.cartoes import marcar_fatura_paga
from app.servicos.lancamentos import marcar_pago
from app.servicos.livre import proximos_vencimentos, resumo_mes
from app.servicos.util import brl, competencia, nome_mes
from app.ui.componentes import DialogoLancamento, atalho_novo, barra, cabecalho, confirmar, html, moldura, seletor_mes


@ui.page("/")
def pagina_painel(mes: str | None = None):
    comp = mes or competencia(date.today())

    @ui.refreshable
    def conteudo():
        hoje = date.today()
        with sessao() as s:
            r = resumo_mes(s, comp, hoje)
            cats = {c.id: c for c in s.exec(select(Categoria))}
            venc = proximos_vencimentos(s, hoje)

        # ---------------- KPIs
        livre_cls = "g-cy" if r["livre"] >= 0 else "g-mg"
        rotulo = "Livre para gastar" if r["livre"] >= 0 else "Acima do limite"
        comprometido = r["fixas"] + r["parcelas"] + r["metas"]
        orcado = max(r["disponivel"], 1)
        with ui.element("div").classes("w-full").style("display:grid;grid-template-columns:1.45fr 1fr 1fr 1fr;gap:14px"):
            html(f'''<div class="nf-panel nf-hero" style="height:100%">
                <div class="nf-lbl c-cy">◆ {rotulo} · {nome_mes(comp)}</div>
                <div class="num {livre_cls}" style="font-size:34px;margin:6px 0 4px">{brl(r["livre"])}</div>
                <div style="display:flex;gap:22px">
                  <div><div class="dim" style="font-size:11.5px">por dia</div><div class="num g-cy" style="font-size:14px">{brl(r["por_dia"])}</div></div>
                  <div><div class="dim" style="font-size:11.5px">nesta semana</div><div class="num g-cy" style="font-size:14px">{brl(r["semana"])}</div></div>
                  <div><div class="dim" style="font-size:11.5px">dias restantes</div><div class="num c-w" style="font-size:14px">{r["dias_restantes"]}</div></div>
                </div></div>''')
            html(f'''<div class="nf-panel" style="height:100%"><div class="nf-lbl">Entradas</div>
                <div class="num g-gr" style="font-size:22px;margin:6px 0 2px">{brl(r["entradas"])}</div>
                <div class="dim" style="font-size:12px">salário, freelas e outras receitas do mês</div></div>''')
            html(f'''<div class="nf-panel" style="height:100%"><div class="nf-lbl">Comprometido</div>
                <div class="num g-mg" style="font-size:22px;margin:6px 0 2px">{brl(comprometido)}</div>
                <div class="dim" style="font-size:12px">fixas {brl(r["fixas"])} · parcelas {brl(r["parcelas"])} · metas {brl(r["metas"])}</div></div>''')
            html(f'''<div class="nf-panel" style="height:100%"><div class="nf-lbl">Gasto variável</div>
                <div class="num c-w" style="font-size:22px;margin:6px 0 8px">{brl(r["variaveis"])}</div>
                {barra(r["variaveis"] / orcado)}
                <div class="dim" style="font-size:12px;margin-top:4px">{r["variaveis"] / orcado * 100:.0f}% de {brl(r["disponivel"])} disponíveis</div></div>''')

        if r["estouro_no_ritmo"] > 0 and comp == competencia(hoje):
            html(f'''<div class="nf-panel ye" style="margin-top:14px;padding:11px 16px;font-size:13.5px">
                <span class="pill p-ye">ritmo</span>&nbsp; Você está gastando em média <b class="c-w">{brl(r["media_diaria"])}/dia</b>.
                Nesse ritmo, ultrapassa o livre em <b class="g-mg">{brl(r["estouro_no_ritmo"])}</b> até o fim do mês.</div>''')

        with ui.element("div").classes("w-full").style("display:grid;grid-template-columns:1.35fr 1fr;gap:14px;margin-top:14px"):
            # ---------------- vencimentos
            with ui.element("div").classes("nf-panel"):
                ui.label("Próximos vencimentos").classes("nf-h mb-2")
                if not venc:
                    html('<div class="vazio">Nada a pagar por enquanto. Cadastre contas fixas em <b>Recorrências</b>.</div>')
                for v in venc:
                    atrasado = v["data"] < hoje
                    dias = (v["data"] - hoje).days
                    pill = ('<span class="pill p-ye">a receber</span>' if atrasado and v["tipo"] == "entrada" else
                            '<span class="pill p-mg">atrasado</span>' if atrasado else
                            '<span class="pill p-ye">hoje</span>' if dias == 0 else
                            '<span class="pill p-ye">amanhã</span>' if dias == 1 else
                            f'<span class="pill p-dim">em {dias} dias</span>')
                    cor = "c-gr" if v["tipo"] == "entrada" else "c-mg"
                    with ui.element("div").classes("nf-row w-full").style("grid-template-columns:62px 1fr 96px 130px 44px"):
                        html(f'<span class="mono dim">{v["data"]:%d/%m}</span>')
                        html(f'<span class="c-w">{"💳 " if v["fatura_id"] else ""}{v["descricao"]}</span>')
                        html(pill)
                        html(f'<span class="mono {cor} r" style="display:block">{brl(v["valor"])}</span>')
                        ui.button(icon="check", on_click=lambda v=v: pagar(v)).props("flat round dense") \
                            .classes("btn-ico").tooltip("Marcar como pago/recebido")
            # ---------------- categorias
            with ui.element("div").classes("nf-panel"):
                ui.label(f"Saídas por categoria · {nome_mes(comp)}").classes("nf-h mb-3")
                itens = sorted(r["por_categoria"].items(), key=lambda x: -x[1])[:7]
                if not itens:
                    html('<div class="vazio">Sem saídas neste mês ainda.<br>Aperte <kbd>N</kbd> para lançar.</div>')
                total = max(sum(v for _, v in itens), 1)
                for cid, v in itens:
                    c = cats.get(cid)
                    html(f'''<div style="margin-bottom:10px"><div style="display:flex;font-size:12.5px;margin-bottom:4px">
                        <span class="c-w"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{c.cor};box-shadow:0 0 6px {c.cor};margin-right:7px"></span>{c.nome}</span>
                        <span class="mono" style="margin-left:auto">{brl(v)}</span></div>
                        <div class="bar"><i style="width:{v / total * 100:.0f}%;background:{c.cor};box-shadow:0 0 8px {c.cor}"></i></div></div>''')

        with ui.expansion("Como calculamos o livre para gastar").classes("w-full nf-panel").style("margin-top:14px;padding:4px 10px"):
            html(f'''<div class="mono" style="font-size:13px;line-height:1.9;padding:4px 6px 10px">
                Entradas do mês <span class="c-gr" style="float:right">{brl(r["entradas"])}</span><br>
                − Contas fixas <span class="c-mg" style="float:right">{brl(r["fixas"])}</span><br>
                − Parcelas de cartão <span class="c-mg" style="float:right">{brl(r["parcelas"])}</span><br>
                − Reserva para metas <span class="c-mg" style="float:right">{brl(r["metas"])}</span><br>
                = Disponível <span class="c-w" style="float:right">{brl(r["disponivel"])}</span><br>
                − Gastos variáveis <span class="c-mg" style="float:right">{brl(r["variaveis"])}</span><br>
                <b class="c-cy">= Livre para gastar</b> <b class="g-cy" style="float:right">{brl(r["livre"])}</b><br>
                <span class="dim">Por dia = livre ÷ {r["dias_restantes"]} dias restantes (inclui hoje). Semana = por dia × dias até domingo.
                Compras e parcelas no cartão contam no mês em que a fatura vence (quando o dinheiro sai).</span></div>''')

    def pagar(v):
        def ok():
            with sessao() as s:
                if v["fatura_id"]:
                    marcar_fatura_paga(s, v["fatura_id"])
                else:
                    marcar_pago(s, v["lancamento_id"])
            ui.notify(f'{v["descricao"]} marcado como pago', type="positive", position="bottom-right", icon="check")
            conteudo.refresh()
        confirmar("Confirmar pagamento", f'{v["descricao"]} · {brl(v["valor"])} · venc. {v["data"]:%d/%m/%Y}', ok, "Marcar como pago")

    with moldura("/"):
        dialogo = DialogoLancamento(ao_salvar=lambda: conteudo.refresh())
        atalho_novo(dialogo)
        acoes = cabecalho("Painel", f"{date.today():%d/%m/%Y}")
        with acoes:
            seletor_mes("/", comp)
            ui.button("+ Lançar", on_click=lambda: dialogo.abrir()).props("unelevated").classes("btn-pri")
        conteudo()
