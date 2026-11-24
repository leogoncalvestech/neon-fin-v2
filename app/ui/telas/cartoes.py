from datetime import date

from nicegui import ui
from sqlmodel import select

from app.dados.banco import sessao
from app.dados.modelos import Categoria, Conta, Fatura
from app.servicos.cartoes import (arquivar_cartao, datas_da_fatura, faturas_do_cartao, informar_valor_fatura,
                                  limite_usado, marcar_fatura_paga, obter_fatura, resumo_fatura, salvar_cartao,
                                  status_fatura, vencimento_da_fatura)
from app.servicos.util import brl, competencia, nome_mes, parse_valor, somar_meses
from app.ui.componentes import CAMPO, DialogoLancamento, atalho_novo, barra, cabecalho, confirmar, html, moldura

CORES = {"#9D4EDD": "Roxo", "#00B1EA": "Azul", "#FF2E88": "Magenta", "#39FF14": "Verde", "#FF9F1C": "Laranja",
         "#00F0FF": "Ciano", "#FFE600": "Amarelo", "#7C8299": "Grafite"}
MODOS = {"hibrido": "Híbrido", "detalhado": "Detalhado", "simples": "Simples"}
MODO_TXT = {"hibrido": "Lança as compras e, no fechamento, digita o valor real. A diferença vira ajuste.",
            "detalhado": "Lança cada compra; a fatura é a soma das compras e parcelas.",
            "simples": "Não lança compras; só digita o total da fatura de cada mês."}
STATUS_PILL = {"aberta": "p-cy", "fechada": "p-mg", "paga": "p-gr", "atrasada": "p-mg"}


def _escurecer(hex_cor: str) -> str:
    r, g, b = (int(hex_cor[i:i + 2], 16) for i in (1, 3, 5))
    return f"#{r // 3:02x}{g // 3:02x}{b // 3:02x}"


@ui.page("/cartoes")
def pagina_cartoes(id: int | None = None, fat: str | None = None):
    estado = {"id": id, "fat": fat}

    # ------------------------------------------------------------ cadastro
    def abrir_form(conta: Conta | None = None):
        with ui.dialog() as d, ui.card().classes("nf-modal").style("width:520px;max-width:95vw"):
            ui.label("Editar cartão" if conta else "Novo cartão").classes("nf-title").style("font-size:21px")
            nome = ui.input("Nome do cartão", value=conta.nome if conta else "").props(CAMPO + " autofocus").classes("w-full")
            with ui.row().classes("w-full no-wrap gap-2"):
                cor = ui.select(CORES, value=conta.cor if conta and conta.cor in CORES else "#9D4EDD", label="Cor").props(CAMPO).classes("flex-grow")
                final = ui.input("Últimos 4 dígitos", value=conta.final if conta else "").props(CAMPO + " maxlength=4").style("width:150px")
            with ui.row().classes("w-full no-wrap gap-2"):
                limite = ui.input("Limite (R$)", value=f"{conta.limite_centavos / 100:.2f}".replace(".", ",") if conta else "").props(CAMPO).classes("flex-grow")
                fech = ui.number("Dia do fechamento", value=conta.dia_fechamento if conta else None, min=1, max=31, format="%d").props(CAMPO).style("width:150px")
                venc = ui.number("Dia do vencimento", value=conta.dia_vencimento if conta else None, min=1, max=31, format="%d").props(CAMPO).style("width:150px")
            ui.label("Como você vai informar as faturas?").classes("nf-lbl mt-2")
            modo = ui.toggle(MODOS, value=conta.modo_fatura if conta else "hibrido").props("no-caps toggle-color=cyan dark unelevated")
            explica = ui.label(MODO_TXT[modo.value]).classes("dim text-sm")
            modo.on_value_change(lambda e: explica.set_text(MODO_TXT[e.value]))
            if conta:
                ui.label("Mudar os dias vale para as próximas compras; o que já foi lançado continua na fatura atual.").classes("dim text-xs")

            def salvar():
                try:
                    lim = parse_valor(limite.value) if (limite.value or "").strip() else 0
                    with sessao() as s:
                        c = salvar_cartao(s, nome=nome.value, dia_fechamento=fech.value, dia_vencimento=venc.value,
                                          limite_centavos=lim, cor=cor.value, final=final.value or "",
                                          modo_fatura=modo.value, conta_id=conta.id if conta else None)
                except (ValueError, TypeError) as e:
                    ui.notify(str(e) if isinstance(e, ValueError) else "Preencha os dias de fechamento e vencimento.", type="negative", position="top")
                    return
                d.close()
                ui.notify(f"Cartão {c.nome} salvo", type="positive", position="bottom-right")
                estado["id"] = c.id
                tudo.refresh()
            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Cancelar", on_click=d.close).props("flat no-caps").classes("btn-gho")
                ui.button("Salvar", on_click=salvar).props("unelevated").classes("btn-pri")
        d.open()

    # ------------------------------------------------------------ tela
    @ui.refreshable
    def tudo():
        hoje = date.today()
        with sessao() as s:
            cartoes = list(s.exec(select(Conta).where(Conta.tipo == "cartao", Conta.ativa == True)))  # noqa: E712
            info = {}
            for c in cartoes:
                fats = faturas_do_cartao(s, c.id)
                atual_comp = competencia(vencimento_da_fatura(hoje, c.dia_fechamento, c.dia_vencimento))
                pend = [f for f in fats if not f.paga and status_fatura(f, c, hoje) in ("fechada", "atrasada")]
                foco = pend[0] if pend else next((f for f in fats if f.competencia == atual_comp), None)
                info[c.id] = {"usado": limite_usado(s, c), "foco": foco,
                              "foco_valor": resumo_fatura(s, foco)["valor"] if foco else 0,
                              "foco_status": status_fatura(foco, c, hoje) if foco else "aberta",
                              "foco_comp": foco.competencia if foco else atual_comp}

        if not cartoes:
            html('<div class="nf-panel vazio" style="padding:50px">Nenhum cartão cadastrado.<br><br>Clique em <b class="c-cy">+ Cartão</b> para começar.</div>')
            return
        if estado["id"] not in [c.id for c in cartoes]:
            estado["id"] = cartoes[0].id

        with ui.element("div").classes("w-full").style("display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:14px"):
            for c in cartoes:
                i = info[c.id]
                st = i["foco_status"]
                pct = i["usado"] / c.limite_centavos if c.limite_centavos else 0
                with ui.element("div").classes("cc" + (" sel" if c.id == estado["id"] else "")).on("click", lambda c=c: selecionar(c.id)):
                    html(f'''<div class="cc-top" style="background:linear-gradient(135deg,{c.cor},{_escurecer(c.cor)} 70%,#0A0A0F)">
                        <div class="cc-nome">{c.nome}</div><div class="cc-chip"></div>
                        <div class="cc-num">•••• •••• •••• {c.final or "····"}</div></div>
                      <div style="padding:12px 16px">
                        <div style="display:flex;align-items:center"><span class="nf-lbl">Fatura {nome_mes(i["foco_comp"])}</span>
                          <span class="pill {STATUS_PILL[st]}" style="margin-left:auto">{st}</span></div>
                        <div class="num {'g-mg' if st in ('fechada', 'atrasada') else 'c-w'}" style="font-size:20px;margin:6px 0">{brl(i["foco_valor"])}</div>
                        <div class="dim" style="font-size:11.5px;margin-bottom:7px">fecha dia {c.dia_fechamento} · vence dia {c.dia_vencimento} · <span class="c-cy">{MODOS[c.modo_fatura]}</span></div>
                        {barra(pct) if c.limite_centavos else ''}
                        <div class="dim" style="font-size:11px;margin-top:4px">{f"limite usado {brl(i['usado'])} de {brl(c.limite_centavos)}" if c.limite_centavos else "sem limite cadastrado"}</div>
                      </div>''')
        detalhe()

    def selecionar(cid):
        estado["id"], estado["fat"] = cid, None
        tudo.refresh()

    @ui.refreshable
    def detalhe():
        hoje = date.today()
        with sessao() as s:
            c = s.get(Conta, estado["id"])
            fats = {f.competencia: f for f in faturas_do_cartao(s, c.id)}
            atual = competencia(vencimento_da_fatura(hoje, c.dia_fechamento, c.dia_vencimento))
            # abas: faturas existentes + a fatura atual + a próxima (para poder informar o total)
            comps = sorted(set(fats) | {somar_meses(atual, -1), atual, somar_meses(atual, 1)})
            # mostra 3 meses para trás e 3 para frente, mais qualquer fatura antiga ainda não paga
            comps = [x for x in comps if somar_meses(atual, -3) <= x <= somar_meses(atual, 3)
                     or (x in fats and not fats[x].paga and x < atual)]
            pend = [k for k in comps if k in fats and not fats[k].paga and k <= atual]
            sel = estado["fat"] if estado["fat"] in comps else (pend[0] if pend else atual)
            f = fats.get(sel)
            res = resumo_fatura(s, f) if f else {"itens": [], "soma_itens": 0, "valor": 0, "ajuste": 0}
            cats = {x.id: x.nome for x in s.exec(select(Categoria))}
            st = status_fatura(f, c, hoje) if f else ("aberta" if sel >= atual else "paga")
            fech, venc = datas_da_fatura(sel, c.dia_fechamento, c.dia_vencimento)
            futuras = sum(resumo_fatura(s, x)["valor"] for k, x in fats.items() if k > sel and not x.paga)
            valores_tabs = {k: resumo_fatura(s, fats[k])["valor"] if k in fats else 0 for k in comps}

        with ui.row().classes("w-full items-center gap-2 mt-6 mb-3"):
            ui.label(f"{c.nome} · faturas").classes("nf-h")
            for k in comps:
                pago = k in fats and fats[k].paga
                ui.html(f'<span>{nome_mes(k)} <span class="mono dim">{brl(valores_tabs[k])}</span>{" ✓" if pago else ""}</span>', sanitize=False) \
                    .classes("fat-tab" + (" on" if k == sel else "")).on("click", lambda k=k: escolher(k))
            ui.button(icon="edit", on_click=lambda: abrir_form(c)).props("flat round dense").classes("btn-ico ml-auto").tooltip("Editar cartão")
            ui.button(icon="archive", on_click=lambda: arquivar(c)).props("flat round dense").classes("btn-ico").tooltip("Arquivar cartão")

        with ui.element("div").classes("w-full").style("display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);gap:14px"):
            with ui.element("div").classes("nf-panel"):
                html(f'''<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
                    <span class="nf-h">Fatura de {nome_mes(sel, longo=True)}</span><span class="pill {STATUS_PILL[st]}">{st}</span>
                    <span class="dim" style="margin-left:auto;font-size:12px">fecha {fech:%d/%m} · vence {venc:%d/%m}</span></div>''')
                if c.modo_fatura == "simples" and not res["itens"]:
                    html('<div class="vazio">Cartão no modo <b class="c-cy">simples</b>: as compras não são lançadas uma a uma.<br>'
                         'Digite ao lado o total da fatura que aparece no app do banco.</div>')
                elif not res["itens"]:
                    html('<div class="vazio">Nenhuma compra nesta fatura ainda.<br>Aperte <kbd>N</kbd> e escolha este cartão em "Pago com".</div>')
                else:
                    with ui.element("div").classes("nf-row head").style("grid-template-columns:62px 1fr 1fr 120px"):
                        for h in ("Compra", "Descrição", "Categoria", "Valor"):
                            ui.label(h).classes("r" if h == "Valor" else "")
                    for l in res["itens"]:
                        parc = f' <span class="pill p-pu">{l.parcela_n}/{l.parcela_total}</span>' if l.parcela_total else ""
                        html(f'''<div class="nf-row" style="grid-template-columns:62px 1fr 1fr 120px">
                            <span class="mono dim">{l.data:%d/%m}</span><span class="c-w">{l.descricao}{parc}</span>
                            <span>{cats[l.categoria_id]}</span><span class="mono r">{brl(l.valor_centavos if l.tipo == "saida" else -l.valor_centavos)}</span></div>''')

            with ui.element("div").classes("nf-panel" + (" mg" if st in ("fechada", "atrasada") else "")):
                ui.label("Valor da fatura").classes("nf-h mb-2")
                ajuste_cls = "c-mg" if res["ajuste"] > 0 else "c-gr"
                html(f'''<div class="mono" style="font-size:13px;line-height:2">
                    Compras e parcelas lançadas <span style="float:right">{brl(res["soma_itens"])}</span><br>
                    Valor real informado <span style="float:right">{brl(f.valor_informado_centavos) if f and f.valor_informado_centavos is not None else "—"}</span><br>
                    {f'Ajuste (encargos/diferença) <span class="{ajuste_cls}" style="float:right">{brl(res["ajuste"], sinal=True)}</span><br>' if res["ajuste"] and res["soma_itens"] else ""}
                    <b class="c-w">Total da fatura</b> <b class="num g-mg" style="float:right;font-size:16px">{brl(res["valor"])}</b><br>
                    <span class="dim">Faturas futuras (parcelas) <span style="float:right">{brl(futuras)}</span></span></div>''')
                rotulo = "Total da fatura (R$)" if c.modo_fatura == "simples" else "Valor real do app do banco (R$)"
                atual_v = f.valor_informado_centavos if f and f.valor_informado_centavos is not None else None
                campo = ui.input(rotulo, value=f"{atual_v / 100:.2f}".replace(".", ",") if atual_v is not None else "",
                                 placeholder="ex.: 1.148,30").props(CAMPO).classes("w-full mt-2")

                def salvar_valor():
                    try:
                        v = parse_valor(campo.value)
                        with sessao() as s:
                            fx = obter_fatura(s, c.id, sel)
                            r2 = informar_valor_fatura(s, fx.id, v)
                    except ValueError as e:
                        ui.notify(str(e), type="negative", position="top")
                        return
                    extra = f" · ajuste {brl(r2['ajuste'], sinal=True)}" if r2["ajuste"] and c.modo_fatura != "simples" else ""
                    ui.notify(f"Fatura {nome_mes(sel)} = {brl(r2['valor'])}{extra}", type="positive", position="bottom-right")
                    tudo.refresh()
                campo.on("keydown.enter", salvar_valor)
                with ui.row().classes("w-full gap-2 mt-2"):
                    ui.button("Salvar valor", on_click=salvar_valor).props("unelevated").classes("btn-gho flex-grow")
                    if f and f.paga:
                        ui.button("Desmarcar paga", on_click=lambda: pagar(f.id, False)).props("unelevated").classes("btn-gho flex-grow")
                    elif res["valor"] > 0:
                        ui.button("✓ Fatura paga", on_click=lambda: pagar(obter_id(sel), True)).props("unelevated").classes("btn-grn flex-grow")
                if c.modo_fatura == "hibrido":
                    html('<div class="dim" style="font-size:11.5px;margin-top:8px">Híbrido: a diferença entre o valor real e o que você lançou vira um ajuste — nada é contado duas vezes.</div>')

    def obter_id(comp):
        with sessao() as s:
            f = obter_fatura(s, estado["id"], comp)
            s.commit()
            return f.id

    def escolher(k):
        estado["fat"] = k
        detalhe.refresh()

    def pagar(fid, paga):
        with sessao() as s:
            marcar_fatura_paga(s, fid, paga)
        ui.notify("Fatura marcada como paga" if paga else "Pagamento desfeito", type="positive" if paga else "warning", position="bottom-right")
        tudo.refresh()

    def arquivar(c):
        def ok():
            with sessao() as s:
                arquivar_cartao(s, c.id)
            ui.notify(f"{c.nome} arquivado", type="warning", position="bottom-right")
            estado["id"] = None
            tudo.refresh()
        confirmar(f"Arquivar {c.nome}?", "O cartão some das telas e de 'Pago com', mas todo o histórico continua salvo.", ok, "Arquivar", perigo=True)

    with moldura("/cartoes"):
        dialogo = DialogoLancamento(ao_salvar=lambda: tudo.refresh())
        atalho_novo(dialogo)
        acoes = cabecalho("Cartões", "Quantos cartões quiser · cada um com seu fechamento, vencimento e modo de fatura")
        with acoes:
            ui.button("+ Cartão", on_click=lambda: abrir_form()).props("unelevated").classes("btn-pri")
        tudo()
