from nicegui import ui
from sqlmodel import select

from app.dados.banco import sessao
from app.dados.modelos import Categoria, Configuracao
from app.servicos.projecao import definir_saldo_inicial, gravar_config, ler_config
from app.servicos.util import brl, nome_mes, parse_valor
from app.ui.componentes import CAMPO, cabecalho, html, moldura

CORES = ["#00F0FF", "#39FF14", "#FF2E88", "#9D4EDD", "#FFE600", "#FF9F1C", "#4DA3FF", "#7C8299"]


@ui.page("/config")
def pagina_config():
    from app.dados import banco
    CAMINHO_DB = banco.caminho_atual

    @ui.refreshable
    def tudo():
        with sessao() as s:
            reserva = int(s.get(Configuracao, "reserva_mensal_centavos").valor)
            cats = list(s.exec(select(Categoria).order_by(Categoria.tipo, Categoria.nome)))
        with ui.element("div").classes("w-full").style("display:grid;grid-template-columns:1fr 1fr;gap:14px"):
            with ui.element("div").classes("nf-panel"):
                ui.label("Reserva mensal para metas").classes("nf-h")
                html(f'<div class="dim" style="font-size:12.5px;margin:4px 0 10px">Valor separado todo mês <b class="c-w">antes</b> de calcular o livre para gastar '
                     f'(ex.: entrada do carro novo + reserva de emergência). Atual: <b class="g-pu">{brl(reserva)}</b></div>')
                campo = ui.input("Valor por mês (R$)", value=f"{reserva / 100:.2f}".replace(".", ",")).props(CAMPO).classes("w-full")

                def salvar():
                    try:
                        v = parse_valor(campo.value)
                        if v < 0:
                            raise ValueError("O valor não pode ser negativo.")
                    except ValueError as e:
                        ui.notify(str(e), type="negative")
                        return
                    with sessao() as s:
                        s.get(Configuracao, "reserva_mensal_centavos").valor = str(v)
                        s.commit()
                    ui.notify(f"Reserva mensal = {brl(v)}", type="positive", position="bottom-right")
                    tudo.refresh()
                ui.button("Salvar", on_click=salvar).props("unelevated").classes("btn-pri mt-2")

            with ui.element("div").classes("nf-panel"):
                with sessao() as s:
                    saldo = int(ler_config(s, "saldo_inicial_centavos"))
                    mes_saldo = ler_config(s, "saldo_inicial_mes")
                    est = int(ler_config(s, "estimativa_padrao_centavos"))
                ui.label("Saldo e estimativas (Visão anual)").classes("nf-h")
                html(f'<div class="dim" style="font-size:12.5px;margin:4px 0 10px">Quanto você tinha na conta no <b class="c-w">início</b> de um mês '
                     f'(antes do salário e das contas daquele mês). A partir dele a Visão anual calcula o saldo previsto. '
                     f'Atual: <b class="g-cy">{brl(saldo)}</b> em {nome_mes(mes_saldo, longo=True)}</div>')
                with ui.row().classes("w-full no-wrap gap-2"):
                    c_saldo = ui.input("Saldo inicial (R$)", value=f"{saldo / 100:.2f}".replace(".", ",")).props(CAMPO).classes("flex-grow")
                    c_mes = ui.input("No início de", value=mes_saldo).props(CAMPO + " type=month").style("width:190px")
                ui.label("Estimativa padrão de gastos variáveis por mês").classes("nf-lbl mt-3")
                html('<div class="dim" style="font-size:12.5px;margin-bottom:6px">Mercado, gasolina, lazer... usada nos meses futuros quando você '
                     'não definiu um valor específico. Deixe 0 para contar só o que for lançado.</div>')
                c_est = ui.input("Estimativa padrão (R$/mês)", value=f"{est / 100:.2f}".replace(".", ",")).props(CAMPO).classes("w-full")

                def salvar_saldo():
                    try:
                        v_saldo = parse_valor(c_saldo.value) if (c_saldo.value or "").strip() not in ("", "-") else 0
                        v_est = parse_valor(c_est.value) if (c_est.value or "").strip() else 0
                        if v_est < 0:
                            raise ValueError("A estimativa não pode ser negativa.")
                        with sessao() as s:
                            definir_saldo_inicial(s, v_saldo, c_mes.value)
                            gravar_config(s, "estimativa_padrao_centavos", v_est)
                    except ValueError as e:
                        ui.notify(str(e), type="negative")
                        return
                    ui.notify(f"Saldo {brl(v_saldo)} em {nome_mes(c_mes.value)} · estimativa {brl(v_est)}/mês", type="positive", position="bottom-right")
                    tudo.refresh()
                ui.button("Salvar", on_click=salvar_saldo).props("unelevated").classes("btn-pri mt-2")

            with ui.element("div").classes("nf-panel"):
                ui.label("Seus dados").classes("nf-h")
                html(f'<div class="dim" style="font-size:12.5px;line-height:1.7">Banco: <span class="mono c-w">{CAMINHO_DB}</span><br>'
                     'Um backup é feito a cada vez que o app abre, na pasta <span class="mono">backups/</span> ao lado do banco '
                     '(guarda os 30 mais recentes). Para restaurar, feche o app e copie um backup por cima de <span class="mono">financas.db</span>.</div>')

            with ui.element("div").classes("nf-panel"):
                ui.label("Categorias").classes("nf-h mb-2")
                for tipo, titulo in (("saida", "Saídas"), ("entrada", "Entradas")):
                    ui.label(titulo).classes("nf-lbl mt-2")
                    html("".join(f'<span class="pill p-dim" style="margin:3px 4px 0 0"><span style="display:inline-block;width:7px;height:7px;border-radius:50%;'
                                 f'background:{c.cor};margin-right:6px"></span>{c.nome}</span>' for c in cats if c.tipo == tipo))
                with ui.row().classes("w-full no-wrap gap-2 mt-4 items-center"):
                    nome = ui.input("Nova categoria").props(CAMPO).classes("flex-grow")
                    tipo = ui.select({"saida": "Saída", "entrada": "Entrada"}, value="saida").props(CAMPO).style("width:120px")

                    def criar():
                        n = (nome.value or "").strip()
                        if not n:
                            ui.notify("Digite o nome da categoria", type="negative")
                            return
                        with sessao() as s:
                            if s.exec(select(Categoria).where(Categoria.nome == n, Categoria.tipo == tipo.value)).first():
                                ui.notify("Essa categoria já existe", type="warning")
                                return
                            s.add(Categoria(nome=n, tipo=tipo.value, cor=CORES[len(cats) % len(CORES)]))
                            s.commit()
                        ui.notify(f"Categoria {n} criada", type="positive", position="bottom-right")
                        tudo.refresh()
                    ui.button("Adicionar", on_click=criar).props("unelevated").classes("btn-gho")

    with moldura("/config"):
        cabecalho("Configurações", "Reserva para metas, categorias e onde seus dados ficam")
        tudo()
