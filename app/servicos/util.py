"""Funções puras de datas e dinheiro."""
import calendar
import re
from datetime import date


def competencia(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def dia_seguro(ano: int, mes: int, dia: int) -> date:
    """Dia 31 em mês de 30 dias vira o último dia do mês."""
    return date(ano, mes, min(dia, calendar.monthrange(ano, mes)[1]))


def somar_meses(comp: str, n: int) -> str:
    ano, mes = map(int, comp.split("-"))
    total = ano * 12 + (mes - 1) + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def meses_entre(inicio: str, fim: str):
    """Gera 'AAAA-MM' de inicio até fim (inclusive)."""
    atual = inicio
    while atual <= fim:
        yield atual
        atual = somar_meses(atual, 1)


def brl(centavos: int, sinal: bool = False) -> str:
    neg = centavos < 0
    reais = f"{abs(centavos) / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if sinal:
        return f"{'-' if neg else '+'} R$ {reais}"
    return f"{'-' if neg else ''}R$ {reais}"


def parse_valor(texto: str | float | int | None) -> int:
    """Converte '1.234,56', '1234.56', 'R$ 38,90' ou 38.9 em centavos. Levanta ValueError se inválido."""
    if texto is None:
        raise ValueError("Informe um valor.")
    if isinstance(texto, (int, float)):
        return round(float(texto) * 100)
    t = re.sub(r"[^\d,.\-]", "", str(texto).strip())
    if not t or t in "-.,":
        raise ValueError("Informe um valor.")
    if "," in t:  # formato brasileiro: ponto = milhar, vírgula = decimal
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1 or (t.count(".") == 1 and len(t.split(".")[1]) == 3):
        t = t.replace(".", "")  # "1.234" = mil duzentos e trinta e quatro
    return round(float(t) * 100)


MESES_PT = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def nome_mes(comp: str, longo: bool = False) -> str:
    ano, mes = map(int, comp.split("-"))
    longos = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto",
              "Setembro", "Outubro", "Novembro", "Dezembro"]
    return f"{longos[mes - 1]}/{ano}" if longo else f"{MESES_PT[mes - 1]}/{str(ano)[2:]}"


MAX_MESES_FILTRO = 36


def periodo_filtrado(de: str, ate: str, meses: str | list[str] | None = None) -> list[str]:
    """Lista de 'AAAA-MM' entre de e ate (inclusive). Se 'meses' vier (lista ou 'AAAA-MM,AAAA-MM'),
    mantém só esses. Valida formato, ordem e o limite de 36 meses."""
    for c in (de, ate):
        if not (isinstance(c, str) and len(c) == 7 and c[4] == "-" and c[:4].isdigit() and c[5:].isdigit()
                and 1 <= int(c[5:]) <= 12):
            raise ValueError("Mês inválido: use AAAA-MM.")
    if ate < de:
        de, ate = ate, de
    todos = list(meses_entre(de, ate))
    if len(todos) > MAX_MESES_FILTRO:
        raise ValueError(f"Escolha no máximo {MAX_MESES_FILTRO} meses.")
    if meses:
        escolhidos = set(meses.split(",") if isinstance(meses, str) else meses)
        filtrados = [c for c in todos if c in escolhidos]
        return filtrados or todos
    return todos
