"""Peças reutilizáveis da interface: moldura com menu, seletor de mês e o diálogo de lançamento."""
from contextlib import contextmanager
from datetime import date

from nicegui import ui
from sqlmodel import select

from app.dados.banco import sessao
from app.dados.modelos import Categoria, Conta, Lancamento
from app.servicos.lancamentos import criar_lancamento, editar_lancamento
from app.servicos.livre import resumo_mes
from app.servicos.util import brl, competencia, nome_mes, parse_valor, somar_meses

NAV = [("Painel", "/", "space_dashboard"), ("Lançamentos", "/lancamentos", "receipt_long"),
       ("Visão anual", "/anual", "calendar_view_month"), ("Cartões", "/cartoes", "credit_card"),
       ("Recorrências", "/recorrencias", "event_repeat"), ("Relatórios", "/relatorios", "insights"),
       ("Configurações", "/config", "tune")]


def fmt_curto(centavos: int | None) -> str:
    """4800,00 → '4.800' (sem centavos, para tabelas largas)."""
    if centavos is None:
        return "—"
    v = round(centavos / 100)
    return ("-" if v < 0 else "") + f"{abs(v):,}".replace(",", ".")


def echart_base(**extra) -> dict:
    """Opções comuns dos gráficos no tema neon."""
    base = {"backgroundColor": "transparent", "textStyle": {"fontFamily": "Inter", "color": "#7C8299"},
            "grid": {"left": 58, "right": 18, "top": 36, "bottom": 30},
            "tooltip": {"trigger": "axis", "backgroundColor": "#14141D", "borderColor": "#00F0FF",
                        "textStyle": {"color": "#F4F6FF", "fontFamily": "Inter", "fontSize": 12}},
            "legend": {"top": 0, "right": 0, "textStyle": {"color": "#C9CEDC"}, "itemWidth": 12, "itemHeight": 8}}
    base.update(extra)
    return base

CAMPO = "outlined dense dark color=cyan"


@contextmanager
def moldura(ativo: str):
    """Menu lateral + área principal. Use: with moldura('/'): ..."""
    with ui.element("div").classes("nf-shell"):
        with ui.element("div").classes("nf-side"):
            ui.html('<div class="nf-logo">NEON<span>//</span>FIN</div>', sanitize=False)
            for nome, rota, icone in NAV:
                with ui.link(target=rota).classes("nf-nav" + (" on" if rota == ativo else "")):
                    ui.icon(icone)
                    ui.label(nome)
            ui.html('<div class="nf-hint"><kbd>N</kbd> novo lançamento<br><kbd>Esc</kbd> fechar janela</div>', sanitize=False)
        with ui.element("div").classes("nf-main"):
            yield


def cabecalho(titulo: str, sub: str = ""):
    with ui.row().classes("w-full items-end no-wrap gap-4 mb-5"):
        with ui.column().classes("gap-1"):
            ui.label(titulo).classes("nf-title")
            if sub:
                ui.label(sub).classes("nf-sub")
        acoes = ui.row().classes("ml-auto items-center gap-2")
    return acoes


def seletor_mes(rota: str, comp: str):
    with ui.row().classes("items-center gap-1"):
        ui.button(icon="chevron_left", on_click=lambda: ui.navigate.to(f"{rota}?mes={somar_meses(comp, -1)}")) \
            .props("flat round dense").classes("btn-ico")
        ui.label(nome_mes(comp, longo=True)).classes("nf-h c-cy").style("min-width:140px;text-align:center")
        ui.button(icon="chevron_right", on_click=lambda: ui.navigate.to(f"{rota}?mes={somar_meses(comp, 1)}")) \
            .props("flat round dense").classes("btn-ico")
        if comp != competencia(date.today()):
            ui.button("Hoje", on_click=lambda: ui.navigate.to(rota)).props("flat dense no-caps").classes("btn-ico")


def barra(pct: float, classe: str | None = None) -> str:
    classe = classe or ("b-mg" if pct >= 1 else "b-ye" if pct >= .8 else "b-cy")
    return f'<div class="bar"><i class="{classe}" style="width:{max(0, min(pct, 1)) * 100:.0f}%"></i></div>'


def html(conteudo: str):
    return ui.html(conteudo, sanitize=False)


def confirmar(titulo: str, texto: str, ao_confirmar, rotulo: str = "Confirmar", perigo: bool = False):
    with ui.dialog() as d, ui.card().classes("nf-modal").style("min-width:420px"):
        ui.label(titulo).classes("nf-title").style("font-size:20px")
        ui.label(texto).classes("dim")
        with ui.row().classes("w-full justify-end gap-2 mt-3"):
            ui.button("Cancelar", on_click=d.close).props("flat no-caps").classes("btn-gho")

            def ok():
                d.close()
                ao_confirmar()
            ui.button(rotulo, on_click=ok).props("unelevated").classes("btn-dan" if perigo else "btn-pri")
    d.open()


# ======================================================================== lançamento
class DialogoLancamento:
    """Janela de novo lançamento / edição. Tecla N abre; Enter salva."""

    def __init__(self, ao_salvar):
        self.ao_salvar = ao_salvar
        self.editando: Lancamento | None = None
        with ui.dialog() as self.dialogo, ui.card().classes("nf-modal").style("width:500px;max-width:95vw"):
            with ui.row().classes("w-full items-center"):
                self.titulo = ui.label("Lançamento rápido").classes("nf-title").style("font-size:21px")
                html("<kbd>N</kbd>").classes("ml-2")
                ui.button(icon="close", on_click=self.dialogo.close).props("flat round dense").classes("btn-ico ml-auto")
            self.tipo = ui.toggle({"saida": "Saída", "entrada": "Entrada"}, value="saida",
                                  on_change=self._mudou_tipo).props("no-caps toggle-color=cyan dark unelevated")
            self.valor = ui.input("Valor (R$)", placeholder="0,00").props(CAMPO + " autofocus").classes("w-full f-valor saida")
            self.valor.on("keydown.enter", self.salvar)
            self.valor.on_value_change(lambda: self._atualizar_impacto())
            with ui.row().classes("w-full no-wrap gap-2"):
                self.descricao = ui.input("Descrição").props(CAMPO).classes("flex-grow")
                self.descricao.on("keydown.enter", self.salvar)
                self.descricao.on("blur", self._sugerir_categoria)
                self.data = ui.input("Data", value=date.today().isoformat()).props(CAMPO + " type=date").style("width:160px")
            with ui.row().classes("w-full no-wrap gap-2"):
                self.categoria = ui.select({}, label="Categoria").props(CAMPO).classes("flex-grow")
                self.conta = ui.select({}, label="Pago com / recebido em", on_change=self._mudou_conta).props(CAMPO).classes("flex-grow")
            with ui.row().classes("w-full no-wrap gap-2 items-center"):
                self.parcelas = ui.number("Parcelas", value=1, min=1, max=48, step=1, format="%d").props(CAMPO).style("width:110px")
                self.info_parcela = ui.label("").classes("dim text-xs")
                self.parcelas.on_value_change(lambda: self._atualizar_impacto())
            self.obs = ui.input("Observação (opcional)").props(CAMPO).classes("w-full")
            self.impacto = html("").classes("w-full")
            with ui.row().classes("w-full items-center mt-1"):
                ui.label("⏎ salvar · Esc fechar").classes("dim text-xs")
                ui.button("Cancelar", on_click=self.dialogo.close).props("flat no-caps").classes("btn-gho ml-auto")
                self.botao = ui.button("Salvar", on_click=self.salvar).props("unelevated").classes("btn-pri")

    # ------------------------------------------------------------------ abrir
    def abrir(self, lanc: Lancamento | None = None):
        self.editando = lanc
        with sessao() as s:
            self._cats = {c.id: c for c in s.exec(select(Categoria).order_by(Categoria.nome))}
            self._contas = {c.id: c for c in s.exec(select(Conta).where(Conta.ativa == True))}  # noqa: E712
            self._resumo = resumo_mes(s, competencia(date.today()), date.today())
        if lanc:
            self.titulo.text = "Editar lançamento"
            self.tipo.value = lanc.tipo
            self.valor.value = f"{lanc.valor_centavos / 100:.2f}".replace(".", ",")
            self.descricao.value = lanc.descricao
            self.data.value = lanc.data.isoformat()
            self._mudou_tipo()
            self.categoria.value = lanc.categoria_id
            self.conta.value = lanc.conta_id
            self.parcelas.value = lanc.parcela_total or 1
            self.obs.value = lanc.observacao
            bloqueado = bool(lanc.compra_id)
            for campo in (self.valor, self.data, self.conta, self.parcelas, self.tipo):
                campo.set_enabled(not bloqueado)
            self.info_parcela.text = f"parcela {lanc.parcela_n}/{lanc.parcela_total} — só descrição e categoria" if bloqueado else ""
        else:
            self.titulo.text = "Lançamento rápido"
            for campo in (self.valor, self.data, self.conta, self.parcelas, self.tipo):
                campo.set_enabled(True)
            self.tipo.value = "saida"
            self.valor.value = ""
            self.descricao.value = ""
            self.obs.value = ""
            self.data.value = date.today().isoformat()
            self.parcelas.value = 1
            self._mudou_tipo()
        self._mudou_conta()
        self.dialogo.open()

    # ------------------------------------------------------------------ reações
    def _mudou_tipo(self, *_):
        t = self.tipo.value
        self.valor.classes(remove="saida entrada", add=t)
        self.categoria.options = {i: c.nome for i, c in self._cats.items() if c.tipo == t}
        if self.categoria.value not in self.categoria.options:
            self.categoria.value = next(iter(self.categoria.options), None)
        self.categoria.update()
        ops = {}
        for i, c in self._contas.items():
            if c.tipo == "cartao":
                if t == "entrada" or c.modo_fatura == "simples":
                    continue  # cartão em modo simples não recebe compras (só o total da fatura)
                ops[i] = f"💳 {c.nome}"
            else:
                ops[i] = c.nome
        self.conta.options = ops
        if self.conta.value not in ops:
            self.conta.value = next(iter(ops), None)
        self.conta.update()
        self._atualizar_impacto()

    def _mudou_conta(self, *_):
        c = self._contas.get(self.conta.value) if hasattr(self, "_contas") else None
        eh_cartao = bool(c and c.tipo == "cartao")
        self.parcelas.set_visibility(eh_cartao)
        if not eh_cartao:
            self.parcelas.value = 1
        self._atualizar_impacto()

    def _sugerir_categoria(self, *_):
        if self.editando or not (self.descricao.value or "").strip():
            return
        with sessao() as s:
            ultimo = s.exec(select(Lancamento).where(Lancamento.descricao.ilike(self.descricao.value.strip()),
                                                     Lancamento.tipo == self.tipo.value)
                            .order_by(Lancamento.id.desc())).first()
        if ultimo and ultimo.categoria_id in self.categoria.options:
            self.categoria.value = ultimo.categoria_id
            ui.notify(f"Categoria sugerida: {self._cats[ultimo.categoria_id].nome}", position="top", timeout=1200)

    def _atualizar_impacto(self):
        if not hasattr(self, "_resumo"):
            return
        try:
            v = parse_valor(self.valor.value)
        except ValueError:
            v = 0
        livre = self._resumo["livre"]
        n = int(self.parcelas.value or 1)
        txt = ""
        if v > 0 and self.tipo.value == "saida" and not self.editando:
            efeito = v // n if n > 1 else v
            txt = (f'Livre para gastar: <span class="mono">{brl(livre)}</span> → '
                   f'<b class="g-cy mono">{brl(livre - efeito)}</b>')
            if n > 1:
                txt += f' <span class="dim">· {n}x de {brl(v // n)} a partir da próxima fatura</span>'
        elif v > 0 and self.tipo.value == "entrada" and not self.editando:
            txt = f'Livre para gastar: <span class="mono">{brl(livre)}</span> → <b class="g-gr mono">{brl(livre + v)}</b>'
        self.impacto.content = f'<div class="nf-impacto">{txt}</div>' if txt else ""

    # ------------------------------------------------------------------ salvar
    def salvar(self, *_):
        try:
            valor = parse_valor(self.valor.value)
            d = date.fromisoformat(self.data.value)
            if self.categoria.value is None or self.conta.value is None:
                raise ValueError("Escolha a categoria e a conta.")
            with sessao() as s:
                if self.editando:
                    campos = {"descricao": (self.descricao.value or "").strip(), "categoria_id": self.categoria.value,
                              "observacao": self.obs.value or ""}
                    if not self.editando.compra_id:
                        campos.update(valor_centavos=valor, data=d, conta_id=self.conta.value, tipo=self.tipo.value)
                    editar_lancamento(s, self.editando.id, **campos)
                    msg = "Lançamento atualizado"
                else:
                    criados = criar_lancamento(s, data=d, descricao=self.descricao.value or "", tipo=self.tipo.value,
                                               valor_centavos=valor, categoria_id=self.categoria.value,
                                               conta_id=self.conta.value, parcelas=int(self.parcelas.value or 1),
                                               observacao=self.obs.value or "")
                    msg = f"Salvo · {criados[0].descricao} · {brl(valor)}" + (f" em {len(criados)}x" if len(criados) > 1 else "")
        except ValueError as e:
            ui.notify(str(e), type="negative", position="top")
            return
        self.dialogo.close()
        ui.notify(msg, type="positive", position="bottom-right", icon="check")
        self.ao_salvar()


def atalho_novo(dialogo: DialogoLancamento):
    """Tecla N abre o lançamento rápido (ignorada enquanto você digita num campo)."""
    def tecla(e):
        if e.action.keydown and not e.action.repeat and str(e.key).lower() == "n" \
                and not (e.modifiers.ctrl or e.modifiers.alt or e.modifiers.meta):
            dialogo.abrir()
    ui.keyboard(on_key=tecla)


# ======================================================================== filtro de meses
def filtro_meses(rota: str, de: str, ate: str, selecionados: list[str], extra: str = ""):
    """Painel de filtro: período De/Até, atalhos e escolha mês a mês (clique liga/desliga)."""
    from app.servicos.util import meses_entre as _entre
    hoje = date.today()
    todos = list(_entre(min(de, ate), max(de, ate)))
    ano = int(de[:4])
    atual = competencia(hoje)

    def ir(d, a, sel=None):
        url = f"{rota}?de={d}&ate={a}"
        if sel is not None and 0 < len(sel) < len(list(_entre(d, a))):
            url += "&meses=" + ",".join(sorted(sel))
        ui.navigate.to(url + extra)

    def alternar(c):
        sel = set(selecionados)
        if c in sel:
            if len(sel) == 1:
                ui.notify("Deixe pelo menos um mês selecionado", type="warning", position="top")
                return
            sel.remove(c)
        else:
            sel.add(c)
        ir(de, ate, sel)

    atalhos = [("Este ano", f"{hoje.year}-01", f"{hoje.year}-12"),
               ("Ano passado", f"{hoje.year - 1}-01", f"{hoje.year - 1}-12"),
               ("Próximos 12 meses", atual, somar_meses(atual, 11)),
               ("Últimos 6 meses", somar_meses(atual, -5), atual),
               (f"1º semestre {ano}", f"{ano}-01", f"{ano}-06"),
               (f"2º semestre {ano}", f"{ano}-07", f"{ano}-12")]
    with ui.element("div").classes("nf-panel w-full").style("padding:12px 16px;margin-bottom:14px"):
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.icon("filter_alt").classes("c-cy")
            ui.label("Período").classes("nf-h")
            c_de = ui.input("De", value=de).props(CAMPO + " type=month").style("width:170px")
            ui.label("até").classes("dim")
            c_ate = ui.input("Até", value=ate).props(CAMPO + " type=month").style("width:170px")
            ui.button("Aplicar", on_click=lambda: ir(c_de.value, c_ate.value)).props("unelevated").classes("btn-gho")
            with ui.row().classes("gap-1 ml-auto"):
                for nome, d, a in atalhos:
                    ativo = (d == de and a == ate and len(selecionados) == len(todos))
                    ui.html(f'<span class="chip{" on" if ativo else ""}">{nome}</span>', sanitize=False) \
                        .on("click", lambda d=d, a=a: ir(d, a))
        with ui.row().classes("w-full items-center gap-1 mt-2"):
            ui.label(f"Meses ({len(selecionados)} de {len(todos)}):").classes("dim text-sm mr-1")
            for c in todos:
                on = c in selecionados
                ui.html(f'<span class="chip{" on" if on else ""}" style="{"" if on else "opacity:.55;text-decoration:line-through"}">{nome_mes(c)}</span>',
                        sanitize=False).on("click", lambda c=c: alternar(c)).tooltip("Clique para mostrar/esconder este mês")
            if len(selecionados) < len(todos):
                ui.button("Todos", on_click=lambda: ir(de, ate)).props("flat dense no-caps").classes("btn-ico ml-1")
