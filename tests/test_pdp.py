"""
A leitura do PDP, testada sem PDP.

Não há credencial aqui e não deve haver: o que se testa é o tradutor — dado o
que o PDP devolve, o que vira marco. As respostas abaixo são cópias reduzidas
das reais, com os mesmos nomes de campo e os mesmos formatos esquisitos
(`display_id` com `#`, data em dd/mm/aaaa, caminho crítico como texto).

O teste que mais importa é o último: quando o PDP muda de formato, esta leitura
tem de **parar**, não devolver meio cronograma. Um cronograma pela metade vira
uma curva de caixa errada que ninguém percebe.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app import pdp
from app.pdp import PDP, PDPIndisponivel


TELA = """
<html><body>
<select id="project-select"><option value="0">Projeto</option>
  <option value="26">Gante</option></select>
<select id="area-select"><option value="0">Área</option>
  <option value="6">Regularização Fundiária</option>
  <option value="9">Legalização Imobiliária</option></select>
<select id="process-select"><option value="0">Processo</option>
  <option value="232">Regularização</option></select>
<select id="phase-select"><option value="0">Fase</option>
  <option value="160">Desenvolvimento de Projeto e Legalização</option></select>
</body></html>
"""

TAREFAS = [
    {"id": 1, "display_id": "#3272", "text": "Matrículas Retificadas",
     "start_date": "03/06/2027", "end_date": "02/09/2027", "duration": 68,
     "real_duration": 66, "progress_percent": 0,
     "is_planned_critical_path": "not-critical-path"},
    {"id": 2, "display_id": "#3273", "text": "Matrículas Unificadas",
     "start_date": "03/09/2027", "end_date": "03/12/2027", "duration": 66,
     "real_duration": 66, "progress_percent": 0,
     "is_planned_critical_path": "planned_critical_path"},
]
LIGACOES = [{"id": 9, "source": 1, "target": 2, "relation_type": "TI", "lag": 0}]


class RespostaFalsa:
    def __init__(self, status=200, texto="", json=None):
        self.status_code = status
        self.text = texto
        self.url = "https://pdp.teste/dashboard"
        self._json = json

    def json(self):
        if self._json is None:
            raise ValueError("não é json")
        return self._json


class SessaoFalsa:
    """Um PDP de mentira: responde a tela, o cronograma e os filtros."""

    def __init__(self, *, tarefas=TAREFAS, quebrado=False):
        self.headers = {}
        self.tarefas = tarefas
        self.quebrado = quebrado
        self.chamadas = []

    def post(self, url, **kw):
        self.chamadas.append(url)
        if url.endswith("/login"):
            return RespostaFalsa(texto="<html>dashboard</html>")
        if self.quebrado:
            return RespostaFalsa(json={"resultado": "ok"})
        area = (kw.get("params") or {}).get("area_id")
        tarefas = self.tarefas
        if area == "6":
            tarefas = [t for t in self.tarefas if t["display_id"] == "#3272"]
        elif area == "9":
            tarefas = [t for t in self.tarefas if t["display_id"] == "#3273"]
        return RespostaFalsa(json={"tasks": tarefas, "links": LIGACOES})

    def get(self, url, **kw):
        self.chamadas.append(url)
        return RespostaFalsa(texto=TELA)

    def close(self):
        pass


@pytest.fixture
def cliente(monkeypatch):
    def montar(**kw):
        c = PDP(url="https://pdp.teste", email="teste@mmi", senha="x")
        c.sessao = SessaoFalsa(**kw)
        return c
    return montar


# ------------------------------------------------------------ conversão
@pytest.mark.parametrize("bruto,esperado", [
    ("11/12/2025", dt.date(2025, 12, 11)),
    ("2025-12-11", dt.date(2025, 12, 11)),
    ("", None), (None, None), ("qualquer coisa", None),
])
def test_a_data_do_pdp_vira_data_ou_nada(bruto, esperado):
    """Data ilegível vira None, não exceção: um marco sem data ainda é um marco."""
    assert pdp._data(bruto) == esperado


@pytest.mark.parametrize("bruto,esperado", [
    ("planned_critical_path", True), ("not-critical-path", False),
    ("", False), (None, False),
])
def test_caminho_critico_vem_como_texto(bruto, esperado):
    assert pdp._critico(bruto) is esperado


# --------------------------------------------------------------- leitura
def test_le_marcos_com_area_datas_e_predecessores(cliente):
    c = cliente()
    c.entrar()
    marcos = {m.pdp_id: m for m in c.marcos(26)}

    assert set(marcos) == {"3272", "3273"}
    m = marcos["3273"]
    assert m.nome == "Matrículas Unificadas"
    assert m.inicio == dt.date(2027, 9, 3) and m.fim == dt.date(2027, 12, 3)
    assert m.duracao == 66                      # real_duration, não duration
    assert m.critico is True
    assert m.area_codigo == "9"
    assert m.predecessores == [("3272", "TI", 0)]
    assert marcos["3272"].predecessores == []


def test_projetos_saem_do_seletor_da_tela(cliente):
    c = cliente()
    c.entrar()
    assert c.projetos() == {"26": "Gante"}


# ------------------------------------------------------------- falhando
def test_sem_credencial_nao_tenta_ler(monkeypatch):
    monkeypatch.delenv("PDP_EMAIL", raising=False)
    monkeypatch.delenv("PDP_SENHA", raising=False)
    with pytest.raises(PDPIndisponivel, match="PDP_EMAIL"):
        PDP(url="https://pdp.teste").entrar()


def test_formato_mudou_e_a_leitura_para(cliente):
    """
    O PDP responde 200 e um JSON qualquer. É o caso perigoso: sem esta trava,
    o sistema gravaria zero marco e diria que deu certo.
    """
    c = cliente(quebrado=True)
    c.entrar()
    with pytest.raises(PDPIndisponivel, match="formato do PDP mudou"):
        c.marcos(26)


def test_projeto_vazio_e_erro_e_nao_silencio(cliente):
    c = cliente(tarefas=[])
    c.entrar()
    with pytest.raises(PDPIndisponivel, match="marco nenhum"):
        c.marcos(26)
